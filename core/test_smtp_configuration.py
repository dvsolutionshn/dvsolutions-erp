import io
import os
import runpy
import smtplib
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class SMTPEnvironmentSettingsTests(SimpleTestCase):
    def load_settings(self, **environment):
        with patch.dict(os.environ, {"DEBUG": "true", **environment}, clear=True):
            return runpy.run_path(str(Path(__file__).resolve().parents[1] / "config" / "settings.py"))

    def test_ssl_uses_465_and_disables_starttls_by_default(self):
        settings = self.load_settings(EMAIL_USE_SSL="true")
        self.assertTrue(settings["EMAIL_USE_SSL"])
        self.assertFalse(settings["EMAIL_USE_TLS"])
        self.assertEqual(settings["EMAIL_PORT"], 465)

    def test_starttls_uses_587_by_default(self):
        settings = self.load_settings()
        self.assertFalse(settings["EMAIL_USE_SSL"])
        self.assertTrue(settings["EMAIL_USE_TLS"])
        self.assertEqual(settings["EMAIL_PORT"], 587)

    def test_ssl_and_starttls_together_are_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(EMAIL_USE_TLS="true", EMAIL_USE_SSL="true")

    def test_public_access_origin_rejects_credentials_paths_and_queries(self):
        for url in ("https://user:secret@example.com", "https://example.com/path", "https://example.com?token=secret", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured):
                self.load_settings(ACCESS_PUBLIC_BASE_URL=url)

    def test_public_access_origin_requires_https_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(
                DEBUG="false", SECRET_KEY="test-only", ALLOWED_HOSTS="example.com",
                DATABASE_URL="postgres://test@localhost/test", ACCESS_PUBLIC_BASE_URL="http://example.com",
            )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="smtp.example.com", EMAIL_PORT=587, EMAIL_USE_TLS=True,
    EMAIL_USE_SSL=False, EMAIL_HOST_USER="notificaciones@example.com",
    EMAIL_HOST_PASSWORD="smtp-secret-test-only", DEFAULT_FROM_EMAIL="DV Solutions <notificaciones@example.com>",
)
class SMTPDiagnosticCommandTests(SimpleTestCase):
    @patch("core.management.commands.comprobar_smtp.EmailMessage")
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_default_connects_and_authenticates_without_sending(self, get_connection, email_message):
        output = io.StringIO()
        call_command("comprobar_smtp", stdout=output)
        get_connection.return_value.open.assert_called_once_with()
        get_connection.return_value.close.assert_called_once_with()
        email_message.assert_not_called()
        self.assertIn("No se envio ningun correo", output.getvalue())
        self.assertNotIn("smtp-secret-test-only", output.getvalue())

    @patch("core.management.commands.comprobar_smtp.EmailMessage")
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_explicit_recipient_sends_one_message_without_creating_account(self, get_connection, email_message):
        email_message.return_value.send.return_value = 1
        call_command("comprobar_smtp", recipient="jorge.palma@rumi.hn", stdout=io.StringIO())
        self.assertEqual(email_message.call_args.kwargs["to"], ["jorge.palma@rumi.hn"])
        email_message.return_value.send.assert_called_once_with(fail_silently=False)
        get_connection.return_value.close.assert_called_once_with()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_console_backend_fails_before_network(self, get_connection):
        with self.assertRaisesMessage(CommandError, "EMAIL_BACKEND"):
            call_command("comprobar_smtp")
        get_connection.assert_not_called()

    @override_settings(EMAIL_HOST="")
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_missing_host_fails_before_network(self, get_connection):
        with self.assertRaisesMessage(CommandError, "EMAIL_HOST"):
            call_command("comprobar_smtp")
        get_connection.assert_not_called()

    @override_settings(EMAIL_USE_TLS=False, EMAIL_USE_SSL=False)
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_plaintext_is_rejected_before_network(self, get_connection):
        with self.assertRaisesMessage(CommandError, "cifrada"):
            call_command("comprobar_smtp")
        get_connection.assert_not_called()

    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_invalid_recipient_fails_before_network(self, get_connection):
        with self.assertRaisesMessage(CommandError, "direccion de correo valida"):
            call_command("comprobar_smtp", recipient="invalid")
        get_connection.assert_not_called()

    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_authentication_error_does_not_expose_server_response(self, get_connection):
        get_connection.return_value.open.side_effect = smtplib.SMTPAuthenticationError(535, b"smtp-secret-test-only")
        with self.assertRaises(CommandError) as error:
            call_command("comprobar_smtp", stdout=io.StringIO())
        self.assertNotIn("smtp-secret-test-only", str(error.exception))
        self.assertIn("rechazo la autenticacion", str(error.exception))
        get_connection.return_value.close.assert_called_once_with()

    @patch("core.management.commands.comprobar_smtp.EmailMessage")
    @patch("core.management.commands.comprobar_smtp.get_connection")
    def test_zero_messages_is_not_success(self, get_connection, email_message):
        email_message.return_value.send.return_value = 0
        with self.assertRaisesMessage(CommandError, "no confirmo"):
            call_command("comprobar_smtp", recipient="test@example.com", stdout=io.StringIO())
        get_connection.return_value.close.assert_called_once_with()
