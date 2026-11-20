from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.models import Paciente, ProfesionalSalud, ServicioClinico
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario

from .models import CitaCliente


class RecepcionMotivoCitaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rol = RolSistema.objects.create(
            codigo="agenda-motivo-qa", nombre="Permisos operativos de agenda QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            solo_tarjetas_pacientes=True, puede_ver_pacientes=True,
            puede_ver_calendario=True, puede_crear_citas=True, puede_editar_citas=True,
        )
        modulos = [
            Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})[0]
            for codigo in ("agenda_citas", "clinica_medica", "crm_marketing")
        ]
        cls.perfiles = []
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"MOTIVO-{slug}", tipo_solucion="clinica")
            for modulo in modulos:
                EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            usuario = Usuario.objects.create_user(username=f"motivo-{slug}", empresa=empresa, rol_sistema=cls.rol)
            paciente = Paciente.objects.create(empresa=empresa, nombre=f"Paciente motivo QA {slug}")
            profesional = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional motivo QA {slug}")
            servicio = ServicioClinico.objects.create(empresa=empresa, nombre="Consulta de valoración QA", duracion_minutos=30)
            cls.perfiles.append((empresa, usuario, paciente, profesional, servicio))

    @patch("crm.views._programar_whatsapp_cita")
    def test_crear_editar_reabrir_y_conservar_motivo_en_las_cuatro_clinicas(self, _programar_whatsapp):
        for empresa, usuario, paciente, profesional, servicio in self.perfiles:
            self.client.force_login(usuario)
            for dias, vista in enumerate(("agenda_citas", "agenda_mobile"), start=1):
                with self.subTest(empresa=empresa.slug, vista=vista):
                    url = reverse(vista, args=[empresa.slug])
                    fecha = (timezone.localdate() + timedelta(days=dias)).isoformat()
                    formulario = self.client.get(url, {"fecha": fecha})
                    self.assertContains(formulario, '<textarea name="observacion"')
                    self.assertEqual(formulario.context["form"].fields["observacion"].label, "Motivo de consulta")
                    datos = {
                        "paciente": paciente.pk, "profesional_salud": profesional.pk,
                        "servicio_clinico": servicio.pk, "fecha_cita": fecha,
                        "hora_cita": "10:00", "periodo_cita": "AM", "estado": "pendiente",
                        "observacion": "Paciente solicita valoración inicial",
                    }
                    self.assertEqual(self.client.post(url, datos).status_code, 302)
                    cita = CitaCliente.objects.get(empresa=empresa, paciente=paciente, observacion=datos["observacion"])
                    edicion = self.client.get(url, {"fecha": fecha, "editar": cita.pk})
                    self.assertEqual(edicion.context["form"]["observacion"].value(), datos["observacion"])
                    self.assertContains(edicion, datos["observacion"])
                    self.assertEqual(edicion.context["form"].fields["observacion"].label, "Motivo de consulta")
                    self.assertNotIn("cirugia_detalle", edicion.context["form"].fields)
                    actualizado = {**datos, "cita_id": cita.pk, "observacion": "Motivo corregido por Recepción", "hora_cita": "11:00"}
                    self.assertEqual(self.client.post(url, actualizado).status_code, 302)
                    cita.refresh_from_db()
                    self.assertEqual(cita.observacion, actualizado["observacion"])
                    self.assertEqual(cita.cita_clinica.observaciones, actualizado["observacion"])
                    reabierta = self.client.get(url, {"fecha": fecha, "editar": cita.pk})
                    self.assertEqual(reabierta.context["form"]["observacion"].value(), actualizado["observacion"])
                    self.assertContains(reabierta, actualizado["observacion"])

                    solo_horario = {**actualizado, "hora_cita": "12:00", "periodo_cita": "PM"}
                    solo_horario.pop("observacion")
                    self.assertEqual(self.client.post(url, solo_horario).status_code, 302)
                    cita.refresh_from_db()
                    self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 12)
                    self.assertEqual(cita.observacion, actualizado["observacion"])
                    self.assertEqual(cita.cita_clinica.observaciones, actualizado["observacion"])

                    self.assertEqual(self.client.post(url, {**solo_horario, "observacion": ""}).status_code, 302)
                    cita.refresh_from_db()
                    self.assertFalse(cita.observacion)
                    vacia = self.client.get(url, {"fecha": fecha, "editar": cita.pk})
                    self.assertFalse(vacia.context["form"]["observacion"].value())

    def test_cita_anterior_sin_motivo_muestra_campo_vacio(self):
        for empresa, usuario, paciente, profesional, servicio in self.perfiles:
            self.client.force_login(usuario)
            cita = CitaCliente.objects.create(
                empresa=empresa, paciente=paciente, profesional_salud=profesional,
                servicio_clinico=servicio, fecha_hora=timezone.now(), observacion=None,
            )
            for vista in ("agenda_citas", "agenda_mobile"):
                with self.subTest(empresa=empresa.slug, vista=vista):
                    edicion = self.client.get(reverse(vista, args=[empresa.slug]), {"editar": cita.pk})
                    self.assertContains(edicion, '<textarea name="observacion"')
                    self.assertFalse(edicion.context["form"]["observacion"].value())

