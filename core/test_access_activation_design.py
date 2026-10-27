"""Activation-page contracts: usable forms, safe failures and private links."""
from datetime import timedelta
from html.parser import HTMLParser
from io import BytesIO
import secrets
from tempfile import TemporaryDirectory

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access_security import firma_estado_usuario
from .access_tokens import hash_token_acceso
from .models import Empresa, TokenAccesoUsuario, Usuario


class _ActivationMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def inputs(self):
        return {attrs.get("name"): attrs for tag, attrs in self.elements if tag == "input"}


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AccessActivationDesignTests(TestCase):
    password = "Clave-Personal-Segura-946!"

    def setUp(self):
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media_root))
        image = BytesIO()
        Image.new("RGB", (2, 2), (6, 25, 68)).save(image, format="PNG")
        self.empresa = Empresa.objects.create(
            nombre="Empresa de ejemplo de activación",
            slug="activation-design-test",
            rtn="ACT-DESIGN-001",
            logo=SimpleUploadedFile("logo-empresa.png", image.getvalue(), content_type="image/png"),
        )
        self.usuario = Usuario.objects.create_user(
            username="activation-design-user",
            email="activation@example.test",
            first_name="Persona de prueba",
            password=None,
            is_active=False,
            empresa=self.empresa,
        )

    def invitation(self, **extra):
        raw = secrets.token_urlsafe(32)
        values = {
            "usuario": self.usuario,
            "tipo": TokenAccesoUsuario.TIPO_INVITACION,
            "token_hash": hash_token_acceso(raw),
            "token_preview": "activation-test",
            "estado_usuario_hash": firma_estado_usuario(self.usuario),
            "fecha_expiracion": timezone.now() + timedelta(hours=2),
            **extra,
        }
        token = TokenAccesoUsuario.objects.create(**values)
        return reverse("establecer_acceso", args=[raw]), token, raw

    def test_get_preserves_post_fields_csrf_accessible_help_and_private_resources(self):
        url, token, raw = self.invitation()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        markup = _ActivationMarkup(response.content.decode())
        forms = [attrs for tag, attrs in markup.elements if tag == "form"]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].get("method", "").lower(), "post")
        self.assertFalse(forms[0].get("action", "").startswith(("http:", "https:", "//")))
        inputs = markup.inputs()
        self.assertTrue(inputs["csrfmiddlewaretoken"]["value"])
        ids = {attrs["id"] for _, attrs in markup.elements if attrs.get("id")}
        labels = {attrs.get("for") for tag, attrs in markup.elements if tag == "label"}
        for name in ("new_password1", "new_password2"):
            field = inputs[name]
            self.assertEqual(field["type"], "password")
            self.assertEqual(field["autocomplete"], "new-password")
            self.assertIn("required", field)
            self.assertIn(field["id"], labels)
            self.assertIn(field["id"] + "_helptext", field.get("aria-describedby", "").split())
            self.assertIn(field["id"] + "_helptext", ids)
        images = [attrs for tag, attrs in markup.elements if tag == "img"]
        self.assertIn(self.empresa.logo.url, [attrs.get("src") for attrs in images])
        self.assertTrue(any("oasis-email-logo.png" in attrs.get("src", "") for attrs in images))
        metas = {attrs.get("name"): attrs.get("content", "") for tag, attrs in markup.elements if tag == "meta"}
        self.assertEqual(metas.get("referrer"), "no-referrer")
        self.assertIn("noindex", metas.get("robots", ""))
        for tag, attrs in markup.elements:
            if tag in ("img", "link", "script"):
                self.assertNotIn(raw, attrs.get("src", "") + attrs.get("href", ""))
        self.assertIn("no-store", response.headers["Cache-Control"])
        token.refresh_from_db()
        self.assertIsNone(token.fecha_uso)

    def test_post_without_csrf_keeps_invitation_and_account_unchanged(self):
        url, token, _ = self.invitation()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(url).status_code, 200)
        previous_password = self.usuario.password
        response = client.post(url, {"new_password1": self.password, "new_password2": self.password})
        self.assertRedirects(response, reverse("public_access"), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        token.refresh_from_db()
        self.assertFalse(self.usuario.is_active)
        self.assertEqual(self.usuario.password, previous_password)
        self.assertIsNone(token.fecha_uso)
        self.assertNotIn("_auth_user_id", client.session)

    def test_mismatched_passwords_show_associated_error_without_repopulating_secrets(self):
        url, token, _ = self.invitation()
        response = self.client.post(
            url, {"new_password1": self.password, "new_password2": "Otra-Clave-Segura-725!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "new_password2", "Las contraseñas no coinciden. Escríbelas de nuevo.")
        html = response.content.decode()
        self.assertIn("Las contraseñas no coinciden.", html)
        self.assertNotIn(self.password, html)
        self.assertNotIn("Otra-Clave-Segura-725!", html)
        markup = _ActivationMarkup(html)
        inputs = markup.inputs()
        ids = {attrs["id"] for _, attrs in markup.elements if attrs.get("id")}
        for name in ("new_password1", "new_password2"):
            self.assertFalse(inputs[name].get("value"))
        confirmation = inputs["new_password2"]
        self.assertEqual(confirmation.get("aria-invalid"), "true")
        error_id = confirmation["id"] + "_error"
        self.assertIn(error_id, confirmation.get("aria-describedby", "").split())
        self.assertIn(error_id, ids)
        self.usuario.refresh_from_db()
        token.refresh_from_db()
        self.assertFalse(self.usuario.is_active)
        self.assertFalse(self.usuario.has_usable_password())
        self.assertIsNone(token.fecha_uso)
        self.assertFalse(token.revocado)

    def test_unavailable_links_do_not_render_password_form_or_recipient_identity(self):
        for state in (
            {"revocado": True},
            {"fecha_expiracion": timezone.now() - timedelta(seconds=1)},
            {"fecha_uso": timezone.now()},
        ):
            with self.subTest(state=state):
                url, _, _ = self.invitation(**state)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
                html = response.content.decode()
                self.assertNotIn(self.usuario.email, html)
                self.assertNotIn(self.usuario.first_name, html)
                self.assertNotIn(self.empresa.nombre, html)
                self.assertNotIn(self.empresa.logo.url, html)
                markup = _ActivationMarkup(html)
                self.assertFalse(any(tag == "form" for tag, _ in markup.elements))
                self.assertNotIn("new_password1", markup.inputs())
                self.assertNotIn("new_password2", markup.inputs())
                self.assertTrue(any(tag == "img" and "oasis-email-logo.png" in attrs.get("src", "") for tag, attrs in markup.elements))
                self.assertIn("no-store", response.headers["Cache-Control"])

    def test_weak_password_keeps_validator_codes_with_spanish_messages(self):
        url, token, _ = self.invitation()
        previous_password = self.usuario.password
        response = self.client.post(url, {"new_password1": "1234", "new_password2": "1234"})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        codes = {error.code for error in form.errors.as_data()["new_password2"]}
        self.assertIn("password_too_short", codes)
        self.assertIn("password_entirely_numeric", codes)
        messages = " ".join(form.errors["new_password2"]).lower()
        self.assertIn("contraseña", messages)
        self.assertNotIn("password", messages)
        self.usuario.refresh_from_db()
        token.refresh_from_db()
        self.assertFalse(self.usuario.is_active)
        self.assertEqual(self.usuario.password, previous_password)
        self.assertIsNone(token.fecha_uso)
        self.assertFalse(token.revocado)
