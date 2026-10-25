from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from crm.forms import CitaClienteForm
from crm.models import CitaCliente
from crm.views import _sincronizar_cita_clinica
from facturacion.models import Cliente

from .forms import CitaClinicaForm
from .models import CitaClinica, HistorialRestriccionCitasPaciente, Paciente, ProfesionalSalud, ServicioClinico
from .restricciones_citas import MENSAJE_NO_AGENDAR
from .views import _sincronizar_agenda_desde_cita_clinica


class NoAgendarFlujosTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia", slug="hospital_mia", tipo_solucion="clinica", estado_licencia="activa",
        )
        for codigo in ("clinica_medica", "agenda_citas", "crm_marketing"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa)
        self.rol = RolSistema.objects.create(
            nombre="Recepcion Prueba", codigo="recepcion-no-agendar", activo=True,
            puede_clinica=True, puede_pacientes=True, puede_citas=True, puede_crm=True,
            puede_expediente_clinico=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="recepcion-no-agendar", password="pass", empresa=self.empresa, rol_sistema=self.rol,
        )
        self.client.force_login(self.usuario)
        self.paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="MIA-RESTR", nombre="Paciente restringido",
            identidad="0801199000001", sexo="femenino",
        )
        self.profesional = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Prueba")
        self.servicio = ServicioClinico.objects.create(
            empresa=self.empresa, nombre="Consulta prueba", categoria="consulta", duracion_minutos=30,
        )
        self.fecha = (timezone.now() + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)

    def restringir(self):
        Paciente.objects.filter(pk=self.paciente.pk).update(
            no_agendar=True, motivo_no_agendar="Motivo confidencial de la restricción",
        )

    def datos_agenda(self):
        return {
            "paciente": self.paciente.pk, "servicio_clinico": self.servicio.pk,
            "profesional_salud": self.profesional.pk,
            "fecha_hora": timezone.localtime(self.fecha).strftime("%Y-%m-%dT%H:%M"),
            "estado": "pendiente",
        }

    def datos_clinica(self):
        return {
            "paciente": self.paciente.pk, "profesional": self.profesional.pk,
            "servicio": self.servicio.pk,
            "fecha_hora": timezone.localtime(self.fecha).strftime("%Y-%m-%dT%H:%M"),
            "estado": "solicitada", "canal": "recepcion", "motivo": "Consulta de seguimiento",
        }

    def test_guardas_ambos_modelos_rechazan_inserciones_incluso_con_paciente_en_cache(self):
        self.restringir()
        with self.assertRaisesMessage(ValidationError, MENSAJE_NO_AGENDAR):
            CitaClinica.objects.create(
                empresa=self.empresa, paciente=self.paciente, fecha_hora=self.fecha, motivo="Prueba",
            )
        with self.assertRaisesMessage(ValidationError, MENSAJE_NO_AGENDAR):
            CitaCliente.objects.create(
                empresa=self.empresa, paciente=self.paciente, fecha_hora=self.fecha, titulo="Prueba",
            )
        self.assertFalse(CitaClinica.objects.exists())
        self.assertFalse(CitaCliente.objects.exists())

    def test_cliente_vinculado_no_permite_evitar_restriccion(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente vinculado", rtn=self.paciente.identidad)
        self.paciente.cliente = cliente
        self.paciente.save(update_fields=["cliente"])
        self.restringir()
        with self.assertRaisesMessage(ValidationError, MENSAJE_NO_AGENDAR):
            CitaCliente.objects.create(
                empresa=self.empresa, cliente=cliente, fecha_hora=self.fecha, titulo="Prueba",
            )

    def test_formularios_rechazan_nuevas_citas_con_mensaje_y_ocultan_motivo(self):
        self.restringir()
        formularios = (
            CitaClienteForm(self.datos_agenda(), empresa=self.empresa, usuario=self.usuario),
            CitaClinicaForm(self.datos_clinica(), empresa=self.empresa, usuario=self.usuario),
        )
        for formulario in formularios:
            with self.subTest(formulario=type(formulario).__name__):
                self.assertFalse(formulario.is_valid())
                self.assertIn(MENSAJE_NO_AGENDAR, str(formulario.non_field_errors()))
                self.assertNotIn("Motivo confidencial", str(formulario.errors))

    def test_agenda_web_crm_y_app_bloquean_post_sin_borrar_citas(self):
        self.restringir()
        for nombre in ("agenda_citas", "crm_citas", "agenda_mobile"):
            with self.subTest(ruta=nombre):
                respuesta = self.client.post(reverse(nombre, args=[self.empresa.slug]), self.datos_agenda())
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, MENSAJE_NO_AGENDAR)
                self.assertNotContains(respuesta, "Motivo confidencial")
        self.assertFalse(CitaCliente.objects.exists())
        self.assertFalse(CitaClinica.objects.exists())

    def test_nueva_cita_clinica_y_recordatorios_bloqueados(self):
        self.restringir()
        respuesta = self.client.post(
            reverse("clinica_crear_cita", args=[self.empresa.slug]), self.datos_clinica(),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, MENSAJE_NO_AGENDAR)
        respuesta = self.client.post(
            reverse("clinica_crear_recordatorios_tratamiento", args=[self.empresa.slug, self.paciente.pk]),
            {
                "tratamiento[]": ["Curación"], "fecha[]": [self.fecha.date().isoformat()],
                "hora[]": ["10:00"], "periodo[]": ["AM"],
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(CitaClinica.objects.exists())

    def test_serie_restringida_no_guarda_parcialmente(self):
        self.restringir()
        datos = {**self.datos_agenda(), "crear_serie": "on", "cantidad_serie": "3"}
        respuesta = self.client.post(reverse("agenda_citas", args=[self.empresa.slug]), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, MENSAJE_NO_AGENDAR)
        self.assertFalse(CitaCliente.objects.exists())

    def test_guardado_en_carrera_muestra_error_en_vez_de_500(self):
        for nombre in ("agenda_citas", "crm_citas", "agenda_mobile"):
            with self.subTest(ruta=nombre), patch(
                "crm.views._guardar_cita_formulario", side_effect=ValidationError(MENSAJE_NO_AGENDAR),
            ):
                respuesta = self.client.post(reverse(nombre, args=[self.empresa.slug]), self.datos_agenda())
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, MENSAJE_NO_AGENDAR)

    def test_citas_anteriores_se_editan_y_generan_su_espejo_sin_nuevas_reservas(self):
        agenda = CitaCliente.objects.create(
            empresa=self.empresa, paciente=self.paciente, fecha_hora=self.fecha, titulo="Consulta existente",
        )
        clinica = CitaClinica.objects.create(
            empresa=self.empresa, paciente=self.paciente, fecha_hora=self.fecha, motivo="Recordatorio existente",
            es_recordatorio_tratamiento=True,
        )
        self.restringir()
        agenda.estado = "cancelada"
        agenda.save(update_fields=["estado"])
        _sincronizar_cita_clinica(agenda)
        clinica.fecha_hora += timedelta(hours=2)
        clinica.save(update_fields=["fecha_hora"])
        with patch("crm.appointment_notifications.programar_notificaciones_cita", return_value=[]):
            _sincronizar_agenda_desde_cita_clinica(clinica)
        self.assertEqual(CitaCliente.objects.count(), 2)
        self.assertEqual(CitaClinica.objects.count(), 2)

    def test_busqueda_marca_restringido_sin_exponer_motivo(self):
        self.restringir()
        respuesta = self.client.get(
            reverse("agenda_buscar_pacientes", args=[self.empresa.slug]), {"q": self.paciente.nombre},
        )
        self.assertEqual(respuesta.status_code, 200)
        fila = respuesta.json()["results"][0]
        self.assertTrue(fila["no_agendar"])
        self.assertEqual(fila["restriccion_mensaje"], MENSAJE_NO_AGENDAR)

    def test_eliminar_paciente_con_auditoria_en_otra_empresa_conserva_expediente(self):
        empresa = Empresa.objects.create(
            nombre="Otra clinica", slug="otra-clinica", tipo_solucion="clinica", estado_licencia="activa", rtn="RESTRICCION-OTRA",
        )
        modulo = Modulo.objects.get(codigo="clinica_medica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
        cliente = Cliente.objects.create(empresa=empresa, nombre="Paciente auditado", rtn="0801199000009")
        paciente = Paciente.objects.create(
            empresa=empresa, cliente=cliente, nombre="Paciente auditado", expediente_codigo="OTRA-1",
            identidad="0801199000009",
        )
        historial = HistorialRestriccionCitasPaciente.objects.create(
            empresa=empresa, paciente=paciente, estado_anterior=False, estado_nuevo=True,
            motivo="Motivo previo", usuario=self.usuario, usuario_nombre=self.usuario.username,
        )
        self.usuario.is_superuser = True
        self.usuario.save(update_fields=["is_superuser"])

        respuesta = self.client.post(reverse("clinica_eliminar_paciente", args=[empresa.slug, paciente.pk]))

        self.assertEqual(respuesta.status_code, 302)
        paciente.refresh_from_db()
        cliente.refresh_from_db()
        self.assertFalse(paciente.activo)
        self.assertFalse(cliente.activo)
        self.assertTrue(HistorialRestriccionCitasPaciente.objects.filter(pk=historial.pk).exists())
