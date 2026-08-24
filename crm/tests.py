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

from .forms import CitaClienteForm
from .models import (
    CampaniaMarketing,
    CitaCliente,
    ConfiguracionCRM,
    EnvioCampania,
    NotificacionCitaWhatsApp,
    NotificacionCumpleanosWhatsApp,
    OpcionServicioAgenda,
    PlantillaMensaje,
    ProgramaCamaraHiperbarica,
    ProgramaTerapiaPostQuirurgica,
    SesionCamaraHiperbarica,
    SesionTerapiaPostQuirurgica,
)
from .services import enviar_plantilla_cita_whatsapp, subir_media_whatsapp
from .tokens import generar_token_respuesta_cita


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

    def test_empresa_clinica_mantiene_separadas_las_interfaces_web_y_app(self):
        response_login = self.client.post(
            reverse("empresa_login", args=[self.empresa.slug]),
            {"username": "crmuser", "password": "pass12345"},
        )
        self.assertRedirects(
            response_login,
            reverse("agenda_citas", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )

        response_dashboard = self.client.get(reverse("dashboard", args=[self.empresa.slug]))
        self.assertEqual(response_dashboard.status_code, 200)
        self.assertTemplateUsed(response_dashboard, "core/dashboard_premium.html")
        self.assertNotContains(response_dashboard, 'class="mobile-home mobile-app-screen active"')

        response_app = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertEqual(response_app.status_code, 200)
        self.assertTemplateUsed(response_app, "crm/agenda_mobile.html")
        self.assertContains(response_app, 'class="mobile-home mobile-app-screen active"')

    def test_luque_sin_modulo_citas_programa_directamente_en_hospital_mia(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        luque = Empresa.objects.create(
            nombre="Luque Aestetic",
            slug="luque_aestetic",
            rtn="08011999111991",
            estado_licencia="activa",
        )
        self.usuario.empresas_acceso.add(luque)
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-APP-01",
            identidad="0801199912345",
            nombre="Paciente Agenda Central",
        )
        Paciente.objects.create(
            empresa=luque,
            expediente_codigo="LQ-APP-01",
            identidad=paciente.identidad,
            nombre=paciente.nombre,
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Consulta central",
            categoria="consulta",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dra. Agenda Central",
        )
        self.client.login(username="crmuser", password="pass12345")

        app_response = self.client.get(reverse("agenda_mobile", args=[luque.slug]))
        self.assertEqual(app_response.status_code, 200)
        self.assertFalse(luque.tiene_modulo_activo("agenda_citas"))
        self.assertContains(app_response, "Las citas se programan directamente en Hospital Mia")
        self.assertEqual(app_response.context["pacientes_app_payload"][0]["agenda_paciente_id"], paciente.id)

        search_response = self.client.get(
            reverse("agenda_buscar_pacientes", args=[luque.slug]),
            {"q": "Agenda Central"},
        )
        self.assertEqual(search_response.status_code, 200)
        self.assertEqual(search_response.json()["results"][0]["id"], paciente.id)

        response = self.client.post(
            reverse("agenda_mobile", args=[luque.slug]),
            {
                "paciente": paciente.id,
                "servicio_clinico": servicio.id,
                "profesional_salud": profesional.id,
                "fecha_cita": "2026-09-20",
                "hora_cita": "10:00",
                "periodo_cita": "AM",
                "estado": "pendiente",
                "observacion": "Creada desde Luque Aestetic",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(CitaCliente.objects.filter(empresa=self.empresa, paciente=paciente).exists())
        self.assertFalse(CitaCliente.objects.filter(empresa=luque).exists())

    def test_configuracion_crm_muestra_panel_premium_de_automatizaciones(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("crm_configuracion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Centro de automatizaciones CRM")
        self.assertContains(response, "Mensajes automaticos de citas")
        self.assertContains(response, "Plantillas aprobadas en Meta")

    @patch("crm.services._post_whatsapp")
    def test_plantilla_cita_usa_texto_editable_de_configuracion(self, mock_post):
        mock_post.return_value = {"messages": [{"id": "wamid.cita"}]}
        config = SimpleNamespace(
            whatsapp_api_version="v25.0",
            whatsapp_phone_number_id="123",
            whatsapp_token="token-test",
            whatsapp_plantilla_cita="recordatorio_cita",
            whatsapp_idioma_cita="es",
            mensaje_cita_recordatorio_1_dia="le escribimos de Hospital MIA para recordarle su cita de manana",
        )

        enviar_plantilla_cita_whatsapp(
            config,
            "99990000",
            paciente="Paciente Demo",
            aviso="recordatorio: su cita es manana",
            fecha="12/07/2026",
            hora="01:00 AM",
            consulta="Hidrofacial",
            profesional="Dr. Candy Luque",
        )

        payload = mock_post.call_args.args[1]
        parametros = payload["template"]["components"][0]["parameters"]
        self.assertEqual(parametros[1]["text"], "le escribimos de Hospital MIA para recordarle su cita de manana")
        self.assertEqual(parametros[2]["text"], "12/07/2026")
        self.assertEqual(parametros[3]["text"], "01:00 AM")
        self.assertEqual(parametros[4]["text"], "Hidrofacial")
        self.assertEqual(parametros[5]["text"], "Dr. Candy Luque")

    def test_agenda_citas_responde_como_modulo_separado(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendario de Citas")
        self.assertContains(response, reverse("agenda_mobile", args=[self.empresa.slug]))

    def test_hospital_mia_crea_varias_sesiones_con_hora_individual(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-92001",
            identidad="08011999009201",
            nombre="Paciente Terapias Multiples",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Lic. Terapias",
            especialidad="Terapias",
        )
        form_inicial = CitaClienteForm(empresa=self.empresa)
        servicio = ServicioClinico.objects.get(empresa=self.empresa, nombre="Terapias")
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(
            reverse("agenda_citas", args=[self.empresa.slug]),
            {
                "paciente": paciente.id,
                "servicio_clinico": servicio.id,
                "profesional_salud": profesional.id,
                "fecha_cita": "2026-09-10",
                "hora_cita": "08:00",
                "periodo_cita": "AM",
                "detalles_agenda": '[{"clave":"terapia-1-1","fase":1,"sesion":1,"hora":"08:00","periodo":"AM"},{"clave":"terapia-1-2","fase":1,"sesion":2,"hora":"09:00","periodo":"AM"}]',
                "estado": "pendiente",
            },
        )
        self.assertEqual(response.status_code, 302, getattr(response.context.get("form"), "errors", None) if response.context else None)
        citas = CitaCliente.objects.filter(empresa=self.empresa, paciente=paciente).order_by("fecha_hora")
        self.assertEqual(citas.count(), 2)
        self.assertEqual(list(citas.values_list("sesion_servicio", flat=True)), [1, 2])
        self.assertEqual(citas.first().grupo_atencion, citas.last().grupo_atencion)
        self.assertNotEqual(citas.first().fecha_hora, citas.last().fecha_hora)

    def test_hidrofacial_pasa_de_tipo_consulta_a_opcion_tratamiento(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        hidro = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Hidrofacial",
            categoria="tratamiento",
            activo=True,
        )
        CitaClienteForm(empresa=self.empresa)
        hidro.refresh_from_db()
        self.assertFalse(hidro.activo)
        self.assertTrue(OpcionServicioAgenda.objects.filter(
            empresa=self.empresa, categoria="tratamientos", nombre="Hidrofacial", activo=True
        ).exists())

    def test_hospital_mia_guarda_varios_servicios_spa_con_horas_separadas(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-92002",
            identidad="08011999009202",
            nombre="Paciente Spa Multiple",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Profesional Spa",
            especialidad="Spa",
        )
        CitaClienteForm(empresa=self.empresa)
        servicio = ServicioClinico.objects.get(empresa=self.empresa, nombre="Spa")
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(
            reverse("agenda_citas", args=[self.empresa.slug]),
            {
                "paciente": paciente.id,
                "servicio_clinico": servicio.id,
                "profesional_salud": profesional.id,
                "fecha_cita": "2026-09-11",
                "hora_cita": "10:00",
                "periodo_cita": "AM",
                "detalles_agenda": '[{"clave":"spa-masaje","opcion_id":"masaje","opcion_nombre":"Masaje","hora":"10:00","periodo":"AM"},{"clave":"spa-sauna","opcion_id":"sauna","opcion_nombre":"Sauna","hora":"11:00","periodo":"AM"}]',
                "estado": "pendiente",
            },
        )
        self.assertEqual(response.status_code, 302, getattr(response.context.get("form"), "errors", None) if response.context else None)
        self.assertEqual(
            list(CitaCliente.objects.filter(empresa=self.empresa, paciente=paciente).order_by("fecha_hora").values_list("opcion_servicio", flat=True)),
            ["Masaje", "Sauna"],
        )

    def test_hospital_mia_progreso_marca_sesiones_realizadas(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-92003",
            identidad="08011999009203",
            nombre="Paciente Progreso",
        )
        CitaClienteForm(empresa=self.empresa)
        servicio = ServicioClinico.objects.get(empresa=self.empresa, nombre="Camara hiperbarica")
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            titulo="Camara hiperbarica - Sesion 4",
            fecha_hora=timezone.make_aware(datetime(2026, 9, 12, 8, 0)),
            estado="realizada",
            sesion_servicio=4,
        )
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(
            reverse("agenda_progreso_servicios", args=[self.empresa.slug]),
            {"paciente": paciente.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["camara"], [4])

    def test_agenda_hospital_mia_permite_crear_tipo_consulta_sin_salir(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+ Agregar tipo de consulta")
        self.assertContains(
            response,
            reverse("agenda_crear_tipo_consulta_rapido", args=[self.empresa.slug]),
        )

    def test_crear_tipo_consulta_rapido_lo_guarda_activo_y_lo_reutiliza(self):
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_crear_tipo_consulta_rapido", args=[self.empresa.slug])
        datos = {
            "nombre": "Valoracion vascular",
            "categoria": "consulta",
            "duracion_minutos": "45",
            "color_calendario": "#E67E22",
        }

        response = self.client.post(url, datos)
        self.assertEqual(response.status_code, 200)
        servicio = ServicioClinico.objects.get(empresa=self.empresa, nombre="Valoracion vascular")
        self.assertTrue(servicio.activo)
        self.assertEqual(servicio.categoria, "consulta")
        self.assertEqual(servicio.duracion_minutos, 45)
        self.assertEqual(servicio.color_calendario, "#E67E22")
        self.assertTrue(response.json()["creado"])

        servicio.activo = False
        servicio.save(update_fields=["activo"])
        response = self.client.post(
            url,
            {**datos, "duracion_minutos": "60", "color_calendario": "#5B4BDB"},
        )
        self.assertEqual(response.status_code, 200)
        servicio.refresh_from_db()
        self.assertTrue(servicio.activo)
        self.assertEqual(servicio.duracion_minutos, 60)
        self.assertEqual(servicio.color_calendario, "#5B4BDB")
        self.assertFalse(response.json()["creado"])

    def test_crear_tipo_consulta_rapido_rechaza_color_invalido(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(
            reverse("agenda_crear_tipo_consulta_rapido", args=[self.empresa.slug]),
            {
                "nombre": "Consulta sin color valido",
                "categoria": "consulta",
                "duracion_minutos": "30",
                "color_calendario": "rojo",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("color_calendario", response.json()["errors"])

    def test_agenda_citas_muestra_historial_por_paciente(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-91001",
            identidad="08011999000991",
            nombre="Paciente Historial Agenda",
            telefono="99990091",
        )
        otro_paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-91002",
            identidad="08011999000992",
            nombre="Paciente Que No Debe Salir",
            telefono="99990092",
        )
        consulta = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Consulta General",
            categoria="consulta",
            duracion_minutos=60,
        )
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=consulta,
            titulo="Consulta futura de prueba",
            responsable="Dra. Candy Luque",
            fecha_hora=timezone.now() + timedelta(days=5),
            estado="confirmada",
        )
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=consulta,
            titulo="Consulta pasada de prueba",
            responsable="Dra. Candy Luque",
            fecha_hora=timezone.now() - timedelta(days=5),
            estado="finalizada",
        )
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=otro_paciente,
            servicio_clinico=consulta,
            titulo="Consulta de otro paciente",
            responsable="Dra. Candy Luque",
            fecha_hora=timezone.now() + timedelta(days=5),
            estado="confirmada",
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(
            reverse("agenda_citas", args=[self.empresa.slug]),
            {"paciente_historial": str(paciente.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historial de citas por paciente")
        self.assertContains(response, "Paciente Historial Agenda")
        self.assertContains(response, "Próximas citas")
        self.assertContains(response, "Citas anteriores")
        self.assertContains(response, "Consulta futura de prueba")
        self.assertContains(response, "Consulta pasada de prueba")
        self.assertNotContains(response, "Consulta de otro paciente")

    def test_serviciosmedicos_ve_agenda_espejo_de_hospital_mia_solo_dr_luis(self):
        servicios = Empresa.objects.create(
            nombre="Servicios Medicos Gonzalez",
            slug="serviciosmedicos",
            rtn="08011999111114",
            tipo_solucion="clinica",
            estado_licencia="activa",
        )
        EmpresaModulo.objects.create(empresa=servicios, modulo=self.modulo_citas, activo=True)
        self.usuario.empresas_acceso.add(servicios)
        consulta = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Consulta General",
            categoria="consulta",
            duracion_minutos=60,
        )
        dr_luis = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dr. Luis González",
            especialidad="Cirugía",
        )
        dra_candy = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dra. Candy Luque",
            especialidad="Medicina estética",
        )
        paciente_luis = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-90001",
            identidad="08011999000111",
            nombre="Paciente de Luis",
        )
        paciente_candy = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-90002",
            identidad="08011999000112",
            nombre="Paciente de Candy",
        )
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente_luis,
            servicio_clinico=consulta,
            profesional_salud=dr_luis,
            titulo="Consulta General",
            responsable=dr_luis.nombre,
            fecha_hora=timezone.make_aware(datetime(2026, 7, 22, 10, 0)),
            estado="confirmada",
        )
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente_candy,
            servicio_clinico=consulta,
            profesional_salud=dra_candy,
            titulo="Consulta General",
            responsable=dra_candy.nombre,
            fecha_hora=timezone.make_aware(datetime(2026, 7, 22, 10, 0)),
            estado="confirmada",
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(
            reverse("agenda_citas", args=[servicios.slug]),
            {"vista": "dia", "fecha": "2026-07-22"},
        )
        mobile = self.client.get(
            reverse("agenda_mobile", args=[servicios.slug]),
            {"vista": "dia", "fecha": "2026-07-22"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vista espejo")
        self.assertContains(response, "Paciente de Luis")
        self.assertNotContains(response, "Paciente de Candy")
        self.assertContains(response, "Dr. Luis González")
        self.assertEqual(mobile.status_code, 200)
        self.assertContains(mobile, "Paciente de Luis")
        self.assertNotContains(mobile, "Paciente de Candy")

    def test_app_movil_agenda_es_instalable_y_usa_los_mismos_datos(self):
        medical_spa = Empresa.objects.create(
            nombre="Mia Medical spa",
            slug="medical_spa",
            rtn="08011999111115",
            estado_licencia="activa",
        )
        self.usuario.empresas_acceso.add(medical_spa)
        empresas_clinicas = [self.empresa, medical_spa]
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente App",
            rtn="08011999000001",
            telefono="99990001",
            activo=True,
        )
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            titulo="Consulta desde app",
            fecha_hora=timezone.make_aware(datetime(2026, 6, 30, 10, 0)),
            responsable="Dra. Candy",
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(
            reverse("agenda_mobile", args=[self.empresa.slug]),
            {"fecha": "2026-06-30"},
        )
        manifest = self.client.get(reverse("agenda_mobile_manifest", args=[self.empresa.slug]))
        service_worker = self.client.get(reverse("agenda_mobile_service_worker", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paciente App")
        self.assertContains(response, "Consulta desde app")
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, "serviceWorker.register")
        self.assertContains(response, "Instalar en Android")
        self.assertContains(response, "iPhone / iPad")
        self.assertContains(response, "Pacientes")
        self.assertContains(response, "Facturaci")
        self.assertContains(response, "Calendario de citas")
        self.assertContains(response, "mobile-view-switch")
        self.assertContains(response, "data-day-strip-swipe")
        self.assertContains(response, "vista=semana")
        self.assertContains(response, "vista=mes")
        self.assertContains(response, "vista=anio")
        self.assertContains(response, "Cambiar empresa para operar y facturar")
        self.assertContains(response, reverse("agenda_mobile", args=[medical_spa.slug]))
        self.assertContains(response, '<article class="patients-premium-shell">')
        self.assertContains(response, '<div class="premium-invoice-shell">')
        self.assertContains(response, "premium-calendar-shell")
        self.assertContains(response, f"Caja móvil · {self.empresa.nombre}")
        self.assertContains(response, "vista=agenda")
        self.assertContains(response, "vista=proximas")
        self.assertContains(response, "app=agenda")
        self.assertContains(response, "#agenda-app")
        self.assertContains(response, "Todos los estados")
        self.assertContains(response, '<section class="app-panel mobile-app-screen" id="patient-profile-app">')
        self.assertContains(response, 'data-patient-filter="favoritos"')
        self.assertContains(response, "Signos vitales")
        self.assertContains(response, "Crear producto sin salir de la factura")
        self.assertContains(response, "Comentario para esta línea de la factura")
        self.assertContains(response, "data-send-invoice-whatsapp")
        medical_response = self.client.get(
            reverse("agenda_mobile", args=[medical_spa.slug]),
            {"fecha": "2026-06-30"},
        )
        medical_manifest = self.client.get(reverse("agenda_mobile_manifest", args=[medical_spa.slug]))
        medical_service_worker = self.client.get(reverse("agenda_mobile_service_worker", args=[medical_spa.slug]))
        self.assertEqual(medical_response.status_code, 200)
        self.assertEqual(medical_manifest.status_code, 200)
        self.assertEqual(medical_service_worker.status_code, 200)
        self.assertEqual(medical_response.context["agenda_empresa"], self.empresa)
        self.assertContains(medical_response, '<article class="patients-premium-shell">')
        self.assertContains(medical_response, '<div class="premium-invoice-shell">')
        self.assertContains(medical_response, "premium-calendar-shell")
        self.assertContains(medical_response, f"Caja móvil · {medical_spa.nombre}")
        self.assertContains(medical_response, reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertContains(medical_response, "data-company-app-switch")
        for indice, (slug, nombre) in enumerate(
            [
                ("luque_aestetic", "Luque Aestetic"),
                ("serviciosmedicos", "Servicios Médicos"),
            ],
            start=1,
        ):
            empresa_clinica = Empresa.objects.create(
                nombre=nombre,
                slug=slug,
                rtn=f"080119991112{indice}",
                estado_licencia="activa",
            )
            self.usuario.empresas_acceso.add(empresa_clinica)
            empresas_clinicas.append(empresa_clinica)
            empresa_response = self.client.get(reverse("agenda_mobile", args=[empresa_clinica.slug]))
            with self.subTest(empresa=slug):
                self.assertEqual(empresa_response.status_code, 200)
                self.assertEqual(empresa_response.context["agenda_empresa"], self.empresa)
                self.assertContains(empresa_response, '<article class="patients-premium-shell">')
                self.assertContains(empresa_response, '<div class="premium-invoice-shell">')
                self.assertContains(empresa_response, "premium-calendar-shell")
                self.assertContains(empresa_response, f"Caja móvil · {nombre}")
        for empresa_clinica in empresas_clinicas:
            with self.subTest(interfaz_web=empresa_clinica.slug):
                dashboard_response = self.client.get(reverse("dashboard", args=[empresa_clinica.slug]))
                self.assertEqual(dashboard_response.status_code, 200)
                self.assertTemplateUsed(dashboard_response, "core/dashboard_premium.html")
                self.assertNotContains(dashboard_response, 'class="mobile-home mobile-app-screen active"')
        vista_agenda = self.client.get(
            reverse("agenda_mobile", args=[self.empresa.slug]),
            {"vista": "agenda", "fecha": "2026-06-30"},
        )
        self.assertEqual(vista_agenda.status_code, 200)
        self.assertContains(vista_agenda, "Agenda cronológica")
        vista_anual = self.client.get(
            reverse("agenda_mobile", args=[self.empresa.slug]),
            {"vista": "anio", "fecha": "2026-06-30"},
        )
        self.assertContains(vista_anual, "Resumen anual")
        self.assertContains(vista_anual, "junio")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["display"], "standalone")
        self.assertEqual(
            {icon["sizes"] for icon in manifest.json()["icons"]},
            {"192x192", "512x512"},
        )
        self.assertEqual(
            manifest.json()["start_url"],
            reverse("agenda_mobile", args=[self.empresa.slug]),
        )
        self.assertEqual(service_worker.status_code, 200)
        self.assertContains(service_worker, "notificationclick")
        self.assertEqual(cita.empresa, self.empresa)

    def test_app_movil_y_archivos_de_instalacion_exigen_sesion_y_permiso(self):
        app_url = reverse("agenda_mobile", args=[self.empresa.slug])
        login_url = reverse("empresa_login", args=[self.empresa.slug])
        rutas_protegidas = [
            app_url,
            reverse("agenda_mobile_manifest", args=[self.empresa.slug]),
            reverse("agenda_mobile_service_worker", args=[self.empresa.slug]),
        ]

        for ruta in rutas_protegidas:
            response = self.client.get(ruta)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(f"{login_url}?next="))

        response = self.client.post(
            f"{login_url}?next={app_url}",
            {
                "username": "crmuser",
                "password": "pass12345",
                "next": app_url,
            },
        )
        self.assertRedirects(response, app_url, fetch_redirect_response=False)

        rol_sin_citas = RolSistema.objects.create(
            nombre="Sin Citas",
            codigo="sin-citas-app",
        )
        usuario_sin_permiso = Usuario.objects.create_user(
            username="sin_citas",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=rol_sin_citas,
        )
        self.client.force_login(usuario_sin_permiso)
        response = self.client.get(app_url)
        self.assertRedirects(
            response,
            reverse("dashboard", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )

    def test_estado_cita_desde_app_regresa_a_la_app_movil(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Estado App",
            rtn="08011999000002",
            telefono="99990002",
            activo=True,
        )
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            titulo="Control mÃ³vil",
            fecha_hora=timezone.make_aware(datetime(2026, 6, 30, 11, 0)),
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.post(
            reverse("agenda_cita_estado", args=[self.empresa.slug, cita.id]),
            {
                "estado": "confirmada",
                "fecha": "2026-06-30",
                "return_to": "mobile",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('agenda_mobile', args=[self.empresa.slug])}?vista=dia&fecha=2026-06-30",
            fetch_redirect_response=False,
        )
        cita.refresh_from_db()
        self.assertEqual(cita.estado, "confirmada")

    def test_calendario_ofrece_vistas_mes_semana_y_dia(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa, nombre="Paciente Calendario",
            rtn="08011999000003", telefono="99990003", activo=True,
        )
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            titulo="EvaluaciÃ³n mÃ©dica",
            fecha_hora=timezone.make_aware(datetime(2026, 6, 22, 10, 30)),
            duracion_minutos=45,
            responsable="Dra. Demo",
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])
        for vista in ["mes", "semana", "dia", "anio"]:
            response = self.client.get(url, {"vista": vista, "fecha": "2026-06-22"})
            self.assertEqual(response.status_code, 200)
            if vista != "anio":
                self.assertContains(response, "EvaluaciÃ³n mÃ©dica")
                self.assertContains(response, "Paciente Calendario")
            else:
                self.assertContains(response, "Paciente Calendario")
                self.assertContains(response, "junio")
        response = self.client.get(url, {"vista": "mes", "fecha": "2026-06-22", "editar": cita.id})
        self.assertContains(response, "Editando cita")
        self.assertContains(response, "45")

    def test_cita_puede_editarse_y_cambiar_estado_desde_calendario(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa, nombre="Paciente Estado",
            rtn="08011999000004", telefono="99990004", activo=True,
        )
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
            codigo="clinica_medica", defaults={"nombre": "ClÃ­nica MÃ©dica", "es_comercial": True}
        )
        EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo_clinica, defaults={"activo": True})
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-001", nombre="Paciente ClÃ­nico")
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa, nombre="Consulta de cardiologÃ­a", categoria="consulta", duracion_minutos=45
        )
        doctor = ProfesionalSalud.objects.create(
            empresa=self.empresa, nombre="Dr. Carlos Demo", especialidad="CardiologÃ­a"
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])

        response = self.client.get(url)
        self.assertContains(response, "Tipo de consulta")
        self.assertContains(response, "Doctor / profesional")
        self.assertContains(response, "Fecha y hora")
        self.assertContains(response, "appointment-datetime")
        self.assertContains(response, "Dr. Carlos Demo")
        self.assertNotContains(response, "<label for=\"id_titulo\">", html=False)
        self.assertNotContains(response, "id_duracion_minutos")

        response = self.client.post(url, {
            "paciente": paciente.id, "servicio_clinico": servicio.id,
            "profesional_salud": doctor.id, "fecha_cita": "2026-06-24",
            "hora_cita": "02:30", "periodo_cita": "PM",
            "duracion_minutos": "45", "estado": "confirmada", "pagada": "on", "observacion": "Primera valoracion",
        })
        self.assertEqual(response.status_code, 302)
        cita = CitaCliente.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertEqual(cita.titulo, "Consulta de cardiologÃ­a")
        self.assertTrue(cita.pagada)
        self.assertEqual(cita.responsable, "Dr. Carlos Demo")
        self.assertEqual(cita.profesional_salud, doctor)
        self.assertEqual(cita.duracion_minutos, servicio.duracion_minutos)
        self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 14)
        self.assertEqual(timezone.localtime(cita.fecha_hora).minute, 30)
        cita_clinica = CitaClinica.objects.get(id=cita.cita_clinica_id)
        self.assertEqual(cita_clinica.paciente, paciente)
        self.assertEqual(cita_clinica.profesional, doctor)
        self.assertEqual(cita_clinica.servicio, servicio)
        self.assertEqual(cita_clinica.estado, "confirmada")
        self.assertTrue(cita_clinica.pagada)

        calendario = self.client.get(url, {"vista": "mes", "fecha": "2026-06-24"})
        self.assertContains(calendario, "is-paid")
        self.assertContains(calendario, "data-appointment-paid=\"Pagada\"")
        self.assertContains(calendario, "appointment-paid-badge")

    def test_agenda_clinica_crea_paciente_rapido_y_lo_sincroniza_con_facturacion(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica",
            defaults={"nombre": "ClÃ­nica MÃ©dica", "es_comercial": True},
        )
        EmpresaModulo.objects.get_or_create(
            empresa=self.empresa,
            modulo=modulo_clinica,
            defaults={"activo": True},
        )
        self.client.login(username="crmuser", password="pass12345")

        agenda = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))
        self.assertContains(agenda, "+ Nuevo paciente")
        self.assertContains(agenda, "patientQuickModal")
        self.assertContains(agenda, "Buscar por nombre, identidad, expediente, teléfono o correo")
        self.assertContains(agenda, reverse("agenda_buscar_pacientes", args=[self.empresa.slug]))

        response = self.client.post(
            reverse("agenda_crear_paciente_rapido", args=[self.empresa.slug]),
            {
                "tipo_id": "dni",
                "identidad": "0801199012345",
                "primer_nombre": "Ana",
                "segundo_nombre": "MarÃ­a",
                "primer_apellido": "LÃ³pez",
                "segundo_apellido": "Paz",
                "fecha_nacimiento": "1990-05-12",
                "sexo": "femenino",
                "telefono": "99991111",
                "whatsapp": "",
                "correo": "ana@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        paciente = Paciente.objects.get(id=payload["paciente"]["id"])
        self.assertEqual(paciente.nombre, "Ana MarÃ­a LÃ³pez Paz")
        self.assertEqual(paciente.whatsapp, "99991111")
        self.assertEqual(paciente.creado_por, self.usuario)
        self.assertIsNotNone(paciente.cliente_id)
        self.assertEqual(paciente.cliente.nombre, paciente.nombre)
        self.assertEqual(paciente.cliente.telefono_whatsapp, "99991111")
        self.assertTrue(paciente.expediente_codigo.startswith("MIA-"))

        agenda_actualizada = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))
        self.assertContains(agenda_actualizada, "appointmentPatientsData")
        paciente_embebido = agenda_actualizada.context["pacientes_busqueda"][0]
        self.assertEqual(paciente_embebido["id"], paciente.id)
        self.assertEqual(paciente_embebido["nombre"], paciente.nombre)
        self.assertEqual(paciente_embebido["documento"], paciente.identidad)

        busqueda = self.client.get(
            reverse("agenda_buscar_pacientes", args=[self.empresa.slug]),
            {"q": "9012345"},
        )
        self.assertEqual(busqueda.status_code, 200)
        resultados = busqueda.json()["results"]
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["id"], paciente.id)
        self.assertEqual(resultados[0]["nombre"], paciente.nombre)
        self.assertEqual(resultados[0]["telefono"], "99991111")

        busqueda_nombre_completo = self.client.get(
            reverse("agenda_buscar_pacientes", args=[self.empresa.slug]),
            {"q": "Ana Paz"},
        )
        self.assertEqual(busqueda_nombre_completo.status_code, 200)
        self.assertEqual(busqueda_nombre_completo.json()["results"][0]["id"], paciente.id)

        pacientes_recientes = self.client.get(
            reverse("agenda_buscar_pacientes", args=[self.empresa.slug]),
        )
        self.assertEqual(pacientes_recientes.status_code, 200)
        self.assertEqual(pacientes_recientes.json()["results"][0]["id"], paciente.id)

    def test_agenda_hospital_mia_recupera_cliente_historico_y_muestra_selector_nativo(self):
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica",
            defaults={"nombre": "ClÃƒÂ­nica MÃƒÂ©dica", "es_comercial": True},
        )
        EmpresaModulo.objects.get_or_create(
            empresa=self.empresa,
            modulo=modulo_clinica,
            defaults={"activo": True},
        )
        cliente_historico = Cliente(
            empresa=self.empresa,
            nombre="Paciente Prueba Historico",
            rtn="0801199011111",
            telefono="99990011",
            activo=True,
        )
        Cliente.objects.bulk_create([cliente_historico])
        self.assertFalse(Paciente.objects.filter(cliente=cliente_historico).exists())
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        paciente = Paciente.objects.get(cliente=cliente_historico)
        self.assertEqual(paciente.nombre, "Paciente Prueba Historico")
        self.assertContains(response, 'class="appointment-patient-native"')
        self.assertContains(response, f'value="{paciente.id}"')

    def test_creacion_rapida_de_paciente_evitar_documento_duplicado(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-00001",
            nombre="Paciente existente",
            identidad="0801199012345",
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.post(
            reverse("agenda_crear_paciente_rapido", args=[self.empresa.slug]),
            {
                "tipo_id": "dni",
                "identidad": "0801199012345",
                "primer_nombre": "Paciente",
                "primer_apellido": "Duplicado",
                "whatsapp": "99992222",
                "sexo": "no_indicado",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("identidad", response.json()["errors"])
        self.assertEqual(Paciente.objects.filter(empresa=self.empresa).count(), 1)

    def test_agenda_clinica_muestra_modal_y_colores_por_tipo_consulta(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "ClÃ­nica MÃ©dica", "es_comercial": True}
        )
        EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo_clinica, defaults={"activo": True})
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-COLOR", nombre="Paciente Color")
        dr_luis = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis")
        dra_candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        consulta = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta general", categoria="consulta")
        spa = ServicioClinico.objects.create(empresa=self.empresa, nombre="Facial hidratante", categoria="spa")
        fecha = timezone.make_aware(datetime(2026, 6, 23, 10, 0))
        CitaCliente.objects.create(
            empresa=self.empresa, paciente=paciente, servicio_clinico=consulta,
            profesional_salud=dr_luis, titulo=consulta.nombre, responsable=dr_luis.nombre,
            fecha_hora=fecha,
        )
        CitaCliente.objects.create(
            empresa=self.empresa, paciente=paciente, servicio_clinico=spa,
            profesional_salud=dra_candy, titulo=spa.nombre, responsable=dra_candy.nombre,
            fecha_hora=fecha.replace(hour=11),
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]), {"vista": "mes", "fecha": "2026-06-23"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "calendarDayModal")
        self.assertContains(response, "calendarDayModalBack")
        self.assertContains(response, "calendar-month-compact")
        self.assertContains(response, "data-calendar-day=\"2026-06-23\"")
        self.assertContains(response, ">2</span>")
        self.assertContains(response, "color-consulta")
        self.assertContains(response, "color-spa")
        self.assertContains(response, "professional-doctor-luis")
        self.assertContains(response, "professional-dra-candy")
        self.assertContains(response, "calendar-professional-badge professional-badge-doctor-luis")
        self.assertContains(response, "#e85d04")
        self.assertContains(response, "Spa")

    def test_app_movil_muestra_fondo_de_consulta_y_badge_del_profesional(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="EXP-APP-COLOR",
            nombre="Paciente Color Movil",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Licenciada en enfermeria",
            especialidad="Licenciada en enfermeria",
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Terapias",
            categoria="tratamiento",
            color_calendario="#805AD5",
        )
        fecha = timezone.make_aware(datetime(2026, 6, 24, 9, 0))
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=profesional,
            titulo=servicio.nombre,
            responsable=profesional.nombre,
            fecha_hora=fecha,
        )
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(
            reverse("agenda_mobile", args=[self.empresa.slug]),
            {"vista": "dia", "fecha": "2026-06-24", "app": "agenda"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'appointment color-servicio-{servicio.id} professional-lic-enfermeria',
        )
        self.assertContains(
            response,
            'mobile-professional-badge professional-badge-lic-enfermeria',
        )
        self.assertContains(response, "background:color-mix")
        self.assertContains(response, "Paciente Color Movil")
        self.assertContains(response, "Licenciada en enfermeria")

    def test_agenda_clinica_permite_filtrar_y_deslizar_periodos(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "ClÃ­nica MÃ©dica", "es_comercial": True}
        )
        EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo_clinica, defaults={"activo": True})
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-FILTRO", nombre="Paciente Filtro")
        dra_candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        dr_luis = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis")
        consulta = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta general", categoria="consulta")
        facial = ServicioClinico.objects.create(empresa=self.empresa, nombre="Facial hidratante", categoria="spa")
        fecha = timezone.make_aware(datetime(2026, 7, 7, 9, 0))
        CitaCliente.objects.create(
            empresa=self.empresa, paciente=paciente, servicio_clinico=consulta,
            profesional_salud=dra_candy, titulo="Consulta de Candy", responsable=dra_candy.nombre,
            fecha_hora=fecha,
        )
        CitaCliente.objects.create(
            empresa=self.empresa, paciente=paciente, servicio_clinico=facial,
            profesional_salud=dr_luis, titulo="Facial de Luis", responsable=dr_luis.nombre,
            fecha_hora=fecha.replace(hour=10),
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_citas", args=[self.empresa.slug])

        response = self.client.get(url, {"vista": "semana", "fecha": "2026-07-07"})
        self.assertContains(response, "calendar-filter-bar")
        self.assertContains(response, "data-calendar-swipe")
        self.assertContains(response, "name=\"servicio\"")
        self.assertContains(response, "name=\"profesional\"")
        self.assertContains(response, "Consulta de Candy")
        self.assertContains(response, "Facial de Luis")

        filtrada = self.client.get(
            url,
            {
                "vista": "semana",
                "fecha": "2026-07-07",
                "servicio": consulta.id,
                "profesional": dra_candy.id,
            },
        )
        self.assertContains(filtrada, "Consulta de Candy")
        self.assertNotContains(filtrada, "Facial de Luis")
        self.assertContains(filtrada, f"servicio={consulta.id}")
        self.assertContains(filtrada, f"profesional={dra_candy.id}")

    def test_eliminar_cita_exige_motivo_y_limpia_registros_vinculados(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="EXP-DEL", nombre="Paciente EliminaciÃ³n"
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa, nombre="Consulta para eliminar", duracion_minutos=30
        )
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. AuditorÃ­a")
        fecha_hora = timezone.make_aware(datetime(2026, 6, 25, 14, 0))
        cita_clinica = CitaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            profesional=doctor,
            servicio=servicio,
            fecha_hora=fecha_hora,
            motivo=servicio.nombre,
        )
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=doctor,
            cita_clinica=cita_clinica,
            titulo=servicio.nombre,
            responsable=doctor.nombre,
            fecha_hora=fecha_hora,
        )
        notificacion = NotificacionCitaWhatsApp.objects.create(
            cita=cita,
            tipo="dia",
            programada_para=fecha_hora - timedelta(days=1),
        )
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_cita_eliminar", args=[self.empresa.slug, cita.id])

        response = self.client.get(url, {
            "vista": "dia", "fecha": "2026-06-25",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar eliminación de cita")
        self.assertContains(response, "Paciente Eliminaci")
        self.assertTrue(CitaCliente.objects.filter(id=cita.id).exists())

        response = self.client.get(
            reverse("agenda_citas", args=[self.empresa.slug]),
            {"vista": "dia", "fecha": "2026-06-25"},
        )
        self.assertContains(response, "Eliminar cita")
        self.assertContains(response, "Motivo obligatorio")

        response = self.client.post(url, {
            "motivo_eliminacion": "no", "vista": "dia", "fecha": "2026-06-25",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CitaCliente.objects.filter(id=cita.id).exists())
        self.assertTrue(CitaClinica.objects.filter(id=cita_clinica.id).exists())

        response = self.client.post(url, {
            "motivo_eliminacion": "El paciente cancelÃ³ definitivamente",
            "vista": "dia", "fecha": "2026-06-25",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CitaCliente.objects.filter(id=cita.id).exists())
        self.assertFalse(CitaClinica.objects.filter(id=cita_clinica.id).exists())
        self.assertFalse(NotificacionCitaWhatsApp.objects.filter(id=notificacion.id).exists())

    @patch("crm.appointment_notifications.enviar_plantilla_cita_whatsapp")
    def test_hospital_mia_programa_y_envia_recordatorios_sin_duplicar(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.cita"}]}
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        modulo_clinica, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "ClÃ­nica MÃ©dica", "es_comercial": True}
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
            "estado": "confirmada", "observacion": "Avisar automÃ¡ticamente",
            "enviar_confirmacion_whatsapp": "on", "recordatorio_semana_whatsapp": "on",
            "recordatorio_dia_whatsapp": "on",
        })
        self.assertEqual(response.status_code, 302)
        cita = CitaCliente.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertEqual(cita.notificaciones_whatsapp.count(), 3)
        confirmacion = cita.notificaciones_whatsapp.get(tipo="confirmacion")
        self.assertEqual(confirmacion.estado, "enviado")
        semana = cita.notificaciones_whatsapp.get(tipo="semana")
        self.assertEqual(timezone.localtime(semana.programada_para).hour, 9)
        self.assertEqual(timezone.localtime(semana.programada_para).minute, 0)
        dia = cita.notificaciones_whatsapp.get(tipo="dia")
        self.assertEqual(timezone.localtime(dia.programada_para).hour, 9)
        self.assertEqual(timezone.localtime(dia.programada_para).minute, 0)
        momento_semana = semana.programada_para + timedelta(minutes=1)
        with patch("crm.appointment_notifications.timezone.now", return_value=momento_semana):
            call_command("procesar_recordatorios_citas")
        semana.refresh_from_db()
        self.assertEqual(semana.estado, "enviado")
        with patch("crm.appointment_notifications.timezone.now", return_value=momento_semana):
            call_command("procesar_recordatorios_citas")
        self.assertEqual(mock_enviar.call_count, 2)

    @patch("crm.views.procesar_notificacion", side_effect=TimeoutError("Meta no respondiÃ³"))
    @patch("crm.appointment_notifications.enviar_plantilla_cita_whatsapp")
    def test_recordatorio_cita_incluye_enlace_solo_si_plantilla_lo_permite(self, mock_enviar, _mock_procesar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.link"}]}
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="EXP-LINK", nombre="Paciente Link", whatsapp="99990002"
        )
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta link", duracion_minutos=30)
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Link")
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.recordatorio_citas_activo = True
        config.whatsapp_phone_number_id = "phone-id"
        config.whatsapp_token = "token"
        config.whatsapp_plantilla_cita = "recordatorio_cita_link"
        config.whatsapp_cita_incluir_enlace = True
        config.save()
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=doctor,
            titulo=servicio.nombre,
            responsable=doctor.nombre,
            fecha_hora=timezone.localtime(timezone.now() + timedelta(days=8)).replace(second=0, microsecond=0),
            enviar_confirmacion_whatsapp=True,
        )
        NotificacionCitaWhatsApp.objects.create(
            cita=cita,
            tipo=NotificacionCitaWhatsApp.TIPO_CONFIRMACION,
            programada_para=timezone.now(),
        )

        call_command("procesar_recordatorios_citas")

        kwargs = mock_enviar.call_args.kwargs
        self.assertIn("https://dvsolutionshn.com/confirmacion/citas/", kwargs["enlace"])

    def test_paciente_confirma_y_cancela_cita_desde_enlace_publico(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="EXP-PUBLIC", nombre="Paciente Publico"
        )
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta publica", duracion_minutos=30)
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Publica")
        fecha = timezone.make_aware(datetime(2026, 7, 1, 9, 0))
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=doctor,
            titulo=servicio.nombre,
            responsable=doctor.nombre,
            fecha_hora=fecha,
        )
        cita_clinica = CitaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            profesional=doctor,
            servicio=servicio,
            fecha_hora=fecha,
            motivo=servicio.nombre,
        )
        cita.cita_clinica = cita_clinica
        cita.save(update_fields=["cita_clinica"])
        url = reverse("crm_cita_respuesta_publica", args=[generar_token_respuesta_cita(cita)])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirme su cita")
        response = self.client.post(url, {"accion": "confirmar"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cita confirmada")
        cita.refresh_from_db()
        cita_clinica.refresh_from_db()
        self.assertEqual(cita.estado, "confirmada")
        self.assertEqual(cita_clinica.estado, "confirmada")

        response = self.client.post(url, {"accion": "cancelar", "motivo": "Necesito otro horario"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cita cancelada")
        cita.refresh_from_db()
        cita_clinica.refresh_from_db()
        self.assertEqual(cita.estado, "cancelada")
        self.assertEqual(cita_clinica.estado, "cancelada")
        self.assertIn("Necesito otro horario", cita.observacion)

    @patch("crm.views.procesar_notificacion", side_effect=TimeoutError("Meta no respondio"))
    def test_falla_inesperada_de_whatsapp_no_impide_guardar_cita(self, _mock_procesar):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="EXP-TIMEOUT",
            nombre="Paciente con cita segura",
            whatsapp="99990001",
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa, nombre="Consulta segura", duracion_minutos=30
        )
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Resiliencia")
        fecha = timezone.localtime(timezone.now() + timedelta(days=10)).replace(second=0, microsecond=0)
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.post(reverse("agenda_citas", args=[self.empresa.slug]), {
            "paciente": paciente.id,
            "servicio_clinico": servicio.id,
            "profesional_salud": doctor.id,
            "fecha_hora": fecha.strftime("%Y-%m-%dT%H:%M"),
            "estado": "confirmada",
            "observacion": "No perder esta cita si Meta falla",
            "enviar_confirmacion_whatsapp": "on",
            "recordatorio_semana_whatsapp": "on",
            "recordatorio_dia_whatsapp": "on",
        })

        self.assertRedirects(response, reverse("agenda_citas", args=[self.empresa.slug]))
        self.assertTrue(CitaCliente.objects.filter(empresa=self.empresa, paciente=paciente).exists())
        cita = CitaCliente.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertIsNotNone(cita.cita_clinica_id)

    def test_traslape_de_horario_solo_bloquea_mismo_profesional(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-DR", nombre="Paciente Agenda")
        paciente_2 = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-DR2", nombre="Paciente Agenda Dos")
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta General", duracion_minutos=60)
        dr_luis = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis Gonzales")
        dra_candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        fecha_hora = timezone.make_aware(datetime(2026, 7, 22, 16, 0))
        CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=dr_luis,
            titulo=servicio.nombre,
            responsable=dr_luis.nombre,
            fecha_hora=fecha_hora,
            duracion_minutos=60,
        )

        base_data = {
            "paciente": paciente_2.id,
            "servicio_clinico": servicio.id,
            "fecha_cita": "2026-07-22",
            "hora_cita": "04:00",
            "periodo_cita": "PM",
            "estado": "pendiente",
        }
        distinta_doctora = CitaClienteForm(
            {**base_data, "profesional_salud": dra_candy.id},
            empresa=self.empresa,
        )
        mismo_doctor = CitaClienteForm(
            {**base_data, "profesional_salud": dr_luis.id},
            empresa=self.empresa,
        )

        self.assertTrue(distinta_doctora.is_valid(), distinta_doctora.errors.as_text())
        self.assertFalse(mismo_doctor.is_valid())
        self.assertIn("Ese horario se cruza", mismo_doctor.errors.as_text())

    def test_hospital_mia_cirugia_usa_hora_inicio_y_finalizacion(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-CIR-HM", nombre="Paciente Cirugia")
        paciente_2 = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-CIR-HM2", nombre="Paciente Cirugia Dos")
        cirugia = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Cirugia plastica",
            categoria="cirugia",
            duracion_minutos=60,
        )
        consulta = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Consulta General",
            categoria="consulta",
            duracion_minutos=30,
        )
        dra_candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        dr_luis = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis Gonzales")

        form = CitaClienteForm({
            "paciente": paciente.id,
            "servicio_clinico": cirugia.id,
            "profesional_salud": dra_candy.id,
            "fecha_cita": "2026-08-10",
            "hora_cita": "01:00",
            "periodo_cita": "PM",
            "cirugia_hora_fin": "06:00",
            "cirugia_periodo_fin": "PM",
            "cirugia_detalle": "Rinoplastia con control postoperatorio",
            "estado": "pendiente",
        }, empresa=self.empresa)

        self.assertIn("cirugia_hora_fin", form.fields)
        self.assertIn("cirugia_periodo_fin", form.fields)
        self.assertIn("cirugia_detalle", form.fields)
        self.assertTrue(form.is_valid(), form.errors.as_text())
        cita = form.save(commit=False)
        self.assertEqual(timezone.localtime(cita.fecha_hora).strftime("%Y-%m-%d %I:%M %p"), "2026-08-10 01:00 PM")
        self.assertEqual(timezone.localtime(cita.cirugia_fin_estimada).strftime("%Y-%m-%d %I:%M %p"), "2026-08-10 06:00 PM")
        cita.empresa = self.empresa
        cita.save()

        mismo_profesional = CitaClienteForm({
            "paciente": paciente_2.id,
            "servicio_clinico": consulta.id,
            "profesional_salud": dra_candy.id,
            "fecha_cita": "2026-08-10",
            "hora_cita": "06:30",
            "periodo_cita": "PM",
            "estado": "pendiente",
        }, empresa=self.empresa)
        otro_profesional = CitaClienteForm({
            "paciente": paciente_2.id,
            "servicio_clinico": consulta.id,
            "profesional_salud": dr_luis.id,
            "fecha_cita": "2026-08-10",
            "hora_cita": "06:30",
            "periodo_cita": "PM",
            "estado": "pendiente",
        }, empresa=self.empresa)

        self.assertFalse(mismo_profesional.is_valid())
        self.assertIn("bloqueado de 01:00 PM a 07:00 PM", mismo_profesional.errors.as_text())
        self.assertTrue(otro_profesional.is_valid(), otro_profesional.errors.as_text())

    def test_hospital_mia_consulta_puede_bloquear_rango_con_hora_final(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-RANGO", nombre="Paciente Rango")
        paciente_2 = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-RANGO2", nombre="Paciente Rango Dos")
        consulta = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Consulta General",
            categoria="consulta",
            duracion_minutos=30,
        )
        dra_candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        dr_luis = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis Gonzales")

        form = CitaClienteForm({
            "paciente": paciente.id,
            "servicio_clinico": consulta.id,
            "profesional_salud": dra_candy.id,
            "fecha_cita": "2026-08-12",
            "hora_cita": "02:00",
            "periodo_cita": "PM",
            "cirugia_hora_fin": "04:00",
            "cirugia_periodo_fin": "PM",
            "estado": "pendiente",
        }, empresa=self.empresa)

        self.assertTrue(form.is_valid(), form.errors.as_text())
        cita = form.save(commit=False)
        cita.empresa = self.empresa
        cita.save()
        self.assertEqual(timezone.localtime(cita.cirugia_fin_estimada).strftime("%Y-%m-%d %I:%M %p"), "2026-08-12 04:00 PM")

        mismo_profesional = CitaClienteForm({
            "paciente": paciente_2.id,
            "servicio_clinico": consulta.id,
            "profesional_salud": dra_candy.id,
            "fecha_cita": "2026-08-12",
            "hora_cita": "03:00",
            "periodo_cita": "PM",
            "estado": "pendiente",
        }, empresa=self.empresa)
        otro_profesional = CitaClienteForm({
            "paciente": paciente_2.id,
            "servicio_clinico": consulta.id,
            "profesional_salud": dr_luis.id,
            "fecha_cita": "2026-08-12",
            "hora_cita": "03:00",
            "periodo_cita": "PM",
            "estado": "pendiente",
        }, empresa=self.empresa)

        self.assertFalse(mismo_profesional.is_valid())
        self.assertIn("bloqueado de 02:00 PM a 04:00 PM", mismo_profesional.errors.as_text())
        self.assertTrue(otro_profesional.is_valid(), otro_profesional.errors.as_text())

    def test_agenda_respeta_capacidad_de_cubiculos_por_tipo_de_servicio(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra Candy Luque")
        tratamiento = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Tratamientos",
            categoria="tratamiento",
            duracion_minutos=60,
        )
        fecha_hora = timezone.make_aware(datetime(2026, 7, 22, 10, 0))
        for indice in range(4):
            paciente = Paciente.objects.create(
                empresa=self.empresa,
                expediente_codigo=f"EXP-TRAT-{indice}",
                nombre=f"Paciente Tratamiento {indice}",
            )
            CitaCliente.objects.create(
                empresa=self.empresa,
                paciente=paciente,
                servicio_clinico=tratamiento,
                profesional_salud=doctor,
                titulo=tratamiento.nombre,
                responsable=doctor.nombre,
                fecha_hora=fecha_hora,
                duracion_minutos=60,
            )

        paciente_extra = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="EXP-TRAT-EXTRA",
            nombre="Paciente Extra",
        )
        form_lleno = CitaClienteForm({
            "paciente": paciente_extra.id,
            "servicio_clinico": tratamiento.id,
            "profesional_salud": doctor.id,
            "fecha_cita": "2026-07-22",
            "hora_cita": "10:00",
            "periodo_cita": "AM",
            "estado": "pendiente",
        }, empresa=self.empresa)
        form_con_espacio = CitaClienteForm({
            "paciente": paciente_extra.id,
            "servicio_clinico": tratamiento.id,
            "profesional_salud": doctor.id,
            "fecha_cita": "2026-07-22",
            "hora_cita": "11:00",
            "periodo_cita": "AM",
            "estado": "pendiente",
        }, empresa=self.empresa)
        form_legacy_lleno = CitaClienteForm({
            "paciente": paciente_extra.id,
            "servicio_clinico": tratamiento.id,
            "profesional_salud": doctor.id,
            "fecha_hora": "2026-07-22T10:00",
            "estado": "pendiente",
        }, empresa=self.empresa)

        self.assertFalse(form_lleno.is_valid())
        self.assertIn("Capacidad: 4; ocupados: 4", form_lleno.errors.as_text())
        self.assertTrue(form_con_espacio.is_valid(), form_con_espacio.errors.as_text())
        self.assertFalse(form_legacy_lleno.is_valid())
        self.assertIn("Capacidad: 4; ocupados: 4", form_legacy_lleno.errors.as_text())

    def test_agenda_respeta_capacidad_de_terapias_y_camara(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        doctor = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Enfermera Agenda")
        servicios = [
            ("Terapias", "tratamiento", 3),
            ("Camara hiperbarica", "tratamiento", 3),
        ]
        for nombre, categoria, capacidad in servicios:
            servicio = ServicioClinico.objects.create(
                empresa=self.empresa,
                nombre=nombre,
                categoria=categoria,
                duracion_minutos=60,
            )
            fecha_hora = timezone.make_aware(datetime(2026, 7, 23, 9, 0))
            for indice in range(capacidad):
                paciente = Paciente.objects.create(
                    empresa=self.empresa,
                    expediente_codigo=f"EXP-{nombre[:3]}-{indice}",
                    nombre=f"Paciente {nombre} {indice}",
                )
                CitaCliente.objects.create(
                    empresa=self.empresa,
                    paciente=paciente,
                    servicio_clinico=servicio,
                    profesional_salud=doctor,
                    titulo=servicio.nombre,
                    responsable=doctor.nombre,
                    fecha_hora=fecha_hora,
                    duracion_minutos=60,
                )
            paciente_extra = Paciente.objects.create(
                empresa=self.empresa,
                expediente_codigo=f"EXP-{nombre[:3]}-EXTRA",
                nombre=f"Paciente Extra {nombre}",
            )
            form = CitaClienteForm({
                "paciente": paciente_extra.id,
                "servicio_clinico": servicio.id,
                "profesional_salud": doctor.id,
                "fecha_cita": "2026-07-23",
                "hora_cita": "09:00",
                "periodo_cita": "AM",
                "estado": "pendiente",
            }, empresa=self.empresa)

            self.assertFalse(form.is_valid())
            self.assertIn("Capacidad: 3; ocupados: 3", form.errors.as_text())

    def test_agenda_permite_repetir_enfermeria_hasta_completar_cupos(self):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        enfermeria = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Enfermeria")
        recursos = [
            ("Tratamientos", "tratamiento", 4, "08:00"),
            ("Terapias", "tratamiento", 3, "10:00"),
            ("Camara hiperbarica", "tratamiento", 3, "12:00"),
        ]

        for nombre, categoria, capacidad, hora in recursos:
            servicio = ServicioClinico.objects.create(
                empresa=self.empresa,
                nombre=nombre,
                categoria=categoria,
                duracion_minutos=60,
            )
            hora_datetime = datetime.strptime(hora, "%H:%M")
            fecha_hora = timezone.make_aware(datetime(2026, 7, 24, hora_datetime.hour, 0))
            for indice in range(capacidad - 1):
                paciente = Paciente.objects.create(
                    empresa=self.empresa,
                    expediente_codigo=f"EXP-CUPO-{nombre[:3]}-{indice}",
                    nombre=f"Paciente Cupo {nombre} {indice}",
                )
                CitaCliente.objects.create(
                    empresa=self.empresa,
                    paciente=paciente,
                    servicio_clinico=servicio,
                    profesional_salud=enfermeria,
                    titulo=servicio.nombre,
                    responsable=enfermeria.nombre,
                    fecha_hora=fecha_hora,
                    duracion_minutos=60,
                )

            paciente_ultimo_cupo = Paciente.objects.create(
                empresa=self.empresa,
                expediente_codigo=f"EXP-CUPO-{nombre[:3]}-ULTIMO",
                nombre=f"Ultimo cupo {nombre}",
            )
            form_ultimo_cupo = CitaClienteForm(
                {
                    "paciente": paciente_ultimo_cupo.id,
                    "servicio_clinico": servicio.id,
                    "profesional_salud": enfermeria.id,
                    "fecha_cita": "2026-07-24",
                    "hora_cita": hora,
                    "periodo_cita": "AM" if hora_datetime.hour < 12 else "PM",
                    "estado": "pendiente",
                },
                empresa=self.empresa,
            )
            self.assertTrue(form_ultimo_cupo.is_valid(), form_ultimo_cupo.errors.as_text())
            cita = form_ultimo_cupo.save(commit=False)
            cita.empresa = self.empresa
            cita.save()

            paciente_sin_cupo = Paciente.objects.create(
                empresa=self.empresa,
                expediente_codigo=f"EXP-CUPO-{nombre[:3]}-LLENO",
                nombre=f"Sin cupo {nombre}",
            )
            form_sin_cupo = CitaClienteForm(
                {
                    "paciente": paciente_sin_cupo.id,
                    "servicio_clinico": servicio.id,
                    "profesional_salud": enfermeria.id,
                    "fecha_cita": "2026-07-24",
                    "hora_cita": hora,
                    "periodo_cita": "AM" if hora_datetime.hour < 12 else "PM",
                    "estado": "pendiente",
                },
                empresa=self.empresa,
            )
            self.assertFalse(form_sin_cupo.is_valid())
            self.assertIn(
                f"Capacidad: {capacidad}; ocupados: {capacidad}",
                form_sin_cupo.errors.as_text(),
            )

    @patch("crm.views.enviar_plantilla_cita_whatsapp")
    def test_modal_cita_permite_cancelar_y_reagendar_con_whatsapp(self, mock_whatsapp):
        mock_whatsapp.return_value = {"messages": [{"id": "wamid.action"}]}
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token"
        config.save()
        paciente = Paciente.objects.create(empresa=self.empresa, expediente_codigo="EXP-ACT", nombre="Paciente Accion", whatsapp="99990000")
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta accion")
        fecha_hora = timezone.make_aware(datetime(2026, 7, 15, 10, 0))
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            titulo=servicio.nombre,
            fecha_hora=fecha_hora,
        )
        self.client.login(username="crmuser", password="pass12345")
        agenda = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]), {"vista": "dia", "fecha": "2026-07-15"})
        self.assertContains(agenda, reverse("agenda_cita_cancelar_whatsapp", args=[self.empresa.slug, cita.id]))
        self.assertContains(agenda, reverse("agenda_cita_reagendar_whatsapp", args=[self.empresa.slug, cita.id]))

        response = self.client.post(reverse("agenda_cita_cancelar_whatsapp", args=[self.empresa.slug, cita.id]), {
            "motivo": "Cambio medico", "vista": "dia", "fecha": "2026-07-15",
        })
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, "cancelada")

        response = self.client.post(reverse("agenda_cita_reagendar_whatsapp", args=[self.empresa.slug, cita.id]), {
            "nueva_fecha": "2026-07-16", "nueva_hora": "11:30", "nueva_periodo": "AM", "vista": "dia", "fecha": "2026-07-15",
        })
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(timezone.localtime(cita.fecha_hora).strftime("%Y-%m-%dT%H:%M"), "2026-07-16T11:30")
        self.assertEqual(mock_whatsapp.call_count, 2)
        self.assertEqual(mock_whatsapp.call_args_list[0].kwargs["aviso"], "cita cancelada")
        self.assertEqual(mock_whatsapp.call_args_list[1].kwargs["aviso"], "cita reagendada")

    def test_preparar_envios_de_campania_crea_whatsapp_por_cliente(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Demo",
            rtn="08011999000005",
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

    def test_plantilla_existente_se_puede_editar_desde_crm(self):
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Cumpleaños",
            tipo="cumpleanos",
            canal="whatsapp",
            mensaje="Mensaje original",
        )

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(f"{reverse('crm_plantillas', args=[self.empresa.slug])}?editar={plantilla.id}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar plantilla")
        self.assertContains(response, "Mensaje original")

        response = self.client.post(reverse("crm_plantillas", args=[self.empresa.slug]), {
            "plantilla_id": plantilla.id,
            "nombre": "Cumpleaños Premium",
            "tipo": "cumpleanos",
            "canal": "whatsapp",
            "asunto": "",
            "mensaje": "Hola {{cliente}}, tenemos una atención especial para ti.",
            "activa": "on",
        })

        self.assertRedirects(response, reverse("crm_plantillas", args=[self.empresa.slug]))
        plantilla.refresh_from_db()
        self.assertEqual(plantilla.nombre, "Cumpleaños Premium")
        self.assertIn("atención especial", plantilla.mensaje)

    def test_plantillas_y_campanias_estan_aisladas_por_empresa(self):
        otra_empresa = Empresa.objects.create(
            nombre="Mia Medical Spa",
            slug="medical_spa",
            rtn="08011999111114",
            estado_licencia="activa",
        )
        EmpresaModulo.objects.create(empresa=otra_empresa, modulo=self.modulo, activo=True)
        plantilla_hospital = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Cumpleaños Hospital",
            tipo="cumpleanos",
            canal="whatsapp",
            mensaje="Mensaje Hospital Mia",
        )
        plantilla_spa = PlantillaMensaje.objects.create(
            empresa=otra_empresa,
            nombre="Cumpleaños Spa",
            tipo="cumpleanos",
            canal="whatsapp",
            mensaje="Mensaje Medical Spa",
        )
        CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campaña Hospital",
            plantilla=plantilla_hospital,
            audiencia="promociones",
        )
        CampaniaMarketing.objects.create(
            empresa=otra_empresa,
            nombre="Campaña Spa",
            plantilla=plantilla_spa,
            audiencia="promociones",
        )

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("crm_plantillas", args=[self.empresa.slug]))
        self.assertContains(response, "Cumpleaños Hospital")
        self.assertNotContains(response, "Cumpleaños Spa")

        response = self.client.get(reverse("crm_campanias", args=[self.empresa.slug]))
        self.assertContains(response, "Campaña Hospital")
        self.assertNotContains(response, "Campaña Spa")

        response = self.client.get(f"{reverse('crm_plantillas', args=[self.empresa.slug])}?editar={plantilla_spa.id}")
        self.assertEqual(response.status_code, 404)

    @patch("crm.appointment_notifications.enviar_plantilla_marketing_whatsapp")
    def test_recordatorio_cumpleanos_automatico_envia_1_y_7_dias(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.birthday"}]}
        ahora = timezone.make_aware(datetime(2026, 7, 11, 9, 5))
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.recordatorio_cumpleanos_activo = True
        config.cumpleanos_recordatorio_1_dia = True
        config.cumpleanos_recordatorio_7_dias = True
        config.save()
        PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Cumpleaños",
            tipo="cumpleanos",
            canal="whatsapp",
            mensaje="Hola {{cliente}}, feliz cumpleaños de parte de {{empresa}}.",
        )
        Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Manana",
            rtn="08011999000031",
            telefono_whatsapp="99990031",
            fecha_nacimiento=date(1990, 7, 12),
            activo=True,
        )
        Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Semana",
            rtn="08011999000032",
            telefono_whatsapp="99990032",
            fecha_nacimiento=date(1990, 7, 18),
            activo=True,
        )

        from crm.appointment_notifications import procesar_recordatorios_cumpleanos
        resultado = procesar_recordatorios_cumpleanos(ahora=ahora)

        self.assertEqual(resultado["enviadas"], 2)
        self.assertEqual(NotificacionCumpleanosWhatsApp.objects.filter(empresa=self.empresa, estado="enviado").count(), 2)
        self.assertEqual(mock_enviar.call_count, 2)

    @patch("crm.views.enviar_plantilla_marketing_whatsapp")
    def test_enviar_campania_por_api_actualiza_envios(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.test"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente API",
            rtn="08011999000006",
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
            rtn="08011999000007",
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
            rtn="08011999000008",
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

    def _crear_cita_camara_para_control(self, *, sesion_servicio=0):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="EXP-HBO-001",
            nombre="Paciente Cámara Hiperbárica",
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Cámara hiperbárica",
            categoria="tratamiento",
            duracion_minutos=60,
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Licenciada en enfermería",
        )
        fecha_hora = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
            hour=9,
            minute=0,
            second=0,
            microsecond=0,
        )
        cita = CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=profesional,
            titulo=servicio.nombre,
            responsable=profesional.nombre,
            fecha_hora=fecha_hora,
            duracion_minutos=60,
            sesion_servicio=sesion_servicio,
        )
        return cita

    def _datos_validos_control_camara(self):
        return {
            "cirugia": "Procedimiento de prueba",
            "indicacion": "Indicada por el médico tratante",
            "programa": "20x45",
            "orden_medica": "Orden médica registrada",
            "numero_sesion": "1",
            "observaciones_previas": "Paciente estable",
            "firma_control_previo": "Enfermera responsable",
            "presion_arterial_antes": "120/80",
            "saturacion_oxigeno_antes": "98%",
            "presion_camara": "2 ATA",
            "tiempo_minutos": "45",
            "compensacion_oidos": "Adecuada",
            "tolerancia": "buena",
            "presion_arterial_despues": "118/78",
            "saturacion_oxigeno_despues": "99%",
            "evolucion_evento_adverso": "Sin eventos adversos",
            "firma_parametros": "Enfermera responsable",
            "nota_enfermeria": "Sesión tolerada sin complicaciones.",
            "firma_enfermeria": "Enfermera responsable",
            "estado_general_estable": "si",
            "sin_fiebre": "si",
            "sin_dificultad_respiratoria": "si",
            "sin_dolor_toracico": "si",
            "sin_sintomas_neurologicos": "si",
            "sin_dolor_oido": "si",
            "compensa_ambos_oidos": "si",
            "area_quirurgica_revisada": "si",
            "seguridad_camara_verificada": "si",
            "apto_para_sesion": "si",
        }

    def test_control_camara_es_modulo_independiente_y_no_aparece_en_calendario(self):
        cita = self._crear_cita_camara_para_control()
        self.client.login(username="crmuser", password="pass12345")

        response_calendario = self.client.get(
            reverse("agenda_citas", args=[self.empresa.slug]),
            {
                "vista": "dia",
                "fecha": timezone.localtime(cita.fecha_hora).date().isoformat(),
                "control_camara": cita.id,
            },
        )
        self.assertEqual(response_calendario.status_code, 200)
        self.assertNotContains(response_calendario, "Seguimiento de 22 sesiones")
        self.assertContains(response_calendario, "Cámara hiperbárica")

        response = self.client.get(
            reverse("agenda_camara_hiperbarica", args=[self.empresa.slug]),
            {
                "fecha": timezone.localtime(cita.fecha_hora).date().isoformat(),
                "control_camara": cita.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control de Cámara Hiperbárica")
        self.assertContains(response, "Programa de 22 sesiones")
        self.assertContains(response, "Paciente Cámara Hiperbárica")
        self.assertContains(response, "Control previo de seguridad")
        self.assertContains(response, "Parámetros de la sesión")
        self.assertContains(response, "Nota de enfermería")
        self.assertContains(response, "Guardar borrador")
        self.assertContains(response, "Finalizar sesión")

    def test_control_camara_guarda_borrador_y_bloquea_al_finalizar(self):
        cita = self._crear_cita_camara_para_control()
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_camara_hiperbarica_guardar", args=[self.empresa.slug, cita.id])
        base = self._datos_validos_control_camara()

        response = self.client.post(url, {**base, "accion": "borrador"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/camara-hiperbarica/", response.url)
        sesion = SesionCamaraHiperbarica.objects.get(cita=cita)
        self.assertEqual(sesion.estado, "borrador")
        self.assertEqual(sesion.numero_sesion, 1)
        self.assertEqual(timezone.localtime(sesion.fecha_sesion).date(), timezone.localdate())
        self.assertEqual(ProgramaCamaraHiperbarica.objects.filter(paciente=cita.paciente).count(), 1)

        response = self.client.post(
            url,
            {**base, "programa_id": sesion.programa_id, "accion": "finalizar"},
        )
        self.assertEqual(response.status_code, 302)
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, "finalizada")

        response = self.client.post(
            url,
            {**base, "programa_id": sesion.programa_id, "nota_enfermeria": "Intento posterior", "accion": "borrador"},
        )
        self.assertEqual(response.status_code, 302)
        sesion.refresh_from_db()
        self.assertEqual(sesion.nota_enfermeria, "Sesión tolerada sin complicaciones.")
        self.assertEqual(sesion.estado, "finalizada")

    def test_control_camara_toma_numero_de_sesion_desde_la_cita(self):
        cita = self._crear_cita_camara_para_control(sesion_servicio=6)
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_camara_hiperbarica_guardar", args=[self.empresa.slug, cita.id])

        response = self.client.post(
            url,
            {**self._datos_validos_control_camara(), "numero_sesion": "1", "accion": "finalizar"},
        )

        self.assertEqual(response.status_code, 302)
        sesion = SesionCamaraHiperbarica.objects.get(cita=cita)
        self.assertEqual(sesion.numero_sesion, 6)
        self.assertEqual(sesion.estado, "finalizada")

    def test_control_camara_con_error_conserva_datos_y_marca_pendientes(self):
        cita = self._crear_cita_camara_para_control(sesion_servicio=4)
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_camara_hiperbarica_guardar", args=[self.empresa.slug, cita.id])
        datos = self._datos_validos_control_camara()
        datos["nota_enfermeria"] = "Texto clínico que debe conservarse"
        datos["firma_enfermeria"] = ""
        datos["accion"] = "finalizar"

        response = self.client.post(url, datos)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faltan datos para finalizar la sesión")
        self.assertContains(response, "Texto clínico que debe conservarse")
        self.assertContains(response, "Desde la cita")
        self.assertContains(response, ">4<", html=False)
        self.assertContains(response, "has-error")
        formulario = response.context["sesion_camara_form"]
        self.assertTrue(formulario.is_bound)
        self.assertEqual(formulario.data["nota_enfermeria"], "Texto clínico que debe conservarse")
        self.assertEqual(formulario.data.getlist("sin_fiebre"), ["si"])
        self.assertIn("firma_enfermeria", formulario.errors)

        sesion = SesionCamaraHiperbarica.objects.get(cita=cita)
        self.assertEqual(sesion.estado, "borrador")
        self.assertEqual(sesion.numero_sesion, 4)
        self.assertEqual(sesion.nota_enfermeria, "Texto clínico que debe conservarse")
        self.assertEqual(sesion.sin_fiebre, "si")
        self.assertEqual(sesion.firma_enfermeria, "")

    def test_control_camara_marca_todos_los_campos_faltantes_y_conserva_el_resto(self):
        cita = self._crear_cita_camara_para_control(sesion_servicio=8)
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_camara_hiperbarica_guardar", args=[self.empresa.slug, cita.id])
        datos = self._datos_validos_control_camara()
        datos.update({
            "numero_sesion": "8",
            "observaciones_previas": "",
            "presion_camara": "",
            "evolucion_evento_adverso": "",
            "nota_enfermeria": "Contenido que no debe borrarse",
            "accion": "finalizar",
        })

        response = self.client.post(url, datos)

        self.assertEqual(response.status_code, 200)
        formulario = response.context["sesion_camara_form"]
        self.assertTrue(formulario.is_bound)
        self.assertIn("observaciones_previas", formulario.errors)
        self.assertIn("presion_camara", formulario.errors)
        self.assertIn("evolucion_evento_adverso", formulario.errors)
        self.assertContains(response, "Contenido que no debe borrarse")
        self.assertContains(response, 'data-hbo-form novalidate', html=False)
        self.assertContains(response, 'class="hbo-cell has-error"', count=3, html=False)

        sesion = SesionCamaraHiperbarica.objects.get(cita=cita)
        self.assertEqual(sesion.estado, "borrador")
        self.assertEqual(sesion.numero_sesion, 8)
        self.assertEqual(sesion.nota_enfermeria, "Contenido que no debe borrarse")

    def _crear_cita_terapia_postquirurgica(self, *, sesion_servicio=0):
        self.empresa.tipo_solucion = "clinica"
        self.empresa.save(update_fields=["tipo_solucion"])
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="EXP-TPQ-001",
            nombre="Paciente Terapia Post Quirúrgica",
        )
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Terapias Post Quirúrgicas",
            categoria="tratamiento",
            duracion_minutos=60,
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Licenciada en enfermería",
        )
        fecha_hora = timezone.localtime(timezone.now() + timedelta(days=3)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )
        return CitaCliente.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            servicio_clinico=servicio,
            profesional_salud=profesional,
            titulo=servicio.nombre,
            responsable=profesional.nombre,
            fecha_hora=fecha_hora,
            duracion_minutos=60,
            sesion_servicio=sesion_servicio,
        )

    def _datos_validos_terapia_postquirurgica(self):
        return {
            "cirugia": "Abdominoplastia",
            "fecha_cirugia": "2026-08-20",
            "numero_sesion": "1",
            "hora_inicio": "10:00",
            "hora_finalizacion": "11:00",
            "presion_arterial": "120/80",
            "frecuencia_cardiaca": "76",
            "frecuencia_respiratoria": "18",
            "saturacion_oxigeno": "98",
            "temperatura": "36.5",
            "escala_dolor": "3",
            "estado_paciente": ["bueno", "edema"],
            "equipos_utilizados": ["usg", "presoterapia"],
            "minutos_area": "30 minutos en abdomen",
            "cuidados_realizados": ["drenaje_linfatico"],
            "cuidado_otro": "",
            "nota_enfermeria": "Paciente tolera la terapia sin complicaciones.",
            "enfermera_nombre": "Lic. Enfermería",
            "firma_enfermeria": "Lic. Enfermería",
        }

    def test_terapias_postquirurgicas_es_modulo_independiente(self):
        cita = self._crear_cita_terapia_postquirurgica(sesion_servicio=2)
        self.client.login(username="crmuser", password="pass12345")

        response = self.client.get(
            reverse("agenda_terapias_postquirurgicas", args=[self.empresa.slug]),
            {
                "fecha": timezone.localtime(cita.fecha_hora).date().isoformat(),
                "control_terapia": cita.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/terapias_postquirurgicas.html")
        self.assertContains(response, "Terapias Post Quirúrgicas")
        self.assertContains(response, "Programa de 12 sesiones")
        self.assertContains(response, "Signos vitales")
        self.assertContains(response, "Protocolo / máquinas")
        self.assertContains(response, "Terapia manual / cuidados")
        self.assertContains(response, "Guardar borrador")
        self.assertContains(response, "Finalizar sesión")
        self.assertEqual(len(response.context["tablero_sesiones_terapia"]), 12)

    def test_terapia_postquirurgica_guarda_borrador_finaliza_y_bloquea(self):
        cita = self._crear_cita_terapia_postquirurgica(sesion_servicio=3)
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_terapias_postquirurgicas_guardar", args=[self.empresa.slug, cita.id])
        datos = self._datos_validos_terapia_postquirurgica()

        response = self.client.post(url, {**datos, "accion": "borrador"})
        self.assertEqual(response.status_code, 302)
        sesion = SesionTerapiaPostQuirurgica.objects.get(cita=cita)
        self.assertEqual(sesion.estado, "borrador")
        self.assertEqual(sesion.numero_sesion, 3)
        self.assertEqual(sesion.estado_paciente, ["bueno", "edema"])
        self.assertEqual(ProgramaTerapiaPostQuirurgica.objects.filter(paciente=cita.paciente).count(), 1)

        response = self.client.post(
            url,
            {**datos, "programa_id": sesion.programa_id, "accion": "finalizar"},
        )
        self.assertEqual(response.status_code, 302)
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, "finalizada")

        response = self.client.post(
            url,
            {**datos, "programa_id": sesion.programa_id, "nota_enfermeria": "Cambio posterior", "accion": "borrador"},
        )
        self.assertEqual(response.status_code, 302)
        sesion.refresh_from_db()
        self.assertEqual(sesion.nota_enfermeria, "Paciente tolera la terapia sin complicaciones.")

    def test_terapia_postquirurgica_con_error_conserva_borrador_y_marca_campos(self):
        cita = self._crear_cita_terapia_postquirurgica(sesion_servicio=4)
        self.client.login(username="crmuser", password="pass12345")
        url = reverse("agenda_terapias_postquirurgicas_guardar", args=[self.empresa.slug, cita.id])
        datos = self._datos_validos_terapia_postquirurgica()
        datos.update({
            "nota_enfermeria": "Contenido clínico que debe conservarse",
            "firma_enfermeria": "",
            "accion": "finalizar",
        })

        response = self.client.post(url, datos)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faltan datos para finalizar la sesión")
        self.assertContains(response, "Contenido clínico que debe conservarse")
        self.assertContains(response, "has-error")
        formulario = response.context["sesion_terapia_form"]
        self.assertTrue(formulario.is_bound)
        self.assertIn("firma_enfermeria", formulario.errors)
        self.assertEqual(formulario.data.getlist("estado_paciente"), ["bueno", "edema"])

        sesion = SesionTerapiaPostQuirurgica.objects.get(cita=cita)
        self.assertEqual(sesion.estado, "borrador")
        self.assertEqual(sesion.numero_sesion, 4)
        self.assertEqual(sesion.nota_enfermeria, "Contenido clínico que debe conservarse")
