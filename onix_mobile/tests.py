from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

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
from facturacion.models import Cliente, Factura

from .models import ConexionOnixExterna, PerfilOnixPersonal, SesionOnixMovil, SolicitudOAuthOnix


@override_settings(
    ONIX_ENABLED=True,
    ONIX_ALLOWED_COMPANY_SLUGS=["demo_1"],
    ONIX_MOBILE_TOKEN_DAYS=30,
    ONIX_MOBILE_LOGIN_MAX_ATTEMPTS=5,
    ONIX_CONNECTION_ENCRYPTION_KEY="VXz_sMKZWREYswmeI4uDGryhpjFPz7PK_fFjjZjj0FY=",
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

    def test_conexiones_registra_whatsapp_normalizado_y_preferencias(self):
        token = self._token()
        response = self.client.post(
            reverse("onix_mobile:personal_profile"),
            {
                "whatsapp": "9999-1234",
                "whatsapp_opt_in": True,
                "timezone": "America/Tegucigalpa",
                "reminder_channel": "whatsapp",
            },
            content_type="application/json",
            **self._authorization(token),
        )

        self.assertEqual(response.status_code, 200)
        perfil = PerfilOnixPersonal.objects.get(usuario=self.usuario)
        self.assertEqual(perfil.telefono_whatsapp, "+50499991234")
        self.assertTrue(perfil.acepta_notificaciones_whatsapp)
        self.assertEqual(perfil.canal_recordatorio, "whatsapp")
        servicios = {item["id"]: item for item in response.json()["connections"]["services"]}
        self.assertEqual(servicios["whatsapp"]["status"], "pendiente")
        self.assertEqual(servicios["email"]["account"], self.usuario.email)

    def test_conexiones_rechaza_opt_in_sin_numero(self):
        response = self.client.post(
            reverse("onix_mobile:personal_profile"),
            {
                "whatsapp": "",
                "whatsapp_opt_in": True,
                "timezone": "America/Tegucigalpa",
                "reminder_channel": "app",
            },
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("numero", response.json()["error"])

    def test_google_start_informa_cuando_faltan_credenciales(self):
        response = self.client.post(
            reverse("onix_mobile:google_connection_start"),
            {},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "google_not_configured")
        self.assertFalse(SolicitudOAuthOnix.objects.exists())

    @override_settings(
        DEBUG=False,
        ONIX_CONNECTION_ENCRYPTION_KEY="",
        ONIX_GOOGLE_CLIENT_ID="google-client-test",
        ONIX_GOOGLE_CLIENT_SECRET="google-secret-test",
    )
    def test_google_start_exige_cifrado_de_tokens_en_produccion(self):
        response = self.client.post(
            reverse("onix_mobile:google_connection_start"),
            {},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "connection_security_not_configured")
        self.assertFalse(SolicitudOAuthOnix.objects.exists())

    @override_settings(
        ONIX_GOOGLE_CLIENT_ID="google-client-test",
        ONIX_GOOGLE_CLIENT_SECRET="google-secret-test",
        ONIX_GOOGLE_REDIRECT_URI="https://erp.test/api/onix/mobile/v1/connections/google/callback/",
    )
    def test_google_start_crea_estado_de_un_solo_uso_y_url_segura(self):
        response = self.client.post(
            reverse("onix_mobile:google_connection_start"),
            {},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlparse(response.json()["authorization_url"]).query)
        self.assertEqual(query["client_id"], ["google-client-test"])
        self.assertEqual(query["redirect_uri"], ["https://erp.test/api/onix/mobile/v1/connections/google/callback/"])
        self.assertIn("https://www.googleapis.com/auth/calendar.events", query["scope"][0])
        solicitud = SolicitudOAuthOnix.objects.get()
        self.assertNotEqual(solicitud.estado_hash, query["state"][0])
        self.assertEqual(
            solicitud.estado_hash,
            SolicitudOAuthOnix.calcular_hash(query["state"][0]),
        )

    @override_settings(
        ONIX_GOOGLE_CLIENT_ID="google-client-test",
        ONIX_GOOGLE_CLIENT_SECRET="google-secret-test",
        ONIX_GOOGLE_REDIRECT_URI="https://erp.test/api/onix/mobile/v1/connections/google/callback/",
    )
    @patch("onix_mobile.views.intercambiar_codigo_google")
    def test_callback_google_cifra_tokens_y_conecta_solo_usuario_correcto(self, intercambiar):
        estado, solicitud = SolicitudOAuthOnix.emitir(
            usuario=self.usuario,
            empresa=self.empresa,
            proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
        )
        intercambiar.return_value = {
            "access_token": "access-secreto-google",
            "refresh_token": "refresh-secreto-google",
            "expires_at": timezone.now() + timedelta(hours=1),
            "scope": ["openid", "https://www.googleapis.com/auth/calendar.events"],
            "email": "onix.calendar@example.com",
            "name": "Cuenta Calendario",
            "subject": "google-subject-123",
        }

        response = self.client.get(
            reverse("onix_mobile:google_connection_callback"),
            {"state": estado, "code": "codigo-google"},
        )

        self.assertEqual(response.status_code, 200)
        conexion = ConexionOnixExterna.objects.get(usuario=self.usuario, empresa=self.empresa)
        self.assertEqual(conexion.estado, ConexionOnixExterna.CONECTADA)
        self.assertEqual(conexion.cuenta_externa, "onix.calendar@example.com")
        self.assertNotIn("access-secreto-google", conexion.token_acceso_cifrado)
        self.assertNotIn("refresh-secreto-google", conexion.token_refresco_cifrado)
        self.assertEqual(conexion.token_acceso(), "access-secreto-google")
        self.assertEqual(conexion.token_refresco(), "refresh-secreto-google")
        solicitud.refresh_from_db()
        self.assertIsNotNone(solicitud.consumida_en)
        segunda = self.client.get(
            reverse("onix_mobile:google_connection_callback"),
            {"state": estado, "code": "codigo-repetido"},
        )
        self.assertEqual(segunda.status_code, 400)
        intercambiar.assert_called_once_with("codigo-google")

    def test_desconectar_google_elimina_tokens_sin_afectar_otra_empresa(self):
        conexion = ConexionOnixExterna.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            proveedor=ConexionOnixExterna.GOOGLE_CALENDAR,
            estado=ConexionOnixExterna.CONECTADA,
        )
        conexion.guardar_tokens(acceso="access", refresco="refresh")
        conexion.save()

        response = self.client.post(
            reverse("onix_mobile:disconnect_connection", args=["google_calendar"]),
            {},
            content_type="application/json",
            **self._authorization(),
        )

        self.assertEqual(response.status_code, 200)
        conexion.refresh_from_db()
        self.assertEqual(conexion.estado, ConexionOnixExterna.REVOCADA)
        self.assertEqual(conexion.token_acceso_cifrado, "")
        self.assertEqual(conexion.token_refresco_cifrado, "")

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

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-1.7 onix-test")
    def test_descarga_pdf_de_factura_con_token_y_empresa_correctos(self, generar_pdf):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Cliente PDF Onix",
            rtn="08011999008802",
        )
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=cliente,
            vendedor=self.usuario,
            estado="borrador",
            subtotal="100.00",
            impuesto="15.00",
            total="115.00",
            total_lempiras="115.00",
        )

        sin_token = self.client.get(
            reverse("onix_mobile:invoice_pdf", args=[factura.id])
        )
        response = self.client.get(
            reverse("onix_mobile:invoice_pdf", args=[factura.id]),
            **self._authorization(),
        )
        otra_empresa = Empresa.objects.create(
            nombre="Empresa PDF privada",
            slug="empresa-pdf-privada",
            rtn="08011999008803",
        )
        cliente_privado = Cliente.objects.create(
            empresa=otra_empresa,
            nombre="Cliente privado",
            rtn="08011999008804",
        )
        factura_privada = Factura.objects.create(
            empresa=otra_empresa,
            cliente=cliente_privado,
            estado="borrador",
        )
        otra_empresa_response = self.client.get(
            reverse("onix_mobile:invoice_pdf", args=[factura_privada.id]),
            **self._authorization(),
        )

        self.assertEqual(sin_token.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(otra_empresa_response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(response.content.startswith(b"%PDF"))
        generar_pdf.assert_called_once()

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
