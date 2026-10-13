import hashlib
import re

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.crypto import salted_hmac

from core.auth_backends import EmailOrUsernameBackend
from core.models import Empresa, Usuario


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SECRET_KEY="fictional-auth-diagnostics-signing-key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class AuthDiagnosticsTests(TestCase):
    password = " Fictional-Login-Secret-274! "
    other_password = "Fictional-Other-Company-Secret-863!"

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Empresa de diagnostico", slug="auth-diagnostics-a",
            rtn="AUTH-DIAGNOSTICS-A",
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Otra empresa de diagnostico", slug="auth-diagnostics-b",
            rtn="AUTH-DIAGNOSTICS-B",
        )
        cls.usuario = Usuario.objects.create_user(
            username="fictional-diagnostics-user", email="diagnostics@example.test",
            password=cls.password, empresa=cls.empresa,
        )

    def setUp(self):
        self.backend = EmailOrUsernameBackend()
        cache.clear()

    def capture_authentication(self, identifier, password, empresa=None, **kwargs):
        request = RequestFactory().post(
            "/fictional-login/",
            {"username": identifier or "", "password": password or ""},
            HTTP_COOKIE="fictional_session_cookie=fictional-private-cookie-value",
        )
        with self.assertLogs("core.auth_backends", level="INFO") as logs:
            user = self.backend.authenticate(
                request, username=identifier, password=password, empresa=empresa,
                **kwargs,
            )
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        message = record.getMessage()
        match = re.fullmatch(
            r"login_validation result=(\w+) company_id=(None|\d+) "
            r"identifier_tag=([0-9a-f]{16})",
            message,
        )
        self.assertIsNotNone(match, message)
        self.assertEqual(record.levelname, "INFO")
        self.assertEqual(record.name, "core.auth_backends")
        self.assertEqual(len(record.args), 3)
        self.assertIsNone(record.exc_info)
        expected_company_id = getattr(empresa, "pk", None)
        self.assertEqual(match.group(2), str(expected_company_id))
        for secret in (
            self.usuario.username, self.usuario.email, self.password,
            self.other_password, self.usuario.password,
            "fictional-private-cookie-value", identifier, password,
        ):
            if secret:
                self.assertNotIn(secret, message)
                self.assertNotIn(hashlib.sha256(secret.encode()).hexdigest(), message)
        self.assertNotIn("password=", message)
        self.assertNotIn("length", message)
        self.assertNotIn("hash", message)
        self.assertNotIn("POST", message)
        self.assertNotIn("cookie", message)
        return user, match.group(1), match.group(3)

    def assert_result(self, expected, identifier, password, empresa=None, **kwargs):
        user, result, tag = self.capture_authentication(
            identifier, password, empresa, **kwargs,
        )
        self.assertEqual(result, expected)
        return user, tag

    def test_success_reports_only_anonymous_identifier_and_company(self):
        user, tag = self.assert_result(
            "success", self.usuario.email, self.password, self.empresa,
        )
        self.assertEqual(user, self.usuario)
        expected_tag = salted_hmac(
            "core.auth.login_diagnostics", self.usuario.email.lower(),
            algorithm="sha256",
        ).hexdigest()[:16]
        self.assertEqual(tag, expected_tag)
        user, _ = self.assert_result(
            "success", None, self.password, email=self.usuario.email,
        )
        self.assertEqual(user, self.usuario)

        configuration = settings.LOGGING
        logger_configuration = configuration["loggers"]["core.auth_backends"]
        self.assertEqual(logger_configuration["level"], "INFO")
        self.assertFalse(logger_configuration["propagate"])
        self.assertEqual(logger_configuration["handlers"], ["login_diagnostics"])
        handler = configuration["handlers"]["login_diagnostics"]
        self.assertEqual(handler["class"], "logging.StreamHandler")
        format_string = configuration["formatters"][handler["formatter"]]["format"]
        self.assertIn("%(process)d", format_string)
        self.assertIn("%(asctime)s", format_string)
        self.assertIn("%(message)s", format_string)

        with self.assertLogs("core.auth_backends", level="INFO"):
            response = self.client.post(
                reverse("superadmin_login"),
                {"username": self.usuario.username, "password": self.password},
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_not_found_reports_email_and_username_without_exposing_either(self):
        for identifier in ("missing@example.test", "fictional-missing-user"):
            with self.subTest(identifier=identifier):
                user, _ = self.assert_result(
                    "not_found", identifier, self.password, self.empresa,
                )
                self.assertIsNone(user)
        user, _ = self.assert_result(
            "not_found", self.usuario.email, self.password, self.otra_empresa,
        )
        self.assertIsNone(user)
        with self.assertLogs("core.auth_backends", level="INFO"):
            response = self.client.post(
                reverse("empresa_login", args=[self.otra_empresa.slug]),
                {"username": self.usuario.username, "password": self.password},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo no pertenece a esta empresa.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_ambiguous_email_in_same_company_remains_rejected(self):
        Usuario.objects.create_user(
            username="fictional-duplicate-user", email=self.usuario.email.upper(),
            password=self.password, empresa=self.empresa,
        )
        user, _ = self.assert_result(
            "ambiguous", self.usuario.email, self.password, self.empresa,
        )
        self.assertIsNone(user)

    def test_invalid_password_remains_rejected(self):
        user, _ = self.assert_result(
            "invalid_password", self.usuario.email, "fictional-wrong-secret",
            self.empresa,
        )
        self.assertIsNone(user)

    def test_inactive_account_remains_rejected(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        user, _ = self.assert_result(
            "inactive", self.usuario.email, self.password, self.empresa,
        )
        self.assertIsNone(user)

    def test_missing_credentials_remain_rejected(self):
        for identifier, password in (
            (None, self.password), ("   ", self.password),
            (self.usuario.email, None),
        ):
            with self.subTest(identifier=identifier, password_present=password is not None):
                user, _ = self.assert_result(
                    "missing_credentials", identifier, password, self.empresa,
                )
                self.assertIsNone(user)

    def test_identifier_tag_is_normalized_keyed_and_distinct(self):
        for identifier in (self.usuario.email, self.usuario.username):
            with self.subTest(identifier=identifier):
                tags = []
                for value in (identifier, identifier.upper(), f"  {identifier.upper()}  "):
                    user, tag = self.assert_result(
                        "success", value, self.password, self.empresa,
                    )
                    self.assertEqual(user, self.usuario)
                    tags.append(tag)
                self.assertEqual(len(set(tags)), 1)

        _, tag = self.assert_result(
            "success", self.usuario.email, self.password, self.empresa,
        )
        _, other_tag = self.assert_result(
            "not_found", "another-fictional@example.test", self.password,
            self.empresa,
        )
        with override_settings(SECRET_KEY="different-fictional-signing-key"):
            _, changed_key_tag = self.assert_result(
                "success", self.usuario.email, self.password, self.empresa,
            )
        self.assertNotEqual(tag, other_tag)
        self.assertNotEqual(tag, changed_key_tag)

    def test_same_email_in_different_companies_uses_only_target_account(self):
        other_user = Usuario.objects.create_user(
            username="fictional-other-company-user", email=self.usuario.email,
            password=self.other_password, empresa=self.otra_empresa,
        )
        for empresa, expected_user, password, wrong_password in (
            (self.empresa, self.usuario, self.password, self.other_password),
            (self.otra_empresa, other_user, self.other_password, self.password),
        ):
            with self.subTest(company_id=empresa.pk):
                user, _ = self.assert_result(
                    "success", self.usuario.email, password, empresa,
                )
                self.assertEqual(user, expected_user)
                user, _ = self.assert_result(
                    "invalid_password", self.usuario.email, wrong_password, empresa,
                )
                self.assertIsNone(user)
        user, _ = self.assert_result(
            "ambiguous", self.usuario.email, self.password,
        )
        self.assertIsNone(user)
        other_user.delete()
        self.usuario.empresas_acceso.add(self.empresa, self.otra_empresa)
        for empresa in (self.empresa, self.otra_empresa):
            with self.subTest(company_id=empresa.pk):
                user, _ = self.assert_result(
                    "success", self.usuario.email, self.password, empresa,
                )
                self.assertEqual(user, self.usuario)
                self.assertTrue(user.puede_acceder_empresa(empresa))
