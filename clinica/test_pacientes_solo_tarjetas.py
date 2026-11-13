from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM

from .models import Paciente


class PacientesSoloTarjetasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        cls.empresas = {}
        cls.pacientes = {}
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"QA-{slug}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            cls.empresas[slug] = empresa
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente QA {slug}", identidad="0801199300123",
                telefono="22212222", whatsapp="99990000", celular_2="99990001", correo="paciente@example.test",
                fecha_nacimiento=date(1993, 2, 3), sexo="femenino",
                foto_perfil="clinica/pacientes/perfil/foto-qa.jpg",
                expediente_codigo=f"EXP-{slug}", alergias="Alergias privadas QA", rh="AB+",
            )
        cls.rol = RolSistema.objects.create(
            nombre="Recepción de tarjetas QA", codigo="recepcion-tarjetas-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
            puede_crear_pacientes=True,
            puede_ver_historia_clinica=True, puede_subir_anexos_clinicos=True,
            solo_tarjetas_pacientes=True,
        )
        cls.usuario = Usuario.objects.create_user(username="recepcion-tarjetas-qa", empresa=cls.empresas["hospital_mia"])
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        for empresa in cls.empresas.values():
            UsuarioEmpresaPermiso.objects.create(usuario=cls.usuario, empresa=empresa, rol_sistema=cls.rol)

    def test_busca_por_identidad_formateada_y_solo_renderiza_tarjetas(self):
        self.client.force_login(self.usuario)
        for slug, paciente in self.pacientes.items():
            for app in (False, True):
                with self.subTest(slug=slug, app=app):
                    consulta = {"q": "0801-1993-00123", "destino": "crear_anexo"}
                    if app:
                        consulta["app"] = "1"
                    respuesta = self.client.get(reverse("clinica_pacientes", args=[slug]), consulta)
                    for dato in (
                        paciente.nombre, paciente.identidad, paciente.telefono, paciente.whatsapp,
                        paciente.celular_2, paciente.correo, paciente.foto_perfil.url,
                    ):
                        self.assertContains(respuesta, dato)
                    for dato in (paciente.expediente_codigo, paciente.alergias, paciente.rh, "Enlace para paciente nuevo"):
                        self.assertNotContains(respuesta, dato)
                    for vista in ("clinica_paciente_detalle", "clinica_paciente_expediente", "clinica_subir_examen_paciente"):
                        self.assertNotContains(respuesta, reverse(vista, args=[slug, paciente.pk]))
                    self.assertIn("no-store", respuesta.headers["Cache-Control"])

    def test_sugerencias_no_incluyen_destino_ni_datos_clinicos(self):
        self.client.force_login(self.usuario)
        empresa = self.empresas["hospital_mia"]
        respuesta = self.client.get(
            reverse("clinica_pacientes_sugerencias", args=[empresa.slug]), {"q": "0801-1993-00123", "app": "1"},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {"results": [{
            "id": self.pacientes[empresa.slug].pk,
            "nombre": self.pacientes[empresa.slug].nombre,
            "documento": "0801199300123",
            "telefono": "99990000",
            "correo": "paciente@example.test",
        }]})
        self.assertIn("no-store", respuesta.headers["Cache-Control"])

    def test_rutas_de_ficha_expediente_y_escritura_responden_con_denegacion(self):
        self.client.force_login(self.usuario)
        slug = "hospital_mia"
        paciente = self.pacientes[slug]
        for nombre, args in (
            ("clinica_paciente_detalle", [slug, paciente.pk]),
            ("clinica_paciente_expediente", [slug, paciente.pk]),
            ("clinica_subir_examen_paciente", [slug, paciente.pk]),
        ):
            with self.subTest(vista=nombre):
                url = reverse(nombre, args=args)
                self.assertContains(self.client.get(url, {"app": "1"}), "No tienes permiso para entrar aquí", status_code=403)
                self.assertContains(self.client.post(url), "No tienes permiso para entrar aquí", status_code=403)

    def test_tarjetas_permiten_compartir_ambos_formularios_en_web_y_app(self):
        RolSistema.objects.filter(pk=self.rol.pk).update(
            puede_generar_enlace_paciente_nuevo=True,
            puede_enviar_enlace_historia_clinica=True,
        )
        self.client.force_login(self.usuario)
        for slug, paciente in self.pacientes.items():
            listado = reverse("clinica_pacientes", args=[slug])
            enlace_nuevo = reverse("clinica_generar_enlace_registro_paciente", args=[slug])
            enlace_historia = reverse("clinica_generar_enlace_preconsulta", args=[slug, paciente.pk])
            for app in ({}, {"app": "1"}):
                with self.subTest(clinica=slug, app=bool(app)):
                    tarjetas = self.client.get(listado, app)
                    plantilla = "clinica/mobile_patient_cards.html" if app else "clinica/pacientes.html"
                    self.assertTemplateUsed(tarjetas, plantilla)
                    self.assertContains(tarjetas, "Enlace para paciente nuevo")
                    self.assertContains(tarjetas, f'action="{enlace_nuevo}"')
                    self.assertContains(tarjetas, "Enviar historia clínica")
                    self.assertContains(tarjetas, f'action="{enlace_historia}"')
                    for vista in ("clinica_paciente_detalle", "clinica_paciente_expediente"):
                        destino = reverse(vista, args=[slug, paciente.pk])
                        self.assertNotContains(tarjetas, f'href="{destino}')
                        self.assertContains(
                            self.client.get(destino, app), "No tienes permiso para entrar aquí", status_code=403,
                        )

                    volver = f"{listado}?app=1" if app else listado
                    historia = self.client.post(enlace_historia, app)
                    self.assertContains(historia, "Compartir por WhatsApp")
                    self.assertEqual(historia.context["volver_url"], volver)
                    self.assertEqual(historia.context["preconsulta"].paciente, paciente)
                    self.assertTrue(historia.context["preconsulta"].sin_datos_previos)
                    self.assertEqual(historia.context["preconsulta"].datos_generales, {})
                    formulario = Client().get(urlsplit(historia.context["enlace_publico"]).path)
                    self.assertContains(formulario, 'name="nombres"')
                    for dato in (paciente.identidad, paciente.correo, paciente.expediente_codigo, paciente.alergias):
                        self.assertNotContains(formulario, dato)

                    nuevo = self.client.post(enlace_nuevo, app)
                    self.assertContains(nuevo, "Compartir por WhatsApp")
                    self.assertEqual(nuevo.context["volver_url"], volver)
                    self.assertEqual(nuevo.context["invitacion"].empresa, paciente.empresa)

    def test_cada_enlace_requiere_su_propio_permiso(self):
        slug = "hospital_mia"
        paciente = self.pacientes[slug]
        listado = reverse("clinica_pacientes", args=[slug])
        enlace_nuevo = reverse("clinica_generar_enlace_registro_paciente", args=[slug])
        enlace_historia = reverse("clinica_generar_enlace_preconsulta", args=[slug, paciente.pk])
        rol = RolSistema.objects.filter(pk=self.rol.pk)
        self.client.force_login(self.usuario)

        rol.update(puede_generar_enlace_paciente_nuevo=True, puede_enviar_enlace_historia_clinica=False)
        tarjetas = self.client.get(listado)
        self.assertContains(tarjetas, f'action="{enlace_nuevo}"')
        self.assertNotContains(tarjetas, f'action="{enlace_historia}"')
        self.assertEqual(self.client.post(enlace_historia).status_code, 403)

        rol.update(puede_generar_enlace_paciente_nuevo=False, puede_enviar_enlace_historia_clinica=True)
        tarjetas = self.client.get(listado)
        self.assertContains(tarjetas, f'action="{enlace_historia}"')
        self.assertNotContains(tarjetas, f'action="{enlace_nuevo}"')
        self.assertEqual(self.client.post(enlace_nuevo).status_code, 403)

        especializado = reverse("clinica_generar_enlace_preconsulta_tipo", args=[slug, paciente.pk, "enfermeria"])
        paciente_ajeno = self.pacientes["medical_spa"]
        ajeno = reverse("clinica_generar_enlace_preconsulta", args=[slug, paciente_ajeno.pk])
        self.assertEqual(self.client.get(enlace_historia).status_code, 403)
        self.assertEqual(self.client.post(especializado).status_code, 403)
        self.assertEqual(self.client.post(ajeno).status_code, 404)

    @patch("clinica.views.enviar_plantilla_preconsulta_whatsapp")
    def test_envio_directo_solo_acepta_el_formulario_en_blanco(self, enviar):
        slug = "hospital_mia"
        paciente = self.pacientes[slug]
        ConfiguracionCRM.objects.create(empresa=paciente.empresa, whatsapp_activo=True)
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_enviar_enlace_historia_clinica=True)
        self.client.force_login(self.usuario)

        generar = reverse("clinica_generar_enlace_preconsulta", args=[slug, paciente.pk])
        historia = self.client.post(generar)
        self.assertEqual(historia.status_code, 200)
        formulario = historia.context["preconsulta"]
        envio = reverse("clinica_enviar_preconsulta_whatsapp", args=[slug, paciente.pk, formulario.pk])
        datos = {"enlace_publico": historia.context["enlace_publico"], "app": "1"}

        self.assertEqual(self.client.post(envio, datos).status_code, 200)
        enviar.assert_called_once()
        formulario.sin_datos_previos = False
        formulario.save(update_fields=["sin_datos_previos"])
        self.assertEqual(self.client.post(envio, datos).status_code, 403)
        enviar.assert_called_once()
