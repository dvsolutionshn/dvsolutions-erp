from datetime import timedelta
import secrets

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.access_security import firma_estado_usuario
from core.access_tokens import hash_token_acceso
from core.models import Empresa, TokenAccesoUsuario, Usuario


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AccessSecurityTests(TestCase):
    password = "ClavePersonalSegura2026!"

    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Rumi pruebas de acceso", slug="rumi-access-security", rtn="ACCESS-SECURITY-001"
        )
        self.admin = Usuario.objects.create_superuser(
            username="access-security-admin", email="admin@example.test", password="AdminSegura2026!"
        )
        self.usuario = Usuario.objects.create_user(
            username="access-security-user", email="jorge.palma@rumi.hn",
            password="ClaveAnteriorSegura2026!", empresa=self.empresa,
        )

    def token(self, tipo=TokenAccesoUsuario.TIPO_RECUPERACION, **extra):
        raw = secrets.token_urlsafe(32)
        values = {
            "usuario": self.usuario,
            "tipo": tipo,
            "token_hash": hash_token_acceso(raw),
            "token_preview": "test-token",
            "estado_usuario_hash": firma_estado_usuario(self.usuario),
            "fecha_expiracion": timezone.now() + timedelta(hours=2),
            **extra,
        }
        token = TokenAccesoUsuario.objects.create(**values)
        return reverse("establecer_acceso", args=[raw]), token

    def consumir(self, url, password=None):
        password = password or self.password
        return self.client.post(url, {"new_password1": password, "new_password2": password})

    def editar(self, **extra):
        self.client.force_login(self.admin)
        data = {
            "username": self.usuario.username,
            "email": self.usuario.email,
            "empresa": self.empresa.pk,
            "is_active": "on" if self.usuario.is_active else "",
            **extra,
        }
        return self.client.post(reverse("superadmin_usuario_edit", args=[self.usuario.pk]), data)

    def assert_rechazado_sin_cambios(self, url, token):
        self.usuario.refresh_from_db()
        estado_anterior = (self.usuario.password, self.usuario.is_active, self.usuario.email)
        response = self.consumir(url)
        self.assertEqual(response.status_code, 400)
        self.usuario.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual((self.usuario.password, self.usuario.is_active, self.usuario.email), estado_anterior)
        self.assertIsNone(token.fecha_uso)

    def test_invitation_activates_without_reusing_link(self):
        self.usuario.is_active = False
        self.usuario.set_unusable_password()
        self.usuario.save(update_fields=["is_active", "password"])
        url, token = self.token(TokenAccesoUsuario.TIPO_INVITACION)
        response = self.consumir(url)
        self.assertRedirects(response, reverse("empresa_login", args=[self.empresa.slug]))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_active)
        self.assertTrue(self.usuario.check_password(self.password))
        token.refresh_from_db()
        self.assertIsNotNone(token.fecha_uso)
        self.assertEqual(self.consumir(url, "OtraClaveSegura2026!").status_code, 400)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(self.password))

    def test_recovery_changes_password_and_revokes_other_links(self):
        url, token = self.token()
        _, otro = self.token()
        response = self.consumir(url)
        self.assertEqual(response.status_code, 302)
        self.usuario.refresh_from_db()
        token.refresh_from_db()
        otro.refresh_from_db()
        self.assertTrue(self.usuario.check_password(self.password))
        self.assertTrue(self.usuario.is_active)
        self.assertIsNotNone(token.fecha_uso)
        self.assertTrue(otro.revocado)

    def test_admin_deactivation_revokes_link_and_cannot_be_undone_with_it(self):
        url, token = self.token()
        self.assertEqual(self.editar(is_active="").status_code, 302)
        token.refresh_from_db()
        self.assertTrue(token.revocado)
        self.assert_rechazado_sin_cambios(url, token)
        self.assertFalse(self.usuario.is_active)

    def test_active_account_email_change_revokes_previous_mail_link(self):
        url, token = self.token()
        self.assertEqual(self.editar(email="new-address@example.test").status_code, 302)
        token.refresh_from_db()
        self.assertTrue(token.revocado)
        self.assert_rechazado_sin_cambios(url, token)
        self.assertEqual(self.usuario.email, "new-address@example.test")

    def test_admin_password_change_revokes_link(self):
        url, token = self.token()
        password = "NuevaClaveAdministrativa2026!"
        self.assertEqual(self.editar(password1=password, password2=password).status_code, 302)
        token.refresh_from_db()
        self.assertTrue(token.revocado)
        self.assert_rechazado_sin_cambios(url, token)
        self.assertTrue(self.usuario.check_password(password))

    def test_unrelated_account_edit_keeps_valid_link(self):
        url, token = self.token()
        self.assertEqual(self.editar(first_name="Jorge").status_code, 302)
        token.refresh_from_db()
        self.assertFalse(token.revocado)
        self.assertEqual(self.consumir(url).status_code, 302)

    def test_email_changed_outside_panel_invalidates_link_by_signature(self):
        url, token = self.token()
        Usuario.objects.filter(pk=self.usuario.pk).update(email="other-owner@example.test")
        self.assert_rechazado_sin_cambios(url, token)

    def test_password_changed_outside_panel_invalidates_link_by_signature(self):
        url, token = self.token()
        self.usuario.set_password("CambioExternoSeguro2026!")
        self.usuario.save(update_fields=["password"])
        self.assert_rechazado_sin_cambios(url, token)

    def test_recovery_issued_for_inactive_account_cannot_activate_it(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        url, token = self.token()
        self.assert_rechazado_sin_cambios(url, token)

    def test_invitation_cannot_replace_existing_usable_password(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        url, token = self.token(TokenAccesoUsuario.TIPO_INVITACION)
        self.assert_rechazado_sin_cambios(url, token)

    def test_retired_account_cannot_consume_a_link(self):
        self.usuario.retirado_control = True
        self.usuario.save(update_fields=["retirado_control"])
        url, token = self.token()
        self.assert_rechazado_sin_cambios(url, token)

    def test_revoked_expired_used_or_legacy_links_are_rejected(self):
        cases = [
            {"revocado": True},
            {"fecha_expiracion": timezone.now() - timedelta(seconds=1)},
            {"fecha_uso": timezone.now()},
            {"estado_usuario_hash": ""},
        ]
        for values in cases:
            with self.subTest(values=values):
                url, token = self.token(**values)
                response = self.consumir(url)
                self.assertEqual(response.status_code, 400)
                self.usuario.refresh_from_db()
                self.assertFalse(self.usuario.check_password(self.password))

    def test_invalid_password_does_not_consume_link(self):
        url, token = self.token()
        response = self.client.post(url, {"new_password1": self.password, "new_password2": "different"})
        self.assertEqual(response.status_code, 200)
        token.refresh_from_db()
        self.assertIsNone(token.fecha_uso)
        self.assertEqual(self.consumir(url).status_code, 302)

    def test_form_opened_before_email_change_cannot_be_submitted(self):
        url, token = self.token()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        Usuario.objects.filter(pk=self.usuario.pk).update(email="updated@example.test")
        self.assert_rechazado_sin_cambios(url, token)
