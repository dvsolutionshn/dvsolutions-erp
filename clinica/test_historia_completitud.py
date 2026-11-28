"""Una historia pública completa por paciente, sin alterar respuestas recibidas."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import ConfiguracionCRM

from . import test_historia_completa_flujo as flujo
from .historia_completitud import anotar_estados_historia, contexto_estado_historia, historia_general_completada
from .models import Paciente, PreconsultaClinica
from .tokens import generar_token_preconsulta


class HistoriaCompletitudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        flujo.HistoriaCompletaFlujoTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.usuario)

    def registro(self, empresa, paciente, **cambios):
        token, token_hash, preview = generar_token_preconsulta()
        datos = {
            "empresa": empresa, "paciente": paciente, "tipo": "general", "estado": "completada",
            "token_hash": token_hash, "token_preview": preview,
            "fecha_expiracion": timezone.now() + timedelta(days=7), "fecha_completada": timezone.now(),
            "motivo_consulta": "Respuesta pública conservada", "datos_generales": {},
        }
        datos.update(cambios)
        return PreconsultaClinica.objects.create(**datos), reverse("clinica_preconsulta_publica", args=[token])

    def url(self, vista, empresa, paciente, app=False, *args):
        return reverse(vista, args=[empresa.slug, paciente.pk, *args]) + ("?app=1" if app else "")

    def test_solo_una_historia_general_publica_completada_cuenta_como_completa(self):
        empresa, paciente, _cita = self.casos[0]
        self.assertIsNone(historia_general_completada(empresa, paciente))
        self.assertFalse(contexto_estado_historia(empresa, paciente)["expediente_historia_completo"])
        situaciones = (
            {"estado": "pendiente", "fecha_completada": None},
            {"estado": "pendiente", "fecha_expiracion": timezone.now() - timedelta(days=1)},
            {"estado": "revocada"},
            {"tipo": "capilar"},
            {"datos_generales": {"registro_interno": True, "formulario_general": {"diagnostico_medico": "si"}}},
            {"motivo_consulta": "", "datos_generales": {
                "formulario_general_pendiente_doctor": True,
                "formulario_general": {"pendiente_doctor_desde_paso": 4, "motivo_categoria": ["no_aplica"]},
            }},
        )
        for cambios in situaciones:
            with self.subTest(cambios=cambios):
                self.registro(empresa, paciente, **cambios)
                self.assertIsNone(historia_general_completada(empresa, paciente))
        completa, _ = self.registro(empresa, paciente, fecha_expiracion=timezone.now() - timedelta(days=10))
        self.registro(empresa, paciente, estado="pendiente", fecha_completada=None)
        self.assertEqual(historia_general_completada(empresa, paciente).pk, completa.pk)
        contexto = contexto_estado_historia(empresa, paciente)
        self.assertTrue(contexto["expediente_historia_completo"])
        self.assertEqual(contexto["expediente_historia_estado"], "Expediente completo")

    def test_otro_paciente_o_empresa_no_completa_el_expediente_actual(self):
        empresa, paciente, _ = self.casos[0]
        otra_empresa, otro_paciente, _ = self.casos[1]
        self.registro(otra_empresa, otro_paciente)
        self.registro(otra_empresa, paciente)
        self.registro(empresa, paciente, cita=self.casos[1][2])
        self.assertIsNone(historia_general_completada(empresa, paciente))
        with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
            historia_general_completada(otra_empresa, paciente)
        self.assertFalse(contexto_estado_historia(empresa, paciente)["expediente_historia_completo"])

    def test_estado_del_listado_se_anota_en_lote_sin_consultas_por_tarjeta(self):
        empresa, paciente, _ = self.casos[0]
        otro = Paciente.objects.create(empresa=empresa, nombre="Otro paciente QA", expediente_codigo="PENDIENTE-QA")
        self.registro(empresa, paciente)
        with self.assertNumQueries(1):
            anotar_estados_historia(empresa, [paciente, otro])
        self.assertTrue(paciente.expediente_historia_completo)
        self.assertFalse(otro.expediente_historia_completo)
        self.assertEqual(otro.expediente_historia_estado, "Expediente pendiente de terminar")
        with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
            anotar_estados_historia(empresa, [paciente, self.casos[1][1]])

    def test_perfil_muestra_estado_y_boton_segun_historia_en_web_y_app_cuatro_empresas(self):
        for empresa, paciente, _ in self.casos:
            for completo in (False, True):
                if completo:
                    self.registro(empresa, paciente)
                    self.registro(empresa, paciente, estado="pendiente", fecha_completada=None)
                for app in (False, True):
                    with self.subTest(empresa=empresa.slug, completo=completo, app=app):
                        respuesta = self.client.get(self.url("clinica_paciente_detalle", empresa, paciente, app))
                        self.assertEqual(respuesta.status_code, 200)
                        self.assertEqual(respuesta.context["expediente_historia_completo"], completo)
                        self.assertContains(respuesta, "Expediente completo" if completo else "Expediente pendiente de terminar")
                        if completo:
                            self.assertNotContains(respuesta, "Enviar link para completar expediente")
                        else:
                            self.assertContains(respuesta, "Enviar link para completar expediente")

    def test_generar_otro_enlace_se_bloquea_sin_crear_ni_revocar_en_las_cuatro_empresas(self):
        for empresa, paciente, cita in self.casos:
            self.registro(empresa, paciente)
            self.registro(empresa, paciente, estado="pendiente", fecha_completada=None, cita=cita)
            antes = list(PreconsultaClinica.objects.filter(paciente=paciente).values())
            for app in (False, True):
                with self.subTest(empresa=empresa.slug, app=app):
                    respuesta = self.client.post(self.url("clinica_generar_enlace_preconsulta", empresa, paciente, app), {"cita_id": cita.pk})
                    self.assertEqual(respuesta.status_code, 302)
                    self.assertEqual("?app=1" in respuesta["Location"], app)
                    self.assertIn("Expediente completo", " ".join(str(m) for m in get_messages(respuesta.wsgi_request)))
                    self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).values()), antes)

    def test_finalizacion_concurrente_bloquea_generar_dentro_del_bloqueo_del_paciente(self):
        empresa, paciente, cita = self.casos[0]
        self.registro(empresa, paciente, estado="pendiente", fecha_completada=None, cita=cita)
        completa, _ = self.registro(empresa, paciente)
        antes = list(PreconsultaClinica.objects.filter(paciente=paciente).values())
        with patch("clinica.views.historia_general_completada", side_effect=[None, completa]):
            respuesta = self.client.post(self.url("clinica_generar_enlace_preconsulta", empresa, paciente), {"cita_id": cita.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).values()), antes)

    def test_registro_basico_o_interno_no_impide_enviar_el_link_pendiente(self):
        empresa, paciente, cita = self.casos[0]
        self.registro(empresa, paciente, datos_generales={"registro_interno": True})
        self.registro(empresa, paciente, motivo_consulta="", datos_generales={
            "formulario_general_pendiente_doctor": True,
            "formulario_general": {"pendiente_doctor_desde_paso": 4},
        })
        respuesta = self.client.post(self.url("clinica_generar_enlace_preconsulta", empresa, paciente), {"cita_id": cita.pk})
        self.assertEqual(respuesta.status_code, 200)
        enlace = respuesta.context["enlace_publico"]
        self.assertContains(Client().get(urlsplit(enlace).path), "Motivo de Consulta")
        pendiente = respuesta.context["preconsulta"]
        self.assertEqual(pendiente.estado, "pendiente")

    @patch("clinica.views.enviar_plantilla_preconsulta_whatsapp")
    def test_no_reenvia_whatsapp_si_el_paciente_ya_completo_otro_link(self, enviar):
        empresa, paciente, _ = self.casos[0]
        ConfiguracionCRM.objects.create(empresa=empresa, whatsapp_activo=True)
        generada = self.client.post(self.url("clinica_generar_enlace_preconsulta", empresa, paciente))
        pendiente = generada.context["preconsulta"]
        enlace = generada.context["enlace_publico"]
        self.registro(empresa, paciente)
        antes = PreconsultaClinica.objects.values().get(pk=pendiente.pk)
        for app in (False, True):
            with self.subTest(app=app):
                ruta = self.url("clinica_enviar_preconsulta_whatsapp", empresa, paciente, app, pendiente.pk)
                respuesta = self.client.post(ruta, {"enlace_publico": enlace})
                self.assertEqual(respuesta.status_code, 302)
        enviar.assert_not_called()
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=pendiente.pk), antes)

    def test_token_pendiente_antiguo_no_permite_otro_guardado_ni_cambia_respuestas(self):
        empresa, paciente, _ = self.casos[0]
        antiguo, enlace = self.registro(empresa, paciente, estado="pendiente", fecha_completada=None,
                                      motivo_consulta="Borrador anterior conservado")
        self.registro(empresa, paciente)
        antes = list(PreconsultaClinica.objects.filter(paciente=paciente).values())
        cliente = Client()
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                respuesta = getattr(cliente, metodo)(enlace, {"motivo_consulta": "No debe reemplazar antecedentes"} if metodo == "post" else {})
                self.assertContains(respuesta, "Información recibida")
                self.assertNotContains(respuesta, 'id="preconsultaForm"')
                self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).values()), antes)
        actividad = reverse("clinica_preconsulta_actividad", args=[enlace.rstrip("/").split("/")[-1]])
        self.assertEqual(cliente.post(actividad, {"evento": "progreso", "paso": "3"}).status_code, 410)
        self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).values()), antes)
        antiguo.refresh_from_db()
        self.assertEqual(antiguo.estado, "pendiente")

    def test_historia_general_completa_no_impide_un_formulario_de_especialidad(self):
        empresa, paciente, _ = self.casos[0]
        completa, _ = self.registro(empresa, paciente)
        especialidad, enlace = self.registro(empresa, paciente, tipo="capilar", estado="pendiente", fecha_completada=None)
        cliente = Client()
        self.assertContains(cliente.get(enlace), "Motivo de Consulta")
        self.assertContains(cliente.post(enlace, flujo.HistoriaCompletaFlujoTests.respuestas(self)), "Información recibida")
        especialidad.refresh_from_db()
        self.assertEqual(especialidad.estado, "completada")
        self.assertEqual(historia_general_completada(empresa, paciente).pk, completa.pk)

    def test_finalizacion_concurrente_impide_guardar_segundo_formulario_dentro_del_bloqueo(self):
        empresa, paciente, _ = self.casos[0]
        pendiente, enlace = self.registro(empresa, paciente, estado="pendiente", fecha_completada=None)
        completa, _ = self.registro(empresa, paciente)
        antes = list(PreconsultaClinica.objects.filter(paciente=paciente).values())
        with patch("clinica.views.historia_general_completada", side_effect=[None, completa]):
            respuesta = Client().post(enlace, flujo.HistoriaCompletaFlujoTests.respuestas(self))
        self.assertContains(respuesta, "Información recibida")
        self.assertNotContains(respuesta, 'id="preconsultaForm"')
        self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).values()), antes)
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, "pendiente")

    def test_completar_el_link_actualiza_estado_y_los_reintentos_no_reemplazan_el_envio(self):
        empresa, paciente, _ = self.casos[0]
        generada = self.client.post(self.url("clinica_generar_enlace_preconsulta", empresa, paciente))
        enlace = urlsplit(generada.context["enlace_publico"]).path
        cliente = Client()
        respuestas = flujo.HistoriaCompletaFlujoTests.respuestas(self)
        self.assertContains(cliente.post(enlace, respuestas), "Información recibida")
        original = deepcopy(PreconsultaClinica.objects.values().get(pk=generada.context["preconsulta"].pk))
        self.assertTrue(contexto_estado_historia(empresa, paciente)["expediente_historia_completo"])
        self.assertContains(cliente.post(enlace, {**respuestas, "motivo_consulta": "Intento duplicado"}), "Información recibida")
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=original["id"]), original)
        for app in (False, True):
            perfil = self.client.get(self.url("clinica_paciente_detalle", empresa, paciente, app))
            self.assertContains(perfil, "Expediente completo")
            self.assertNotContains(perfil, "Enviar link para completar expediente")
