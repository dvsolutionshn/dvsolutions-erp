from datetime import date, datetime, timedelta
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from PIL import Image
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from facturacion.models import Cliente
from clinica.models import CitaClinica, Paciente, ProfesionalSalud, ServicioClinico

from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, NotificacionCitaWhatsApp, PlantillaMensaje
from .services import subir_media_whatsapp


class CRMTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia",
            slug="hospital_mia",
            rtn="08011999111113",
            estado_licencia="activa",
        )
        self.modulo, _ = Modulo.objects.get_or_create(
            codigo="crm_marketing",
            defaults={"nombre": "CRM, Marketing y Agenda", "es_comercial": True},
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo, activo=True)
        self.modulo_citas, _ = Modulo.objects.get_or_create(
            codigo="agenda_citas",
            defaults={"nombre": "Citas", "es_comercial": True},
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo_citas, activo=True)
        self.rol = RolSistema.objects.create(
            nombre="CRM Total",
            codigo="crm-total",
            puede_crm=True,
            puede_campanias=True,
            puede_citas=True,
            puede_configuracion_crm=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="crmuser",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )

    def test_dashboard_crm_responde_para_empresa_con_modulo(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("crm_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRM y Marketing")

    def test_agenda_citas_responde_como_modulo_separado(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendario de Citas")

    def test_calendario_ofrece_vistas_mes_semana_y_dia(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Paciente Calendario", activo=True)
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            titulo="Evaluación médica",
            fecha_hora=timezone.make_aware(datetime(2026, 6, 22, 10, 30)),
            duracion_minutos=45,
            responsable="Dra. Demo",
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])
        for vista in ["mes", "semana", "dia"]:
            response = self.client.get(url, {"vista": vista, "fecha": "2026-06-22"})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Evaluación médica")
            self.assertContains(response, "Paciente Calendario")
        response = self.client.get(url, {"vista": "mes", "fecha": "2026-06-22", "editar": cita.id})
        self.assertContains(response, "Editando cita")
        self.assertContains(response, "45")

    def test_cita_puede_editarse_y_cambiar_estado_desde_calendario(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Paciente Estado", activo=True)
        cita = CitaCliente.objects.create(
            empresa=self.empresa, cliente=cliente, titulo="Consulta inicial",
            fecha_hora=timezone.make_aware(datetime(2026, 6, 23, 9, 0)),
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])
        response = self.client.post(url, {
            "cita_id": cita.id, "cliente": cliente.id, "producto": "", "titulo": "Consulta actualizada",
            "fecha_hora": "2026-06-23T09:30", "duracion_minutos": "90",
            "responsable": "Dr. Responsable", "estado": "confirmada", "observacion": "Control",
        })
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(cita.titulo, "Consulta actualizada")
        self.assertEqual(cita.duracion_minutos, 90)
        estado_url = reverse("agenda_cita_estado", args=[self.empresa.slug, cita.id])
        response = self.client.post(estado_url, {"estado": "realizada", "vista": "dia", "fecha": "2026-06-23"})
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, "realizada")

    def test_agenda_clinica_usa_paciente_tipo_consulta_y_doctor(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "Clínica Médica", "es_comercial": True}
        )
        EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo_clinica, defaults={"activo": True})
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-001", nombre="Paciente Clínico")
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa, nombre="Consulta de cardiología", categoria="consulta", duracion_minutos=45
        )
        doctor = ProfesionalSalud.objects.create(
            empresa=self.empresa, nombre="Dr. Carlos Demo", especialidad="Cardiología"
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])

        response = self.client.get(url)
        self.assertContains(response, "Tipo de consulta")
        self.assertContains(response, "Doctor / profesional")
        self.assertContains(response, "Dr. Carlos Demo")
        self.assertNotContains(response, "<label for=\"id_titulo\">", html=False)
        self.assertNotContains(response, "id_duracion_minutos")

        response = self.client.post(url, {
            "paciente": paciente.id, "servicio_clinico": servicio.id,
            "profesional_salud": doctor.id, "fecha_hora": "2026-06-24T11:00",
            "duracion_minutos": "45", "estado": "confirmada", "observacion": "Primera valoración",
        })
        self.assertEqual(response.status_code, 302)
        cita = CitaCliente.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertEqual(cita.titulo, "Consulta de cardiología")
        self.assertEqual(cita.responsable, "Dr. Carlos Demo")
        self.assertEqual(cita.profesional_salud, doctor)
        self.assertEqual(cita.duracion_minutos, servicio.duracion_minutos)
        cita_clinica = CitaClinica.objects.get(id=cita.cita_clinica_id)
        self.assertEqual(cita_clinica.paciente, paciente)
        self.assertEqual(cita_clinica.profesional, doctor)
        self.assertEqual(cita_clinica.servicio, servicio)
        self.assertEqual(cita_clinica.estado, "confirmada")

    @patch("crm.appointment_notifications.enviar_plantilla_cita_whatsapp")
    def test_hospital_mia_programa_y_envia_recordatorios_sin_duplicar(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.cita"}]}
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "Clínica Médica", "es_comercial": True}
        )
        EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo_clinica, defaults={"activo": True})
        paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="EXP-WA", nombre="Paciente WhatsApp", whatsapp="99990000"
        )
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta general", duracion_minutos=30)
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. WhatsApp")
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.recordatorio_citas_activo = True
        config.whatsapp_phone_number_id = "phone-id"
        config.whatsapp_token = "token"
        config.whatsapp_plantilla_cita = "recordatorio_cita"
        config.save()
        fecha = timezone.localtime(timezone.now() + timedelta(days=10)).replace(second=0, microsecond=0)
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("agenda_citas", args=[self.empresa.slug]), {
            "paciente": paciente.id, "servicio_clinico": servicio.id, "profesional_salud": doctor.id,
            "fecha_hora": fecha.strftime("%Y-%m-%dT%H:%M"), "duracion_minutos": "30",
            "estado": "confirmada", "observacion": "Avisar automáticamente",
            "enviar_confirmacion_whatsapp": "on", "recordatorio_semana_whatsapp": "on",
            "recordatorio_dia_whatsapp": "on",
        })
        self.assertEqual(response.status_code, 302)
        cita = CitaCliente.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertEqual(cita.notificaciones_whatsapp.count(), 3)
        confirmacion = cita.notificaciones_whatsapp.get(tipo="confirmacion")
        self.assertEqual(confirmacion.estado, "enviado")
        semana = cita.notificaciones_whatsapp.get(tipo="semana")
        momento_semana = cita.fecha_hora - timedelta(days=7) + timedelta(minutes=1)
        with patch("crm.appointment_notifications.timezone.now", return_value=momento_semana):
            call_command("procesar_recordatorios_citas")
        semana.refresh_from_db()
        self.assertEqual(semana.estado, "enviado")
        with patch("crm.appointment_notifications.timezone.now", return_value=momento_semana):
            call_command("procesar_recordatorios_citas")
        self.assertEqual(mock_enviar.call_count, 2)

    def test_preparar_envios_de_campania_crea_whatsapp_por_cliente(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Demo",
            telefono_whatsapp="99999999",
            fecha_nacimiento=date(1990, 4, 18),
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Test",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Hola {{cliente}}, promocion especial de {{empresa}}.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Abril",
            plantilla=plantilla,
            audiencia="promociones",
            fecha_programada=timezone.now(),
        )

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_preparar_envios_campania", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertIn("Paciente Demo", envio.mensaje)
        self.assertIn("50499999999", envio.whatsapp_url)

    @patch("crm.views.enviar_plantilla_marketing_whatsapp")
    def test_enviar_campania_por_api_actualiza_envios(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.test"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente API",
            telefono_whatsapp="99999998",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo API",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Hola {{cliente}}, tenemos una promocion.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania API",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Hola Paciente API, tenemos una promocion.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_whatsapp_api", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertEqual(envio.estado, "enviado")
        mock_enviar.assert_called_once()

    @patch("crm.views.enviar_plantilla_whatsapp")
    def test_enviar_campania_prueba_masiva_usa_hello_world(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.hello"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Demo",
            telefono_whatsapp="99999996",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Interna",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Mensaje interno.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Demo",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Mensaje interno.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.whatsapp_plantilla_prueba = "hello_world"
        config.whatsapp_idioma_plantilla = "en_US"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_plantilla_prueba", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertEqual(envio.estado, "enviado")
        mock_enviar.assert_called_once_with(config, "99999996", nombre_plantilla="hello_world", idioma="en_US")

    @patch("crm.views.enviar_plantilla_marketing_whatsapp")
    @patch("crm.views.subir_media_whatsapp")
    def test_enviar_campania_por_api_usa_imagen_si_existe(self, mock_subir, mock_enviar_plantilla):
        mock_subir.return_value = "media-test"
        mock_enviar_plantilla.return_value = {"messages": [{"id": "wamid.image"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Imagen",
            telefono_whatsapp="99999997",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Imagen",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Promocion con imagen.",
            imagen_promocional="crm/promociones/demo.png",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Imagen",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Promocion con imagen.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_whatsapp_api", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        mock_subir.assert_called_once()
        mock_enviar_plantilla.assert_called_once()
        self.assertEqual(mock_enviar_plantilla.call_args.kwargs["media_id"], "media-test")

    @patch("crm.services._post_multipart")
    def test_subir_media_acepta_ruta_de_imagefield(self, mock_post):
        mock_post.return_value = {"id": "media-test"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporal:
            temporal.write(b"\x89PNG\r\n\x1a\n")
            ruta_imagen = Path(temporal.name)
        fake_file = SimpleNamespace(path=str(ruta_imagen))
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_phone_number_id = "123"

        try:
            media_id = subir_media_whatsapp(config, fake_file)
        finally:
            ruta_imagen.unlink(missing_ok=True)

        self.assertEqual(media_id, "media-test")
        mock_post.assert_called_once()

    @patch("crm.services._post_multipart")
    def test_subir_media_optimiza_imagen_grande(self, mock_post):
        mock_post.return_value = {"id": "media-optimizada"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporal:
            ruta_imagen = Path(temporal.name)
        try:
            imagen = Image.frombytes("RGB", (2200, 2200), os.urandom(2200 * 2200 * 3))
            imagen.save(ruta_imagen, format="PNG")
            fake_file = SimpleNamespace(path=str(ruta_imagen))
            config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
            config.whatsapp_phone_number_id = "123"

            media_id = subir_media_whatsapp(config, fake_file)

            self.assertEqual(media_id, "media-optimizada")
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs, {})
            args = mock_post.call_args.args
            self.assertEqual(args[5], "image/jpeg")
            self.assertTrue(Path(args[4]).name.endswith(".jpg"))
        finally:
            ruta_imagen.unlink(missing_ok=True)
