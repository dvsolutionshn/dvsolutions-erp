from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from core.views import empresa_login


class LoginCorporativoTests(SimpleTestCase):
    def test_identidad_propia_con_y_sin_logo(self):
        for logo in (None, SimpleNamespace(url="/media/logo-empresa.png")):
            empresa = SimpleNamespace(slug="empresa", nombre="Mi Empresa & Asociados", logo=logo)
            request = RequestFactory().get("/empresa/?next=/empresa/dashboard/")
            html = render_to_string("core/login_corporativo.html", {"empresa": empresa, "request": request})
            self.assertIn("Mi Empresa &amp; Asociados", html)
            self.assertIn("core/images/dv-solutions-oficial.png", html)
            self.assertIn('value="/empresa/dashboard/"', html)
            self.assertEqual("/media/logo-empresa.png" in html, bool(logo))
            self.assertNotIn('class="company-logo"', html)
            if logo:
                self.assertEqual(html.count('/media/logo-empresa.png'), 1)
                self.assertLess(html.index('class="access-logo"'), html.index('class="welcome"'))

    def test_logo_oficial_es_la_imagen_entregada(self):
        path = finders.find("core/images/dv-solutions-oficial.png")
        self.assertIsNotNone(path)
        self.assertEqual(sha256(Path(path).read_bytes()).hexdigest(),
                         "88cf552e962d439c3f77d98fe7cb9994c2bf20ecff694729985403e97ccde926")

    def test_tecnicentro_conserva_acceso_especializado(self):
        empresa = SimpleNamespace(slug="garage-demo", tipo_solucion="tecnicentro")
        with patch("core.views._resolver_empresa_request", return_value=empresa), patch("core.views.redirect") as redirect:
            empresa_login(RequestFactory().get("/garage-demo/"), empresa.slug)
        redirect.assert_called_once_with("tecnicentro_login", empresa_slug="garage-demo")

    def test_recordarme_corporativo_y_next_local(self):
        for remember in ("on", ""):
            request = RequestFactory().post("/empresa/", {"username": "ejemplo", "password": "test", "remember": remember, "next": "/empresa/dashboard/"})
            SessionMiddleware(lambda r: None).process_request(request)
            empresa = SimpleNamespace(slug="empresa", tipo_solucion="erp")
            user = Mock()
            user.puede_acceder_empresa.return_value = True
            with patch("core.views._resolver_empresa_request", return_value=empresa), patch("core.views._es_perfil_clinico", return_value=False), patch("core.views._flash_session_expired_message"), patch("core.views._login_block_seconds", return_value=0), patch("core.views.authenticate", return_value=user), patch("core.views._clear_login_failures"), patch("core.views.login"):
                response = empresa_login(request, empresa.slug)
            self.assertEqual(response.url, "/empresa/dashboard/")
            self.assertEqual(request.session.get_expire_at_browser_close(), remember != "on")
