from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AccionOnix,
    ConversacionOnix,
    Empresa,
    EmpresaModulo,
    MensajeOnix,
    Modulo,
    Usuario,
)

from .models import SesionOnixMovil


@override_settings(
    ONIX_ENABLED=True,
    ONIX_ALLOWED_COMPANY_SLUGS=["demo_1"],
    ONIX_MOBILE_TOKEN_DAYS=30,
    ONIX_MOBILE_LOGIN_MAX_ATTEMPTS=5,
)
class OnixMobileApiTests(TestCase):
    password = "ClaveSegura123!"

    def setUp(self):
        cache.clear()
        self.empresa = Empresa.objects.create(
            nombre="Demo 1",
            slug="demo_1",
            rtn="08011999008801",
        )
        self.usuario = Usuario.objects.create_user(
            username="onix_mobile_user",
            email="onix@example.com",
            password=self.password,
            empresa=self.empresa,
            es_administrador_empresa=True,
        )

    def _login(self, **cambios):
        datos = {
            "empresa": self.empresa.slug,
            "usuario": self.usuario.username,
            "password": self.password,
            "dispositivo": "iPhone de pruebas",
        }
        datos.update(cambios)
        return self.client.post(
            reverse("onix_mobile:login"),
            datos,
            content_type="application/json",
        )

    def _token(self):
        response = self._login()
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def _authorization(self, token=None):
        return {"HTTP_AUTHORIZATION": f"Bearer {token or self._token()}"}

    def test_login_entrega_token_opaco_y_bootstrap_sin_guardar_token_crudo(self):
        response = self._login()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["token"].startswith("onx_"))
        self.assertEqual(payload["bootstrap"]["company"]["slug"], "demo_1")
        self.assertEqual(payload["bootstrap"]["assistant"]["name"], "Onix")
        self.assertEqual(payload["bootstrap"]["assistant"]["mode"], "guided")
        self.assertFalse(payload["bootstrap"]["capabilities"]["ai"])
        self.assertGreaterEqual(len(payload["bootstrap"]["categories"]), 10)
        sesion = SesionOnixMovil.objects.get()
        self.assertNotEqual(sesion.token_hash, payload["token"])
        self.assertEqual(sesion.token_hash, SesionOnixMovil.calcular_hash(payload["token"]))

    @override_settings(ONIX_ENABLED=True, OPENAI_API_KEY="test-key")
    def test_bootstrap_muestra_ia_y_categorias_segun_modulos_y_permisos(self):
        facturacion = Modulo.objects.create(nombre="Facturacion", codigo="facturacion")
        agenda = Modulo.objects.create(nombre="Agenda de citas", codigo="agenda_citas")
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=facturacion, activo=True)
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=agenda, activo=True)

        response = self._login()

        self.assertEqual(response.status_code, 200)
        bootstrap = response.json()["bootstrap"]
        self.assertEqual(bootstrap["assistant"]["mode"], "ai")
        self.assertEqual(bootstrap["assistant"]["status"], "IA activa")
        self.assertTrue(bootstrap["capabilities"]["ai"])
        self.assertTrue(bootstrap["capabilities"]["calendar"])
        self.assertTrue(bootstrap["capabilities"]["payments"])
        categorias = {item["id"]: item["status"] for item in bootstrap["categories"]}
        self.assertEqual(categorias["calendario"], "available")
        self.assertEqual(categorias["pagos"], "available")

    def test_login_rechaza_credenciales_invalidas_sin_revelar_el_campo_incorrecto(self):
        response = self._login(password="incorrecta")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credentials")
        self.assertNotIn("usuario", response.json()["error"].lower())
        self.assertEqual(SesionOnixMovil.objects.count(), 0)

    @override_settings(ONIX_MOBILE_LOGIN_MAX_ATTEMPTS=2)
    def test_login_limita_intentos_repetidos(self):
        self._login(password="incorrecta")
        self._login(password="incorrecta")

        response = self._login(password="incorrecta")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "too_many_attempts")

    def test_bootstrap_requiere_bearer_valido(self):
        sin_token = self.client.get(reverse("onix_mobile:bootstrap"))
        con_token = self.client.get(
            reverse("onix_mobile:bootstrap"),
            **self._authorization(),
        )

        self.assertEqual(sin_token.status_code, 401)
        self.assertEqual(con_token.status_code, 200)
        self.assertEqual(con_token.json()["bootstrap"]["user"]["username"], self.usuario.username)

    def test_token_vencido_no_autoriza(self):
        token = self._token()
        SesionOnixMovil.objects.update(expira_en=timezone.now() - timedelta(seconds=1))

        response = self.client.get(
            reverse("onix_mobile:bootstrap"),
            **self._authorization(token),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_session")

    def test_historial_devuelve_solo_la_conversacion_del_usuario_y_empresa(self):
        conversacion = ConversacionOnix.objects.create(empresa=self.empresa, usuario=self.usuario)
        MensajeOnix.objects.create(
            conversacion=conversacion,
            rol=MensajeOnix.ROL_USUARIO,
            contenido="Muestrame las facturas",
        )
        MensajeOnix.objects.create(
            conversacion=conversacion,
            rol=MensajeOnix.ROL_ASISTENTE,
            contenido="Estas son las facturas recientes.",
        )
        intruso = Usuario.objects.create_user(
            username="otro_mobile",
            password=self.password,
            empresa=self.empresa,
        )
        otra = ConversacionOnix.objects.create(empresa=self.empresa, usuario=intruso)
        MensajeOnix.objects.create(
            conversacion=otra,
            rol=MensajeOnix.ROL_ASISTENTE,
            contenido="Mensaje privado ajeno",
        )

        response = self.client.get(
            reverse("onix_mobile:history"),
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 200)
        contenidos = [mensaje["content"] for mensaje in response.json()["messages"]]
        self.assertEqual(contenidos, ["Muestrame las facturas", "Estas son las facturas recientes."])

    @patch("onix_mobile.views.responder_consulta")
    def test_chat_reutiliza_onix_del_erp_y_adapta_la_accion_a_la_api_movil(self, responder):
        responder.return_value = {
            "answer": "Prepare la factura para que la confirmes.",
            "assistant_mode": "ai",
            "actions": [
                {
                    "id": "d22bdd17-3eac-4efc-b7ec-7c6b0e02c76f",
                    "type": "crear_borrador_factura",
                    "status": "pendiente",
                    "decision_url": "/demo_1/dashboard/asistente/acciones/id/",
                    "total": "1150.00",
                }
            ],
        }

        response = self.client.post(
            reverse("onix_mobile:chat"),
            {"pregunta": "Crea una factura de prueba"},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        accion = payload["message"]["actions"][0]
        self.assertNotIn("decision_url", accion)
        self.assertEqual(
            accion["endpoint"],
            "/api/onix/mobile/v1/actions/d22bdd17-3eac-4efc-b7ec-7c6b0e02c76f/",
        )
        responder.assert_called_once_with(
            "Crea una factura de prueba",
            "onix-mobile://chat",
            empresa=self.empresa,
            usuario=self.usuario,
        )

    def test_cancelar_accion_respeta_usuario_y_empresa_del_token(self):
        conversacion = ConversacionOnix.objects.create(empresa=self.empresa, usuario=self.usuario)
        accion = AccionOnix.objects.create(
            empresa=self.empresa,
            usuario=self.usuario,
            conversacion=conversacion,
            tipo=AccionOnix.TIPO_CREAR_BORRADOR_FACTURA,
            datos={},
            vista_previa={"title": "Factura de prueba", "total": "100.00"},
            expira_en=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.post(
            reverse("onix_mobile:action", args=[accion.id]),
            {"decision": "cancelar"},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 200)
        accion.refresh_from_db()
        self.assertEqual(accion.estado, AccionOnix.ESTADO_CANCELADA)
        self.assertEqual(response.json()["action"]["status"], "cancelada")

    def test_logout_revoca_sesion(self):
        token = self._token()

        salida = self.client.post(
            reverse("onix_mobile:logout"),
            {},
            content_type="application/json",
            **self._authorization(token),
        )
        siguiente = self.client.get(
            reverse("onix_mobile:bootstrap"),
            **self._authorization(token),
        )

        self.assertEqual(salida.status_code, 200)
        self.assertEqual(siguiente.status_code, 401)
        self.assertIsNotNone(SesionOnixMovil.objects.get().revocada_en)
