from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from core.demo_dashboard import MODULES


class DashboardDemoImageTests(SimpleTestCase):
    def test_cada_modulo_y_portada_tienen_una_imagen_distinta(self):
        images = [module[-1] for module in MODULES] + ["building"]
        self.assertEqual(len(images), len(set(images)))
        digests = set()
        for image in images:
            with self.subTest(image=image):
                path = finders.find(f"core/img/demo-dashboard/{image}.png")
                self.assertIsNotNone(path, f"Missing image: {image}")
                content = Path(path).read_bytes()
                self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
                digest = sha256(content).hexdigest()
                self.assertNotIn(digest, digests, f"Repeated image: {image}")
                digests.add(digest)


class DashboardDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Empresa de demostracion", slug="demo_1")
        cls.user = Usuario.objects.create_user(username="operador-demo", password="test-only", empresa=cls.empresa, es_administrador_empresa=True)
        for code, name in (("facturacion", "Facturacion"), ("contabilidad", "Contabilidad"), ("recibos", "Recibos")):
            module, _ = Modulo.objects.get_or_create(codigo=code, defaults={"nombre": name})
            EmpresaModulo.objects.create(empresa=cls.empresa, modulo=module, activo=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_demo_renderiza_datos_y_enlaces_reales(self):
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/dashboard_demo.html")
        self.assertContains(response, "Empresa de demostracion")
        self.assertContains(response, "3 módulos disponibles")
        self.assertContains(response, reverse("crear_factura", args=["demo_1"]))
        self.assertContains(response, "demo-dashboard.css")
        self.assertContains(response, "dv-solutions-oficial.png")
        self.assertContains(response, reverse("empresa_respaldo", args=["demo_1"]))

    def test_otras_empresas_y_clinicas_conservan_plantilla(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        for slug in ("otra_empresa", "hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            with self.subTest(slug=slug):
                Empresa.objects.create(nombre=slug, slug=slug, rtn=slug)
                response = self.client.get(reverse("dashboard", args=[slug]))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "core/dashboard_premium.html")
                self.assertNotContains(response, "demo-dashboard.css")
                self.assertNotContains(response, 'class="demo-dashboard"')

    def test_recibos_incluido_en_facturacion_sin_licencia_separada(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="recibos").delete()
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertContains(response, "Abrir Recibos")
        self.assertNotIn("Recibos", [s["title"] for s in response.context["demo_solutions"]])

    def test_sin_sesion_y_sin_acceso_a_empresa(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertEqual(response.status_code, 302)
        other = Empresa.objects.create(nombre="Otra", slug="otra", rtn="otra")
        outsider = Usuario.objects.create_user(username="externo", password="test-only", empresa=other)
        self.client.force_login(outsider)
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertEqual(response.status_code, 302)

    def test_sin_permisos_no_muestra_acciones_ni_modulos_ajenos(self):
        self.user.es_administrador_empresa = False
        self.user.save(update_fields=["es_administrador_empresa"])
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No tienes módulos disponibles")
        self.assertNotContains(response, "Nueva factura")
        self.assertNotContains(response, "Abrir módulo")
        self.assertNotContains(response, 'href="' + reverse("empresa_respaldo", args=["demo_1"]) + '"')
        self.assertNotIn("Facturación", [s["title"] for s in response.context["demo_solutions"]])

    def test_sin_modulos_activos(self):
        EmpresaModulo.objects.filter(empresa=self.empresa).update(activo=False)
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertContains(response, "0 módulos disponibles")
        self.assertNotContains(response, "Nueva factura")
        self.assertNotContains(response, "Abrir módulo")

    def test_licencia_vencida_conserva_bloqueo(self):
        self.empresa.fecha_vencimiento_plan = timezone.localdate() - timedelta(days=1)
        self.empresa.save(update_fields=["fecha_vencimiento_plan"])
        response = self.client.get(reverse("dashboard", args=["demo_1"]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
