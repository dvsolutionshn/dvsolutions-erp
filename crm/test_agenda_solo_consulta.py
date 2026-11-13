from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.models import CitaClinica, Paciente, ProfesionalSalud, ServicioClinico
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

    def test_recepcion_puede_gestionar_sin_ver_ni_sobrescribir_notas_clinicas(self):
        RolSistema.objects.filter(pk=self.rol.pk).update(
            solo_tarjetas_pacientes=True, puede_crear_citas=True, puede_editar_citas=True,
            puede_reagendar_citas=True, puede_cancelar_citas=True,
        )
        self.client.force_login(self.usuario)
        fecha = timezone.localdate(self.cita.fecha_hora).isoformat()
        for vista in ("agenda_citas", "agenda_mobile"):
            with self.subTest(vista=vista):
                respuesta = self.client.get(self.url(vista), {"vista": "dia", "fecha": fecha, "editar": self.cita.pk})
                self.assertContains(respuesta, 'name="cita_id"')
                self.assertContains(respuesta, self.url("agenda_cita_reagendar_whatsapp", self.cita.pk))
                self.assertContains(respuesta, self.url("agenda_cita_cancelar_whatsapp", self.cita.pk))
                self.assertNotIn("observacion", respuesta.context["form"].fields)
                self.assertNotIn('<textarea name="observacion"', respuesta.content.decode())
                for privado in (self.cita.observacion, self.cita.cirugia_detalle, self.foto.imagen.url):
                    self.assertNotContains(respuesta, privado)
                self.assertNotContains(respuesta, self.url("agenda_cita_eliminar", self.cita.pk))
        respuesta = self.client.post(self.url("agenda_cita_eliminar", self.cita.pk), {"motivo_eliminacion": "Prueba"})
        self.assertIn(respuesta.status_code, (302, 403))
        self.cita.refresh_from_db()

    @patch("crm.views._programar_whatsapp_cita")
    def test_recepcion_puede_escribir_motivo_al_crear_cita_en_web_y_app(self, _programar_whatsapp):
        RolSistema.objects.filter(pk=self.rol.pk).update(solo_tarjetas_pacientes=True, puede_crear_citas=True)
        consulta = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta operativa QA")
        cirugia = ServicioClinico.objects.create(empresa=self.empresa, nombre="Cirugía QA", categoria="cirugia")
        profesional = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Profesional QA")
        self.client.force_login(self.usuario)
        for indice, vista in enumerate(("agenda_citas", "agenda_mobile")):
            for dias, servicio in enumerate((consulta, cirugia), start=1 + indice * 2):
                with self.subTest(vista=vista, servicio=servicio.nombre):
                    fecha = (timezone.localdate() + timedelta(days=dias)).isoformat()
                    url = self.url(vista)
                    formulario = self.client.get(url, {"fecha": fecha})
                    self.assertContains(formulario, "Motivo de consulta")
                    self.assertContains(formulario, '<textarea name="observacion"')
                    self.assertContains(formulario, 'data-field-name="observacion" data-appointment-create-reason')
                    self.assertNotContains(formulario, 'name="cirugia_detalle"')
                    self.assertNotContains(formulario, 'name="fotos_cirugia"')
                    motivo = f"Paciente solicita cita ({vista}, {servicio.nombre})"
                    datos = {
                        "paciente": self.cita.paciente_id, "servicio_clinico": servicio.pk,
                        "profesional_salud": profesional.pk, "fecha_cita": fecha,
                        "hora_cita": "10:00", "periodo_cita": "AM", "observacion": motivo,
                        "cirugia_detalle": "Detalle clínico que Recepción no puede escribir",
                    }
                    if servicio == cirugia:
                        datos.update(cirugia_hora_fin="11:00", cirugia_periodo_fin="AM")
                    respuesta = self.client.post(url, datos)
                    self.assertEqual(respuesta.status_code, 302)
                    cita = CitaCliente.objects.get(empresa=self.empresa, paciente=self.cita.paciente, observacion=motivo)
                    self.assertFalse(cita.cirugia_detalle)
                    self.assertEqual(cita.cita_clinica.observaciones, motivo)
                    self.assertEqual(cita.cita_clinica.motivo, servicio.nombre)
                    agenda = self.client.get(url, {"vista": "dia", "fecha": fecha})
                    self.assertContains(agenda, self.cita.paciente.nombre)
                    self.assertNotContains(agenda, motivo)

    @patch("crm.views._enviar_aviso_cita_whatsapp")
    def test_recepcion_reagenda_sin_sobrescribir_motivo_y_notas(self, _enviar_aviso):
        RolSistema.objects.filter(pk=self.rol.pk).update(solo_tarjetas_pacientes=True, puede_reagendar_citas=True)
        self.client.force_login(self.usuario)
        notas_anteriores = self.cita.observacion
        inyectada = "Modificación no autorizada de la observación QA"
        fecha = (timezone.localdate() + timedelta(days=2)).isoformat()
        respuesta = self.client.post(self.url("agenda_cita_reagendar_whatsapp", self.cita.pk), {
            "nueva_fecha_hora": f"{fecha}T10:00", "observacion": inyectada, "return_to": "mobile",
        })
        self.assertEqual(respuesta.status_code, 302)
        self.cita.refresh_from_db()
        self.assertEqual(timezone.localtime(self.cita.fecha_hora).strftime("%Y-%m-%dT%H:%M"), f"{fecha}T10:00")
        self.assertEqual(self.cita.observacion.splitlines()[0], notas_anteriores)
        self.assertNotIn(inyectada, self.cita.observacion)
        self.assertIn(notas_anteriores, self.cita.cita_clinica.observaciones)

    def test_editar_requiere_su_permiso_y_conserva_notas_en_web_y_app(self):
        RolSistema.objects.filter(pk=self.rol.pk).update(solo_tarjetas_pacientes=True, puede_crear_citas=True)
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta operativa QA")
        profesional = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Profesional QA")
        datos = {
            "cita_id": self.cita.pk, "paciente": self.cita.paciente_id, "servicio_clinico": servicio.pk,
            "profesional_salud": profesional.pk, "fecha_hora": "2030-01-15T10:00",
            "observacion": "Dato no autorizado", "cirugia_detalle": "Dato no autorizado",
        }
        self.client.force_login(self.usuario)
        url = self.url("agenda_mobile")
        denegada = self.client.post(url, datos)
        self.assertIn(denegada.status_code, (302, 403))
        self.cita.refresh_from_db()
        self.assertIsNone(self.cita.servicio_clinico_id)

        RolSistema.objects.filter(pk=self.rol.pk).update(puede_crear_citas=False, puede_editar_citas=True)
        for dia, vista in enumerate(("agenda_citas", "agenda_mobile"), start=15):
            with self.subTest(vista=vista):
                nueva_fecha = f"2030-01-{dia}T10:00"
                guardada = self.client.post(self.url(vista), {**datos, "fecha_hora": nueva_fecha})
                self.assertEqual(guardada.status_code, 302)
                self.cita.refresh_from_db()
                self.assertEqual(self.cita.servicio_clinico_id, servicio.pk)
                self.assertEqual(timezone.localtime(self.cita.fecha_hora).strftime("%Y-%m-%dT%H:%M"), nueva_fecha)
                self.assertEqual(self.cita.observacion, "Nota de cita reservada QA")
                self.assertEqual(self.cita.cirugia_detalle, "Detalle de cirugía reservado QA")
                self.assertTrue(CitaClinica.objects.filter(pk=self.cita.cita_clinica_id).exists())
