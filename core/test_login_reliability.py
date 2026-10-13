import re

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, Usuario


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CSRF_TRUSTED_ORIGINS=[],
)
class LoginReliabilityTests(TestCase):
    password = "test-login-password"

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Empresa de prueba", slug="login-test", rtn="LOGIN-TEST-001"
        )
        cls.clinica = Empresa.objects.create(
            nombre="Clinica de prueba", slug="hospital_mia", rtn="LOGIN-TEST-002",
            tipo_solucion="clinica",
        )
        cls.garage = Empresa.objects.create(
            nombre="Garage de prueba", slug="garage-login-test", rtn="LOGIN-TEST-003",
            tipo_solucion="tecnicentro",
        )
        modulo, _ = Modulo.objects.get_or_create(
            codigo="tecnicentro", defaults={"nombre": "Tecnicentro"}
        )
        EmpresaModulo.objects.create(empresa=cls.garage, modulo=modulo)
        cls.usuario = Usuario.objects.create_user(
            username="login-test-user", email="empresa@example.test",
            password=cls.password, empresa=cls.empresa,
        )
        cls.usuario_clinica = Usuario.objects.create_user(
            username="login-clinica-user", email="clinica@example.test",
            password=cls.password, empresa=cls.clinica,
        )
        cls.usuario_garage = Usuario.objects.create_user(
            username="login-garage-user", email="garage@example.test",
            password=cls.password, empresa=cls.garage, es_administrador_empresa=True,
        )
        cls.superadmin = Usuario.objects.create_superuser(
            username="login-test-admin", email="admin@example.test", password=cls.password
        )

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)

    def portals(self):
        for empresa, usuario in (
            (self.empresa, self.usuario), (self.clinica, self.usuario_clinica),
        ):
            yield (
                reverse("empresa_login", args=[empresa.slug]),
                reverse("cerrar_sesion", args=[empresa.slug]),
                reverse("dashboard", args=[empresa.slug]),
                usuario,
                reverse("empresa_login", args=[empresa.slug]),
            )
        yield (
            reverse("tecnicentro_login", args=[self.garage.slug]),
            reverse("tecnicentro_logout", args=[self.garage.slug]),
            reverse("tecnicentro_dashboard", args=[self.garage.slug]),
            self.usuario_garage,
            reverse("empresa_login", args=[self.garage.slug]),
        )
        yield (
            reverse("superadmin_login"), reverse("superadmin_logout"),
            reverse("superadmin_dashboard"), self.superadmin, reverse("superadmin_login"),
        )

    def assert_no_store(self, response):
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))
        self.assertIn("Expires", response.headers)

    def fresh_token(self, client):
        response = client.get(reverse("login_csrf_token"))
        self.assertEqual(response.status_code, 200)
        self.assert_no_store(response)
        token = response.json()["csrfToken"]
        self.assertTrue(token)
        return token

    def credentials(self, usuario, token, **extra):
        return {
            "username": usuario.email, "password": self.password,
            "csrfmiddlewaretoken": token, **extra,
        }

    def test_old_form_after_login_and_logout_recovers_with_one_refreshed_post(self):
        for login_url, logout_url, dashboard_url, usuario, failure_url in self.portals():
            with self.subTest(login_url=login_url):
                client = Client(enforce_csrf_checks=True)
                form = client.get(login_url)
                self.assertEqual(form.status_code, 200)
                self.assert_no_store(form)
                self.assertContains(form, "data-login-form")
                self.assertContains(
                    form, f'data-csrf-refresh-url="{reverse("login_csrf_token")}"'
                )
                self.assertContains(form, "core/login.js")
                csrf_input = re.search(
                    r'name="csrfmiddlewaretoken" value="([^"]+)"', form.content.decode()
                )
                self.assertIsNotNone(csrf_input)
                old_token = csrf_input.group(1)
                old_cookie = client.cookies[settings.CSRF_COOKIE_NAME].value

                # Another tab signs in and rotates the cookie shared by both tabs.
                signed_in = client.post(login_url, self.credentials(usuario, old_token))
                self.assertRedirects(signed_in, dashboard_url, fetch_redirect_response=False)
                self.assert_no_store(signed_in)
                self.assertNotEqual(client.cookies[settings.CSRF_COOKIE_NAME].value, old_cookie)
                client.get(logout_url)
                self.assertNotIn("_auth_user_id", client.session)

                stale = client.post(login_url, self.credentials(usuario, old_token))
                self.assertRedirects(stale, failure_url, fetch_redirect_response=False)
                self.assert_no_store(stale)
                self.assertNotIn("_auth_user_id", client.session)

                # The restored form can submit once with the current server token.
                refreshed = self.fresh_token(client)
                signed_in = client.post(login_url, self.credentials(usuario, refreshed))
                self.assertRedirects(signed_in, dashboard_url, fetch_redirect_response=False)
                self.assertEqual(client.session["_auth_user_id"], str(usuario.pk))

    def test_token_endpoint_creates_cookie_for_anonymous_client(self):
        self.assertNotIn(settings.CSRF_COOKIE_NAME, self.client.cookies)
        token = self.fresh_token(self.client)
        self.assertIn(settings.CSRF_COOKIE_NAME, self.client.cookies)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.post(
            reverse("empresa_login", args=[self.empresa.slug]),
            self.credentials(self.usuario, token),
        )
        self.assertRedirects(
            response, reverse("dashboard", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )

    def test_token_endpoint_only_allows_get(self):
        token = self.fresh_token(self.client)
        response = self.client.post(
            reverse("login_csrf_token"), {"csrfmiddlewaretoken": token}
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET")

    def test_login_pages_and_authenticated_redirects_are_not_cached(self):
        for login_url, _, _, usuario, _ in self.portals():
            with self.subTest(login_url=login_url):
                client = Client(enforce_csrf_checks=True)
                self.assert_no_store(client.get(login_url))
                client.force_login(usuario)
                self.assert_no_store(client.get(login_url))
        # The company entry also redirects to the specialized Garage OS login.
        response = self.client.get(reverse("empresa_login", args=[self.garage.slug]))
        self.assertRedirects(
            response, reverse("tecnicentro_login", args=[self.garage.slug]),
            fetch_redirect_response=False,
        )
        self.assert_no_store(response)

    def test_refreshed_token_does_not_allow_incorrect_password(self):
        for login_url, _, _, usuario, _ in self.portals():
            with self.subTest(login_url=login_url):
                client = Client(enforce_csrf_checks=True)
                response = client.post(login_url, self.credentials(
                    usuario, self.fresh_token(client), password="incorrect-password"
                ))
                self.assertEqual(response.status_code, 200)
                self.assert_no_store(response)
                self.assertNotIn("_auth_user_id", client.session)

    def test_missing_csrf_and_foreign_origin_still_reject_valid_credentials(self):
        for login_url, _, _, usuario, failure_url in self.portals():
            for scenario in ("missing-token", "foreign-origin"):
                with self.subTest(login_url=login_url, scenario=scenario):
                    client = Client(enforce_csrf_checks=True)
                    data = self.credentials(usuario, self.fresh_token(client))
                    headers = {}
                    if scenario == "missing-token":
                        data.pop("csrfmiddlewaretoken")
                    else:
                        headers["HTTP_ORIGIN"] = "https://untrusted.example.test"
                    response = client.post(login_url, data, **headers)
                    self.assertRedirects(response, failure_url, fetch_redirect_response=False)
                    self.assert_no_store(response)
                    self.assertNotIn("_auth_user_id", client.session)

    def test_company_and_superadmin_permissions_still_apply(self):
        for login_url in (
            reverse("empresa_login", args=[self.clinica.slug]), reverse("superadmin_login"),
        ):
            with self.subTest(login_url=login_url):
                client = Client(enforce_csrf_checks=True)
                response = client.post(
                    login_url, self.credentials(self.usuario, self.fresh_token(client))
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("_auth_user_id", client.session)

    def test_remember_and_safe_next_work_with_refreshed_token(self):
        login_url = reverse("empresa_login", args=[self.empresa.slug])
        dashboard_url = reverse("dashboard", args=[self.empresa.slug])
        local_next = f"{dashboard_url}?vista=prueba"
        for remember in ("on", ""):
            for next_url, expected in (
                (local_next, local_next), ("https://untrusted.example.test/", dashboard_url),
            ):
                with self.subTest(remember=remember, next_url=next_url):
                    client = Client(enforce_csrf_checks=True)
                    response = client.post(login_url, self.credentials(
                        self.usuario, self.fresh_token(client), remember=remember, next=next_url
                    ))
                    self.assertRedirects(response, expected, fetch_redirect_response=False)
                    self.assertEqual(client.session.get_expire_at_browser_close(), remember != "on")

    @override_settings(LOGIN_THROTTLE_LIMIT=2)
    def test_refreshing_token_does_not_clear_login_throttle(self):
        login_url = reverse("empresa_login", args=[self.empresa.slug])
        for _ in range(2):
            response = self.client.post(login_url, self.credentials(
                self.usuario, self.fresh_token(self.client), password="incorrect-password"
            ))
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post(
            login_url, self.credentials(self.usuario, self.fresh_token(self.client))
        )
        self.assertContains(blocked, "bloqueamos temporalmente")
        self.assertNotIn("_auth_user_id", self.client.session)
