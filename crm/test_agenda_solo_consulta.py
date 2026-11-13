from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.models import Paciente
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario

from .models import CitaCirugiaFoto, CitaCliente


class AgendaSoloConsultaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Clínica QA", slug="hospital_mia", rtn="AGENDA-QA", tipo_solucion="clinica",
        )
        for codigo in ("agenda_citas", "clinica_medica", "crm_marketing"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        cls.rol = RolSistema.objects.create(
            codigo="consulta-agenda-qa", nombre="Consulta Agenda QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_calendario=True, puede_ver_pacientes=True,
            puede_escribir_enfermeria=True, puede_escribir_camara_hiperbarica=True,
        )
        cls.usuario = Usuario.objects.create_user(
            username="consulta-agenda-qa", empresa=cls.empresa, rol_sistema=cls.rol,
        )
        paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Paciente Agenda QA", sexo="femenino")
        cls.cita = CitaCliente.objects.create(
            empresa=cls.empresa, paciente=paciente, titulo="Cita de control QA",
            fecha_hora=timezone.now(), observacion="Nota de cita reservada QA",
            cirugia_detalle="Detalle de cirugía reservado QA",
        )
        cls.foto = CitaCirugiaFoto.objects.create(
            cita=cls.cita, empresa=cls.empresa, imagen="crm/citas/cirugias/foto-reservada-qa.jpg",
        )

    def url(self, vista, *args):
        return reverse(vista, args=[self.empresa.slug, *args])

    def test_agenda_de_consulta_muestra_cita_sin_editor_ni_datos_clinicos(self):
        self.client.force_login(self.usuario)
        for vista in ("agenda_citas", "agenda_mobile"):
            with self.subTest(vista=vista):
                respuesta = self.client.get(self.url(vista), {
                    "fecha": timezone.localdate(self.cita.fecha_hora).isoformat(), "vista": "dia",
                    "editar": self.cita.pk,
                })
                self.assertContains(respuesta, self.cita.paciente.nombre)
                self.assertTrue(respuesta.context["agenda_solo_consulta"])
                self.assertIsNone(respuesta.context["cita_editando"])
                for valor in (self.cita.observacion, self.cita.cirugia_detalle, self.foto.imagen.url):
                    self.assertNotContains(respuesta, valor)
                for accion in (
                    "agenda_cita_estado", "agenda_cita_eliminar",
                    "agenda_cita_cancelar_whatsapp", "agenda_cita_reagendar_whatsapp",
                ):
                    self.assertNotContains(respuesta, self.url(accion, self.cita.pk))
                self.assertNotContains(respuesta, 'name="cita_id"')

    def test_solicitudes_directas_no_pueden_modificar_una_cita(self):
        self.client.force_login(self.usuario)
        for vista, args in (
            ("agenda_citas", ()), ("agenda_mobile", ()),
            ("agenda_cita_estado", (self.cita.pk,)), ("agenda_cita_eliminar", (self.cita.pk,)),
            ("agenda_cita_cancelar_whatsapp", (self.cita.pk,)), ("agenda_cita_reagendar_whatsapp", (self.cita.pk,)),
        ):
            with self.subTest(vista=vista):
                datos = {"estado": "realizada", "motivo_eliminacion": "Motivo de prueba"}
                for formulario in (datos, {**datos, "cita_id": self.cita.pk}):
                    respuesta = self.client.post(self.url(vista, *args), formulario)
                    self.assertIn(respuesta.status_code, (302, 403))
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, "pendiente")

    def test_agenda_con_permisos_de_gestion_conserva_editor(self):
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_crear_citas=True, puede_editar_citas=True)
        self.client.force_login(self.usuario)
        for vista in ("agenda_citas", "agenda_mobile"):
            with self.subTest(vista=vista):
                respuesta = self.client.get(self.url(vista), {"editar": self.cita.pk})
                self.assertContains(respuesta, 'name="cita_id"')
                self.assertFalse(respuesta.context["agenda_solo_consulta"])
