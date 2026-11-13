from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso

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
            puede_crear_pacientes=True, puede_generar_enlace_paciente_nuevo=True,
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
