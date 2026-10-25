from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from crm.models import CitaCliente
from facturacion.models import Cliente

from .models import CitaClinica, HistorialRestriccionCitasPaciente, Paciente
from .restricciones_citas import MENSAJE_NO_AGENDAR, cambiar_restriccion_citas, contar_citas_futuras, validar_paciente_para_cita


class RestriccionCitasPacienteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Hospital Mía", slug="hospital_mia", tipo_solucion="clinica", rtn="RESTRICCION-MIA")
        cls.otra = Empresa.objects.create(nombre="Luque", slug="luque_aestetic", tipo_solucion="clinica", rtn="RESTRICCION-LQ")
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        EmpresaModulo.objects.create(empresa=cls.otra, modulo=modulo, activo=True)
        cls.rol = RolSistema.objects.create(
            nombre="Gestión de restricciones", codigo="gestion-restricciones-test",
            puede_clinica=True, puede_pacientes=True, puede_expediente_clinico=True,
            puede_restringir_citas_paciente=True, puede_habilitar_citas_paciente=True,
        )
        cls.usuario = get_user_model().objects.create_user(username="restricciones-test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.paciente = Paciente.objects.create(
            empresa=cls.empresa, expediente_codigo="MIA-RESTRICCION", identidad="0801199000345", nombre="Paciente Restricción",
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _cambiar(self, restringir=True, **kwargs):
        return cambiar_restriccion_citas(
            paciente=self.paciente, usuario=self.usuario, restringir=restringir,
            motivo=kwargs.pop("motivo", "Motivo administrativo reservado"), **kwargs,
        )

    def test_restringir_y_habilitar_conserva_expediente_y_audita_cada_cambio(self):
        self.paciente.notas_privadas = "Antecedentes clínicos originales"
        self.paciente.save()
        paciente, cambiado, _ = self._cambiar(observacion="Observación reservada")
        self.assertTrue(cambiado)
        self.assertTrue(paciente.no_agendar)
        self.assertTrue(paciente.activo)
        self.assertEqual(paciente.notas_privadas, "Antecedentes clínicos originales")
        _, cambiado_otra_vez, _ = self._cambiar()
        self.assertFalse(cambiado_otra_vez)
        paciente, _, _ = self._cambiar(False, motivo="Autorización para reanudar citas")
        self.assertFalse(paciente.no_agendar)
        self.assertEqual(paciente.motivo_no_agendar, "")
        historial = list(HistorialRestriccionCitasPaciente.objects.filter(paciente=paciente).order_by("pk"))
        self.assertEqual(len(historial), 2)
        self.assertEqual((historial[0].estado_anterior, historial[0].estado_nuevo), (False, True))
        self.assertEqual((historial[1].estado_anterior, historial[1].estado_nuevo), (True, False))
        self.assertEqual(historial[0].observacion, "Observación reservada")
        self.assertEqual(historial[0].usuario_id, self.usuario.pk)
        self.assertEqual(historial[0].empresa_id, self.empresa.pk)
        self.assertIsNotNone(historial[0].fecha)
        CitaClinica.objects.create(empresa=self.empresa, paciente=paciente, fecha_hora=timezone.now(), motivo="Nueva cita habilitada")

    def test_motivo_y_confirmacion_obligatorios(self):
        url = reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, self.paciente.pk])
        for datos in ({"motivo": "   ", "confirmar": "on"}, {"motivo": "Motivo"}):
            with self.subTest(datos=datos):
                self.assertEqual(self.client.post(url, datos).status_code, 200)
                self.paciente.refresh_from_db()
                self.assertFalse(self.paciente.no_agendar)
        self.assertFalse(HistorialRestriccionCitasPaciente.objects.exists())

    def test_citas_futuras_no_se_cancelan_ni_cuentan_doble(self):
        futuro = timezone.now() + timedelta(days=5)
        clinica = CitaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, fecha_hora=futuro, motivo="Consulta")
        agenda = CitaCliente.objects.create(empresa=self.empresa, paciente=self.paciente, cita_clinica=clinica, fecha_hora=futuro, titulo="Consulta")
        CitaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, fecha_hora=futuro + timedelta(hours=2), motivo="Recordatorio", es_recordatorio_tratamiento=True)
        CitaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, fecha_hora=futuro, motivo="Cancelada", estado="cancelada")
        self.assertEqual(contar_citas_futuras(self.paciente), 2)
        respuesta = self.client.post(
            reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, self.paciente.pk]),
            {"motivo": "No agendar", "confirmar": "on"}, follow=True,
        )
        self.assertContains(respuesta, "2 cita(s) futura(s)")
        clinica.refresh_from_db()
        agenda.refresh_from_db()
        self.assertEqual(clinica.estado, "solicitada")
        self.assertEqual(agenda.estado, "pendiente")
        clinica.fecha_hora += timedelta(days=1)
        clinica.save()
        agenda.estado = "cancelada"
        agenda.save()

    def test_estado_fresco_bloquea_aunque_objeto_paciente_este_en_cache(self):
        cita = CitaClinica(empresa=self.empresa, paciente=self.paciente, fecha_hora=timezone.now(), motivo="Intento nuevo")
        self._cambiar()
        self.assertFalse(self.paciente.no_agendar)
        with self.assertRaisesMessage(ValidationError, MENSAJE_NO_AGENDAR):
            cita.save()
        self.assertFalse(CitaClinica.objects.exists())

    def test_cliente_sin_paciente_tambien_se_bloquea(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente del paciente", rtn="0801199000345")
        self.paciente.cliente = cliente
        self.paciente.save()
        self._cambiar()
        with self.assertRaisesMessage(ValidationError, MENSAJE_NO_AGENDAR):
            CitaCliente.objects.create(empresa=self.empresa, cliente=cliente, fecha_hora=timezone.now(), titulo="Cita por cliente")

    def test_restriccion_no_se_propaga_entre_empresas(self):
        clon = Paciente.objects.create(empresa=self.otra, expediente_codigo="LQ-RESTRICCION", identidad=self.paciente.identidad, nombre=self.paciente.nombre)
        self._cambiar()
        cita = CitaClinica.objects.create(empresa=self.otra, paciente=clon, fecha_hora=timezone.now(), motivo="Consulta")
        self.assertIsNotNone(cita.pk)
        with self.assertRaises(ValidationError):
            CitaClinica.objects.create(empresa=self.otra, paciente=self.paciente, fecha_hora=timezone.now(), motivo="Paciente ajeno")

    def test_acciones_y_motivo_respetan_permisos_separados(self):
        self._cambiar()
        url = reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, self.paciente.pk])
        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertNotContains(pagina, "Motivo administrativo reservado")
        with self.assertRaises(ValidationError) as exc:
            validar_paciente_para_cita(self.paciente.pk, self.empresa.pk, usuario=self.usuario)
        self.assertNotIn("Motivo administrativo reservado", str(exc.exception))
        self.rol.puede_ver_motivo_restriccion_paciente = True
        self.rol.puede_habilitar_citas_paciente = False
        self.rol.save()
        self.assertContains(self.client.get(url), "Motivo administrativo reservado")
        denegado = self.client.post(
            reverse("clinica_habilitar_citas_paciente", args=[self.empresa.slug, self.paciente.pk]),
            {"motivo": "Sin autorización", "confirmar": "on"},
        )
        self.assertRedirects(denegado, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self._cambiar(False, motivo="Sin autorización")
        self.paciente.refresh_from_db()
        self.assertTrue(self.paciente.no_agendar)

    def test_empresa_ajena_no_puede_cambiar_restriccion(self):
        ajeno = Paciente.objects.create(empresa=self.otra, expediente_codigo="AJENO", identidad="0801199000999", nombre="Otro")
        with self.assertRaises(PermissionDenied):
            cambiar_restriccion_citas(paciente=ajeno, usuario=self.usuario, restringir=True, motivo="Intento ajeno")
        respuesta = self.client.post(
            reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, ajeno.pk]),
            {"motivo": "ID ajeno", "confirmar": "on"},
        )
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(HistorialRestriccionCitasPaciente.objects.exists())

    def test_transaccion_revierte_restriccion_si_falla_historial(self):
        with patch("clinica.restricciones_citas.HistorialRestriccionCitasPaciente.objects.create", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                self._cambiar()
        self.paciente.refresh_from_db()
        self.assertFalse(self.paciente.no_agendar)

    def test_tarjeta_restringida_sigue_permitiendo_abrir_expediente(self):
        self._cambiar()
        respuesta = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug]))
        self.assertContains(respuesta, "NO AGENDAR")
        self.assertContains(respuesta, "is-restricted")
        self.assertContains(respuesta, reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk]))
        self.assertEqual(self.client.get(reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])).status_code, 200)

    def test_editar_datos_desde_formulario_anterior_no_restaura_estado_viejo(self):
        paciente_abierto = Paciente.objects.get(pk=self.paciente.pk)
        self._cambiar()
        paciente_abierto.telefono = "99991234"
        paciente_abierto.save()
        paciente_abierto.refresh_from_db()
        self.assertTrue(paciente_abierto.no_agendar)
        self.assertEqual(paciente_abierto.motivo_no_agendar, "Motivo administrativo reservado")
        self.assertEqual(paciente_abierto.telefono, "99991234")
        self.assertEqual(HistorialRestriccionCitasPaciente.objects.filter(paciente=self.paciente).count(), 1)

    def test_app_muestra_restriccion_y_conserva_navegacion_al_habilitar(self):
        self._cambiar()
        for nombre, args in (
            ("clinica_pacientes", [self.empresa.slug]),
            ("clinica_paciente_detalle", [self.empresa.slug, self.paciente.pk]),
        ):
            respuesta = self.client.get(reverse(nombre, args=args), {"app": "1"})
            self.assertContains(respuesta, "NO AGENDAR")
            self.assertNotContains(respuesta, "Motivo administrativo reservado")
        respuesta = self.client.post(
            reverse("clinica_habilitar_citas_paciente", args=[self.empresa.slug, self.paciente.pk]) + "?app=1",
            {"motivo": "Autorización de reanudación", "confirmar": "on"},
        )
        self.assertRedirects(
            respuesta, reverse("clinica_pacientes", args=[self.empresa.slug]) + "?app=1",
        )
        self.paciente.refresh_from_db()
        self.assertFalse(self.paciente.no_agendar)

    def test_directorio_principal_app_regresa_a_su_pestana_pacientes(self):
        url = reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, self.paciente.pk]) + "?app=1&origen=agenda"
        destino = reverse("agenda_mobile", args=[self.empresa.slug]) + "#pacientes-app"
        pagina = self.client.get(url)
        self.assertContains(pagina, f'href="{destino}"')
        respuesta = self.client.post(url, {"motivo": "Prueba desde el directorio app", "confirmar": "on"})
        self.assertRedirects(respuesta, destino, fetch_redirect_response=False)
