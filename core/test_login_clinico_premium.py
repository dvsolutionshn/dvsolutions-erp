from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from core.views import empresa_login


class LoginClinicoPremiumTests(SimpleTestCase):
    def test_plantilla_exclusiva_para_las_cuatro_empresas(self):
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos", "otra_clinica"):
            with self.subTest(slug=slug):
                empresa = SimpleNamespace(slug=slug, tipo_solucion="clinica")
                request = RequestFactory().get("/" + slug + "/")
                with patch("core.views._resolver_empresa_request", return_value=empresa), patch("core.views._es_perfil_clinico", return_value=True), patch("core.views._flash_session_expired_message"), patch("core.views.render", return_value=HttpResponse()) as render:
                    empresa_login(request, slug)
                expected = "core/login_corporativo.html" if slug == "otra_clinica" else "core/login_clinico_premium.html"
                self.assertEqual(render.call_args.args[1], expected)

    def test_formulario_renderiza_sin_next_y_escapa_usuario(self):
        request = RequestFactory().post("/hospital_mia/", {"username": '<script>alert(1)</script>'})
        html = render_to_string("core/login_clinico_premium.html", {"request": request, "empresa": SimpleNamespace(slug="hospital_mia", nombre="Hospital MIA")})
        self.assertIn('name="next" value=""', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('/hospital_mia/recuperar-acceso/', html)

    def test_recordarme_y_redireccion_segura(self):
        for remember in ("on", ""):
            request = RequestFactory().post("/hospital_mia/", {"username": "test", "password": "test", "remember": remember, "next": "https://externo.test/"})
            SessionMiddleware(lambda r: None).process_request(request)
            empresa = SimpleNamespace(slug="hospital_mia", tipo_solucion="clinica")
            user = Mock()
            user.puede_acceder_empresa.return_value = True
            with patch("core.views._resolver_empresa_request", return_value=empresa), patch("core.views._es_perfil_clinico", return_value=True), patch("core.views._flash_session_expired_message"), patch("core.views._login_block_seconds", return_value=0), patch("core.views.authenticate", return_value=user), patch("core.views._clear_login_failures"), patch("core.views.login"), patch("core.views._redirect_dashboard_empresa", return_value=HttpResponse(status=302)) as dashboard:
                empresa_login(request, empresa.slug)
            self.assertEqual(request.session.get_expire_at_browser_close(), remember != "on")
            dashboard.assert_called_once()
