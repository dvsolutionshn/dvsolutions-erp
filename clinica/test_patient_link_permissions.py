from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM

from .models import InvitacionRegistroPaciente, Paciente, PreconsultaClinica


class PermisosEnlacesPacientesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas = {}
        cls.pacientes = {}
        for numero, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(
                nombre=f"Clínica de prueba {numero}", slug=slug, rtn=f"ENLACES-QA-{numero}", tipo_solucion="clinica",
            )
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            cls.empresas[slug] = empresa
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente de prueba {numero}", expediente_codigo=f"EXP-RESERVADO-{numero}",
                identidad=f"080119990002{numero}", correo=f"reservado{numero}@example.test",
                telefono=f"5049000000{numero}", whatsapp=f"5049100000{numero}",
                sexo="femenino", alergias="Alergia privada de prueba", medicamentos_actuales="Medicación privada de prueba",
            )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes["hospital_mia"]
        cls.roles = {}
        for nombre, datos_generales in (("enfermeria", False), ("recepcion", True)):
            cls.roles[nombre] = RolSistema.objects.create(
                nombre=nombre, codigo=f"prueba-enlaces-{nombre}", es_rol_clinico=True,
                usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
                usa_permisos_acciones_v3=True, puede_ver_pacientes=True,
                puede_ver_datos_generales_paciente=datos_generales,
                puede_enviar_enlace_historia_clinica=True, puede_generar_enlace_paciente_nuevo=True,
            )
        cls.usuario = Usuario.objects.create_user(username="prueba-enlaces", empresa=cls.empresa, rol_sistema=cls.roles["enfermeria"])
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        for slug, empresa in cls.empresas.items():
            rol = cls.roles["recepcion" if slug in {"medical_spa", "serviciosmedicos"} else "enfermeria"]
            UsuarioEmpresaPermiso.objects.create(usuario=cls.usuario, empresa=empresa, rol_sistema=rol)
            PreconsultaClinica.objects.create(
                empresa=empresa, paciente=cls.pacientes[slug], tipo="general", estado="completada",
                token_hash=f"prueba-historia-previa-{slug}", fecha_expiracion=timezone.now() + timedelta(days=1),
                motivo_consulta="Historia previa privada",
                datos_generales={"formulario_general": {"procedimientos_interes_otros": "Procedimiento privado"}},
            )

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, vista, *args, slug="hospital_mia", app=False):
        return reverse(vista, args=[slug, *args]) + ("?app=1" if app else "")

    def assert_denegado(self, respuesta):
        self.assertIn(respuesta.status_code, (302, 403, 404))

    def test_compartir_ambos_enlaces_desde_web_y_app_en_las_cuatro_clinicas(self):
        for slug, empresa in self.empresas.items():
            paciente = self.pacientes[slug]
            acceso_limitado = slug not in {"medical_spa", "serviciosmedicos"}
            expediente = "clinica_paciente_expediente" if acceso_limitado else "clinica_paciente_detalle"
            for app in (False, True):
                with self.subTest(clinica=slug, app=app):
                    listado = self.client.get(self.url("clinica_pacientes", slug=slug, app=app))
                    self.assertContains(listado, "Enlace para paciente nuevo")
                    self.assertNotContains(listado, self.url("clinica_crear_paciente", slug=slug, app=app))
                    respuesta = self.client.get(self.url(expediente, paciente.pk, slug=slug, app=app))
                    self.assertContains(respuesta, "Enviar historia clínica")

                    historia = self.client.post(self.url("clinica_generar_enlace_preconsulta", paciente.pk, slug=slug, app=app))
                    self.assertContains(historia, "Compartir por WhatsApp")
                    self.assertIn("no-store", historia["Cache-Control"])
                    self.assertTrue(historia.context["preconsulta"].sin_datos_previos)
                    self.assertEqual(historia.context["preconsulta"].datos_generales, {})
                    self.assertEqual(historia.context["preconsulta"].creada_por, self.usuario)
                    self.assertEqual(historia.context["preconsulta"].paciente, paciente)
                    self.assertNotContains(historia, paciente.expediente_codigo)
                    self.assertNotContains(historia, paciente.whatsapp)

                    publica = Client().get(urlsplit(historia.context["enlace_publico"]).path)
                    self.assertContains(publica, 'name="nombres"')
                    for dato in (
                        paciente.identidad, paciente.correo, paciente.expediente_codigo, paciente.whatsapp,
                        paciente.alergias, paciente.medicamentos_actuales, "Procedimiento privado",
                    ):
                        self.assertNotContains(publica, dato)
                    self.assertIn("no-store", publica["Cache-Control"])

                    nuevo = self.client.post(self.url("clinica_generar_enlace_registro_paciente", slug=slug, app=app))
                    self.assertContains(nuevo, "Compartir por WhatsApp")
                    self.assertIn("no-store", nuevo["Cache-Control"])
                    self.assertEqual(nuevo.context["invitacion"].empresa, empresa)
                    self.assertEqual(nuevo.context["invitacion"].creada_por, self.usuario)
                    self.assertContains(Client().get(urlsplit(nuevo.context["enlace_publico"]).path), 'name="nombres"')

            self.assert_denegado(self.client.get(self.url("clinica_crear_paciente", slug=slug)))
            self.assert_denegado(self.client.get(self.url("clinica_editar_paciente", paciente.pk, slug=slug)))
            self.assert_denegado(self.client.get(self.url("clinica_historial_clinico_consolidado", paciente.pk, slug=slug)))
            if acceso_limitado:
                self.assert_denegado(self.client.get(self.url("clinica_paciente_detalle", paciente.pk, slug=slug)))

    def test_los_permisos_son_independientes_y_no_habilitan_otro_paciente_o_formulario(self):
        historia = self.url("clinica_generar_enlace_preconsulta", self.paciente.pk)
        nuevo = self.url("clinica_generar_enlace_registro_paciente")
        rol = RolSistema.objects.filter(pk=self.roles["enfermeria"].pk)
        rol.update(puede_enviar_enlace_historia_clinica=False)
        self.assert_denegado(self.client.post(historia))
        self.assertEqual(self.client.post(nuevo, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 200)

        rol.update(puede_enviar_enlace_historia_clinica=True, puede_generar_enlace_paciente_nuevo=False)
        self.assert_denegado(self.client.post(nuevo))
        self.assertEqual(self.client.post(historia).status_code, 200)
        paciente_ajeno = self.pacientes["medical_spa"]
        self.assert_denegado(self.client.post(self.url("clinica_generar_enlace_preconsulta", paciente_ajeno.pk)))
        especializado = self.url("clinica_generar_enlace_preconsulta_tipo", self.paciente.pk, "enfermeria")
        self.assert_denegado(self.client.post(especializado))
        self.assertEqual(PreconsultaClinica.objects.filter(estado="pendiente").count(), 1)
        self.assertEqual(InvitacionRegistroPaciente.objects.count(), 1)

    def test_las_respuestas_nuevas_se_guardan_sin_sobrescribir_datos_del_paciente(self):
        generado = self.client.post(self.url("clinica_generar_enlace_preconsulta", self.paciente.pk))
        enlace = urlsplit(generado.context["enlace_publico"]).path
        anterior = (self.paciente.nombre, self.paciente.whatsapp, self.paciente.alergias)
        respuestas = {
            "nombres": "Nombre actualizado", "apellidos": "Apellido actualizado",
            "identidad": self.paciente.identidad, "fecha_nacimiento": "1990-01-15",
            "sexo": "femenino", "estado_civil": "soltero", "telefono": "99991111",
            "telefono_codigo_area": "504", "informante": "yo_mismo", "referido_por": "no_aplica",
            "diagnostico_medico": "no", "alergias_respuesta": "si", "alergias_detalle": "Alergia reportada",
            "medicamentos_respuesta": "no", "consentimiento_datos": "on",
            **{f"funcion_{campo}": "normal" for campo in ("apetito", "sueno", "sed", "miccion", "evacuaciones")},
        }
        publico = Client()
        rechazo = publico.post(enlace, {**respuestas, "identidad": "0801199900099"})
        self.assertIn("identidad", rechazo.context["form"].errors)
        preconsulta = generado.context["preconsulta"]
        preconsulta.refresh_from_db()
        self.assertEqual(preconsulta.estado, "pendiente")

        respuesta = publico.post(enlace, respuestas)
        formulario = respuesta.context.get("form")
        self.assertContains(respuesta, "Información recibida", msg_prefix=str(formulario.errors) if formulario else "")
        preconsulta.refresh_from_db()
        self.paciente.refresh_from_db()
        self.assertEqual(preconsulta.estado, "completada")
        self.assertEqual(preconsulta.alergias, "Alergia reportada")
        self.assertEqual(preconsulta.datos_generales["nombres"], "Nombre actualizado")
        self.assertEqual((self.paciente.nombre, self.paciente.whatsapp, self.paciente.alergias), anterior)

    @patch("clinica.views.enviar_plantilla_preconsulta_whatsapp")
    def test_envio_directo_verifica_token_y_vigencia_del_formulario(self, enviar):
        ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        historia = self.url("clinica_generar_enlace_preconsulta", self.paciente.pk)
        previo = self.client.post(historia)
        generado = self.client.post(historia)
        preconsulta = generado.context["preconsulta"]
        enlace = generado.context["enlace_publico"]
        envio = self.url("clinica_enviar_preconsulta_whatsapp", self.paciente.pk, preconsulta.pk)

        self.assert_denegado(self.client.post(envio, {"enlace_publico": previo.context["enlace_publico"]}))
        self.assert_denegado(self.client.post(envio, {"enlace_publico": "https://example.test/ruta-ajena"}))
        enviar.assert_not_called()
        self.assertEqual(self.client.post(envio, {"enlace_publico": enlace}).status_code, 200)
        enviar.assert_called_once()
        self.assertEqual(enviar.call_args.kwargs["enlace"], enlace)
        self.assertEqual(enviar.call_args.args[1], self.paciente.whatsapp)

        PreconsultaClinica.objects.filter(pk=preconsulta.pk).update(estado="revocada")
        self.assert_denegado(self.client.post(envio, {"enlace_publico": enlace}))
        self.assertEqual(enviar.call_count, 1)
