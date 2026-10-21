from smtplib import SMTPException
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .access_tokens import emitir_token_acceso, enviar_correo_acceso
from .models import Empresa, TokenAccesoUsuario, Usuario


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvitationDeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Rumi", slug="rumi-invitaciones", rtn="08011999000871")
        cls.admin = Usuario.objects.create_superuser(username="master-invitaciones", password="SoloPruebas2026")
        cls.usuario = Usuario.objects.create_user(
            username="jorge-invitaciones", email="jorge.palma@rumi.hn", empresa=cls.empresa,
            is_active=False,
        )

    def setUp(self):
        self.request = RequestFactory().get("/control/", HTTP_HOST="testserver")
        self.client.force_login(self.admin)

    def emitir(self, tipo=TokenAccesoUsuario.TIPO_INVITACION):
        return emitir_token_acceso(self.usuario, tipo, request=self.request, horas=48)

    def enviar(self, raw, token):
        return enviar_correo_acceso(self.request, self.usuario, raw, token, token.tipo)

    def reenviar(self):
        return self.client.post(reverse("superadmin_usuario_reenviar_invitacion", args=[self.usuario.pk]))

    def test_reenvio_fallido_conserva_enlace_recibido_y_muestra_error(self):
        raw, anterior = self.emitir()
        self.enviar(raw, anterior)
        with patch("core.access_tokens.EmailMultiAlternatives.send", side_effect=SMTPException("Fallo simulado")):
            with self.assertLogs("core.views", level="ERROR"):
                response = self.reenviar()
        self.assertEqual(response.status_code, 302)
        anterior.refresh_from_db()
        self.assertFalse(anterior.revocado)
        nuevo = TokenAccesoUsuario.objects.filter(usuario=self.usuario).exclude(pk=anterior.pk).get()
        self.assertTrue(nuevo.revocado)
        self.assertEqual(nuevo.estado_envio, TokenAccesoUsuario.ENVIO_ERROR)
        self.assertEqual(nuevo.estado_display, "Error de envio")
        self.assertIsNone(nuevo.fecha_envio)
        listado = self.client.get(reverse("superadmin_usuarios"))
        self.assertContains(listado, "Error de envio")
        self.assertContains(listado, "No se envio esta invitacion")
        activation = Client().post(reverse("establecer_acceso", args=[raw]), {
            "new_password1": "ClavePersonalJorgeSegura2026",
            "new_password2": "ClavePersonalJorgeSegura2026",
        })
        self.assertEqual(activation.status_code, 302)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_active)

    def test_reenvio_exitoso_revoca_solo_despues_de_aceptacion(self):
        raw, anterior = self.emitir()
        self.enviar(raw, anterior)
        self.assertEqual(self.reenviar().status_code, 302)
        anterior.refresh_from_db()
        nuevo = TokenAccesoUsuario.objects.filter(usuario=self.usuario).exclude(pk=anterior.pk).get()
        self.assertTrue(anterior.revocado)
        self.assertFalse(nuevo.revocado)
        self.assertEqual(nuevo.estado_envio, TokenAccesoUsuario.ENVIO_ENVIADO)
        self.assertIsNotNone(nuevo.fecha_envio)
        self.assertEqual(Client().get(reverse("establecer_acceso", args=[raw])).status_code, 400)
        self.assertEqual(len(mail.outbox), 2)

    def test_alta_con_fallo_de_correo_conserva_cuenta_y_registra_error(self):
        with patch("core.access_tokens.EmailMultiAlternatives.send", side_effect=SMTPException("Fallo simulado")):
            with self.assertLogs("core.views", level="ERROR"):
                response = self.client.post(reverse("superadmin_usuario_create"), {
                    "first_name": "Nueva", "last_name": "Persona", "email": "otra.persona@rumi.hn",
                    "empresa": self.empresa.pk, "modo_creacion": "invitacion",
                })
        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(email="otra.persona@rumi.hn")
        self.assertFalse(usuario.is_active)
        self.assertFalse(usuario.has_usable_password())
        token = usuario.tokens_acceso.get()
        self.assertEqual(token.estado_envio, TokenAccesoUsuario.ENVIO_ERROR)
        self.assertFalse(token.vigente)

    def test_servidor_que_no_acepta_mensaje_registra_fallo(self):
        raw, token = self.emitir()
        with patch("core.access_tokens.EmailMultiAlternatives.send", return_value=0):
            with self.assertRaises(RuntimeError):
                self.enviar(raw, token)
        token.refresh_from_db()
        self.assertEqual(token.estado_envio, TokenAccesoUsuario.ENVIO_ERROR)
        self.assertTrue(token.revocado)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_consola_no_se_presenta_como_invitacion_enviada(self):
        raw, token = self.emitir()
        with self.assertRaisesMessage(ImproperlyConfigured, "Configura SMTP"):
            self.enviar(raw, token)
        token.refresh_from_db()
        self.assertEqual(token.estado_envio, TokenAccesoUsuario.ENVIO_ERROR)
        self.assertTrue(token.revocado)

    @override_settings(ACCESS_PUBLIC_BASE_URL="https://erp.example.com")
    def test_invitacion_a_dominio_externo_usa_url_publica_y_correo_multipart(self):
        raw, token = self.emitir()
        url = self.enviar(raw, token)
        self.assertTrue(url.startswith("https://erp.example.com/acceso/establecer/"))
        self.assertEqual(mail.outbox[0].to, ["jorge.palma@rumi.hn"])
        self.assertIn(url, mail.outbox[0].body)
        self.assertIn("text/html", [alternative.mimetype for alternative in mail.outbox[0].alternatives])
        token.refresh_from_db()
        self.assertEqual(token.estado_display, "Pendiente")
        self.assertNotEqual(token.token_hash, raw)

    def test_superusuario_pendiente_tambien_puede_reenviar_desde_listado(self):
        self.usuario.is_superuser = True
        self.usuario.save(update_fields=["is_superuser"])
        response = self.client.get(reverse("superadmin_usuarios"))
        self.assertContains(response, reverse("superadmin_usuario_reenviar_invitacion", args=[self.usuario.pk]))

    def test_no_se_reinvita_cuenta_bloqueada_con_contrasena(self):
        self.usuario.set_password("ContrasenaExistente2026")
        self.usuario.save(update_fields=["password"])
        anterior = self.usuario.password
        self.assertEqual(self.reenviar().status_code, 302)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.password, anterior)
        self.assertFalse(self.usuario.is_active)
        self.assertFalse(self.usuario.tokens_acceso.exists())

    def test_no_se_reinvita_cuenta_retirada_por_post_directo(self):
        self.usuario.retirado_control = True
        self.usuario.save(update_fields=["retirado_control"])
        self.assertEqual(self.reenviar().status_code, 302)
        self.assertFalse(self.usuario.tokens_acceso.exists())

    def test_recuperacion_fallida_conserva_el_enlace_anterior(self):
        self.usuario.is_active = True
        self.usuario.set_password("ContrasenaExistente2026")
        self.usuario.save(update_fields=["password", "is_active"])
        raw, anterior = self.emitir(TokenAccesoUsuario.TIPO_RECUPERACION)
        self.enviar(raw, anterior)
        nuevo_raw, nuevo = self.emitir(TokenAccesoUsuario.TIPO_RECUPERACION)
        anterior.refresh_from_db()
        self.assertFalse(anterior.revocado)
        with patch("core.access_tokens.EmailMultiAlternatives.send", side_effect=SMTPException("Fallo simulado")):
            with self.assertRaises(SMTPException):
                self.enviar(nuevo_raw, nuevo)
        anterior.refresh_from_db()
        self.assertFalse(anterior.revocado)
        self.assertEqual(Client().get(reverse("establecer_acceso", args=[raw])).status_code, 200)
