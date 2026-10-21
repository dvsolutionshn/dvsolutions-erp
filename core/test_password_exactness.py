from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.forms import (
    SuperAdminLoginForm,
    UsuarioControlCreateForm,
    UsuarioControlUpdateForm,
)
from core.models import Empresa, Usuario


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class PasswordExactnessTests(TestCase):
    password = " Clave-Prueba-946! "

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Empresa de pruebas de contrasenas",
            slug="password-exactness-test",
            rtn="PASSWORD-TEST-001",
        )
        cls.usuario = Usuario.objects.create_user(
            username="password-exactness-user",
            email="password-exactness@example.test",
            password=cls.password,
            empresa=cls.empresa,
        )
        cls.superadmin = Usuario.objects.create_superuser(
            username="password-exactness-admin",
            email="password-admin@example.test",
            password=cls.password,
        )

    def setUp(self):
        cache.clear()

    def company_login(self, usuario, password):
        client = Client(enforce_csrf_checks=True)
        token_response = client.get(reverse("login_csrf_token"))
        self.assertEqual(token_response.status_code, 200)
        response = client.post(
            reverse("empresa_login", args=[self.empresa.slug]),
            {
                "username": usuario.username,
                "password": password,
                "csrfmiddlewaretoken": token_response.json()["csrfToken"],
            },
        )
        return client, response

    def assert_company_login_succeeds(self, usuario, password):
        client, response = self.company_login(usuario, password)
        self.assertRedirects(
            response,
            reverse("dashboard", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )
        self.assertEqual(client.session["_auth_user_id"], str(usuario.pk))

    def create_data(self, **extra):
        return {
            "modo_creacion": UsuarioControlCreateForm.MODO_RAPIDO,
            "email": "created-password@example.test",
            "empresa": self.empresa.pk,
            "password1": self.password,
            "password2": self.password,
            **extra,
        }

    def update_data(self, **extra):
        return {
            "username": self.usuario.username,
            "email": self.usuario.email,
            "empresa": self.empresa.pk,
            "is_active": "on",
            "password1": self.password,
            "password2": self.password,
            **extra,
        }

    def test_quick_creation_preserves_password_and_first_company_login_works(self):
        form = UsuarioControlCreateForm(data=self.create_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["password1"], self.password)
        self.assertEqual(form.cleaned_data["password2"], self.password)

        usuario = form.save()
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password(self.password))
        self.assertFalse(usuario.check_password(self.password.strip()))
        self.assert_company_login_succeeds(usuario, self.password)

    def test_password_update_preserves_password_and_first_company_login_works(self):
        new_password = " Clave-Nueva-732! "
        form = UsuarioControlUpdateForm(
            instance=self.usuario,
            data=self.update_data(password1=new_password, password2=new_password),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["password1"], new_password)
        self.assertEqual(form.cleaned_data["password2"], new_password)

        usuario = form.save()
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password(new_password))
        self.assertFalse(usuario.check_password(new_password.strip()))
        self.assertFalse(usuario.check_password(self.password))
        self.assert_company_login_succeeds(usuario, new_password)

    def test_quick_creation_rejects_confirmation_differing_only_in_edge_spaces(self):
        for confirmation in (self.password.lstrip(), self.password.rstrip()):
            with self.subTest(confirmation=confirmation):
                form = UsuarioControlCreateForm(
                    data=self.create_data(password2=confirmation)
                )
                self.assertFalse(form.is_valid())
                self.assertIn("Las contrasenas no coinciden.", form.errors["password2"])
        self.assertFalse(Usuario.objects.filter(email="created-password@example.test").exists())

    def test_password_update_rejects_confirmation_differing_only_in_edge_spaces(self):
        for confirmation in (self.password.lstrip(), self.password.rstrip()):
            with self.subTest(confirmation=confirmation):
                form = UsuarioControlUpdateForm(
                    instance=self.usuario,
                    data=self.update_data(password2=confirmation),
                )
                self.assertFalse(form.is_valid())
                self.assertIn("Las contrasenas no coinciden.", form.errors["password2"])
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(self.password))

    def test_empty_password_update_keeps_existing_password(self):
        form = UsuarioControlUpdateForm(
            instance=self.usuario,
            data=self.update_data(password1="", password2=""),
        )
        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password(self.password))

    def test_superadmin_form_authenticates_exact_password_with_edge_spaces(self):
        form = SuperAdminLoginForm(
            data={"username": self.superadmin.username, "password": self.password}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["password"], self.password)
        self.assertEqual(form.get_user(), self.superadmin)

    def test_superadmin_form_rejects_modified_or_incorrect_password(self):
        for password in (self.password.strip(), "contrasena-incorrecta"):
            with self.subTest(password=password):
                form = SuperAdminLoginForm(
                    data={"username": self.superadmin.username, "password": password}
                )
                self.assertFalse(form.is_valid())
                self.assertIsNone(form.get_user())
                self.assertIn("Credenciales invalidas.", form.non_field_errors())

    def test_superadmin_first_post_authenticates_exact_password(self):
        client = Client(enforce_csrf_checks=True)
        token_response = client.get(reverse("login_csrf_token"))
        self.assertEqual(token_response.status_code, 200)
        response = client.post(
            reverse("superadmin_login"),
            {
                "username": self.superadmin.username,
                "password": self.password,
                "csrfmiddlewaretoken": token_response.json()["csrfToken"],
            },
        )
        self.assertRedirects(
            response, reverse("superadmin_dashboard"), fetch_redirect_response=False
        )
        self.assertEqual(client.session["_auth_user_id"], str(self.superadmin.pk))

    def test_company_rejects_modified_or_incorrect_password(self):
        for password in (self.password.strip(), "contrasena-incorrecta"):
            with self.subTest(password=password):
                client, response = self.company_login(self.usuario, password)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Correo o contrasena incorrectos.")
                self.assertNotIn("_auth_user_id", client.session)

    def test_three_fresh_company_posts_with_same_credentials_all_authenticate(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt + 1):
                self.assert_company_login_succeeds(self.usuario, self.password)
