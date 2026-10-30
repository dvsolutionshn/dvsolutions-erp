"""Four-clinic presentation parity over existing APP and authorization routes.

Home keeps its original initializers. Only the prepared dashboard, metadata,
and destination-capability reads are measured as immutable. No business POST
or native PDF is used here; the installed/browser split is presentation only.
"""
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import connection, reset_queries
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import test_mobile_inventory_product_edit as _inventory_tests
from .mobile_inventory import productos_inventario_app


class _ClinicInterfaceMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes, self.scripts, self.script_id = [], {}, None
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs); self.nodes.append((tag, values))
        if tag == "script" and values.get("id"):
            self.script_id = values["id"]; self.scripts[self.script_id] = ""

    def handle_data(self, data):
        if self.script_id: self.scripts[self.script_id] += data

    def handle_endtag(self, tag):
        if tag == "script": self.script_id = None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class MobileClinicInterfaceTests(TestCase):
    SLUGS = _inventory_tests.MobileInventoryProductEditTests.SLUGS
    NOW = datetime(2026, 10, 30, 18, tzinfo=datetime_timezone.utc)
    ROUTES = _inventory_tests.MobileInventoryProductEditTests.ROUTES

    @classmethod
    def setUpTestData(cls):
        _inventory_tests.MobileInventoryProductEditTests.setUpTestData.__func__(cls)

    snapshot = _inventory_tests.MobileInventoryProductEditTests.snapshot
    no_writes = _inventory_tests.MobileInventoryProductEditTests.no_writes
    financial_snapshot = _inventory_tests.MobileInventoryProductEditTests.financial_snapshot
    stock_snapshot = _inventory_tests.MobileInventoryProductEditTests.stock_snapshot
    set_role = _inventory_tests.MobileInventoryProductEditTests.set_role
    url = _inventory_tests.MobileInventoryProductEditTests.url

    def setUp(self):
        _inventory_tests.MobileInventoryProductEditTests.setUp(self)

    def home(self, company=None, **query):
        company = company or self.empresa
        response = self.client.get(reverse("agenda_mobile", args=[company.slug]), query)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/agenda_mobile.html")
        # This trained GET has original clinical/config/account initializers.
        # Do not claim globally zero writes for this original workflow.
        return response

    def immutable(self, url, method="get"):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url)
        self.no_writes(queries); self.assertEqual(self.snapshot(), before)
        return response

    def destination(self, company=None, user=None):
        from .mobile_modules import destino_inicio_instalado_app
        company = company or self.empresa
        request = RequestFactory().get(reverse("dashboard", args=[company.slug]))
        request.user = user if user is not None else Usuario.objects.get(pk=self.usuario.pk)
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            result = destino_inicio_instalado_app(request, company)
        self.no_writes(queries); self.assertEqual(self.snapshot(), before)
        return result

    def test_four_companies_both_roles_share_premium_shell_and_original_read_inventory_panel(self):
        for granular in (True, False):
            self.set_role(granular, puede_inventario=True, puede_editar_productos=True)
            for company in self.empresas.values():
                page = self.home(company)
                self.assertTrue(page.context["hospital_mia_app_premium"])
                self.assertTrue(page.context["pacientes_app_premium"])
                self.assertContains(page, 'class="hospital-mia-premium-app"')
                self.assertContains(page, 'class="patients-premium-shell"')
                self.assertContains(page, 'premium-calendar-shell')
                self.assertTrue(page.context["puede_ver_productos_app"])
                self.assertContains(page, 'id="productos-app"')
                self.assertContains(page, 'href="#productos-app" data-app-open="productos-app"')

    def test_each_company_read_dto_and_edit_map_contain_only_its_existing_products(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        for company in self.empresas.values():
            page = self.home(company)
            expected = productos_inventario_app(company)
            self.assertEqual(page.context["inventario_productos_app_payload"], expected)
            mapped = page.context["inventario_productos_app_editar_urls"]
            self.assertEqual(set(mapped), {str(item["id"]) for item in expected})
            for item in expected:
                expected_url = reverse("editar_producto_facturacion", args=[company.slug, item["id"]])+"?app=1"
                self.assertEqual(mapped[str(item["id"])], expected_url)
                self.assertEqual(self.immutable(expected_url).status_code, 200)
            foreign_ids = {product.pk for slug, product in self.productos.items() if slug != company.slug}
            self.assertFalse(foreign_ids & {item["id"] for item in expected})

    def test_actual_company_switch_links_complete_four_company_cycle_and_return_to_hospital(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        sequence = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos", "hospital_mia")
        first_hospital = None; next_url = reverse("agenda_mobile", args=[sequence[0]])
        for index, slug in enumerate(sequence):
            page = self.client.get(next_url)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["empresa"].slug, slug)
            self.assertTrue(page.context["hospital_mia_app_premium"])
            switch = page.context["empresas_app_switch"]
            self.assertEqual([item["slug"] for item in switch if item["actual"]], [slug])
            self.assertTrue(set(sequence) <= {item["slug"] for item in switch})
            if slug == "hospital_mia":
                payload = page.context["inventario_productos_app_payload"]
                if first_hospital is None: first_hospital = payload
                else: self.assertEqual(payload, first_hospital)
            if index+1 < len(sequence):
                next_url = next(item["url"] for item in switch if item["slug"] == sequence[index+1])
                self.assertEqual(next_url, reverse("agenda_mobile", args=[sequence[index+1]]))

    def test_central_agenda_origin_and_own_company_payloads_remain_distinct(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        for company in self.empresas.values():
            page = self.home(company)
            self.assertEqual(page.context["agenda_empresa"].pk, self.empresa.pk)
            self.assertEqual(page.context["empresa"].pk, company.pk)
            self.assertEqual({item["id"] for item in page.context["productos_app_payload"]}, {item.pk for item in self.productos.values() if item.empresa_id == company.pk} | ({self.producto_b.pk} if company.pk == self.empresa.pk else set()))
            own_warehouses = {item.pk for item in self.bodegas[company.slug]}
            self.assertTrue({item["id"] for item in page.context["bodegas_app_payload"]} <= own_warehouses)

    def test_quick_stock_entry_stays_hospital_only_despite_read_panel_in_all_four(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True, puede_ajustar_inventario=True)
        for company in self.empresas.values():
            finance, stocks = self.financial_snapshot(), self.stock_snapshot()
            page = self.home(company)
            hospital = company.slug == "hospital_mia"
            self.assertEqual(page.context["puede_alimentar_productos_app"], hospital)
            markup = _ClinicInterfaceMarkup(page.content)
            self.assertEqual(any(attrs.get("id") == "inventoryQuickEntryOpen" for _, attrs in markup.nodes), hospital)
            self.assertEqual(self.financial_snapshot(), finance)
            self.assertEqual(self.stock_snapshot(), stocks)

    def test_product_read_permission_does_not_appear_from_edit_or_calendar_alone(self):
        for granular in (True, False):
            self.set_role(granular, puede_editar_productos=True)
            for company in self.empresas.values():
                page = self.home(company)
                self.assertFalse(page.context["puede_ver_productos_app"])
                self.assertFalse(page.context["puede_alimentar_productos_app"])
                self.assertEqual(page.context["inventario_productos_app_payload"], [])
                self.assertEqual(page.context["inventario_productos_app_editar_urls"], {})
                self.assertNotContains(page, 'id="productos-app"')

    def test_inventory_only_does_not_gain_product_edit_permission_in_any_company(self):
        self.set_role(puede_inventario=True)
        for company in self.empresas.values():
            page = self.home(company)
            self.assertTrue(page.context["puede_ver_productos_app"])
            self.assertFalse(page.context["puede_editar_productos_app"])
            self.assertEqual(page.context["inventario_productos_app_editar_urls"], {})
            self.assertNotContains(page, 'id="inventoryProductEdit"')

    def test_installed_destination_helper_returns_authorized_home_for_four_companies_without_writes(self):
        for granular in (True, False):
            self.set_role(granular, puede_inventario=True)
            for company in self.empresas.values():
                self.assertEqual(self.destination(company), reverse("agenda_mobile", args=[company.slug]))

    def test_granular_role_without_calendar_gets_authorized_hub_not_calendar_grant(self):
        role = RolSistema.objects.create(nombre="Inventario sin calendario QA", codigo="clinic-interface-no-calendar", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_inventario=True)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
        for company in self.empresas.values():
            url = reverse("agenda_mobile_modules_screen", args=[company.slug])
            self.assertEqual(self.destination(company), url)
            self.assertEqual(self.immutable(url).status_code, 200)
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa))

    def test_installed_destination_denies_anonymous_nonmember_inactive_license_and_wrong_scope(self):
        self.assertEqual(self.destination(user=AnonymousUser()), "")
        outsider = Usuario.objects.create_user(username="clinic-interface-outsider", password="test-only", empresa=self.externa, rol_sistema=self.granular)
        self.assertEqual(self.destination(user=outsider), "")
        self.assertEqual(self.destination(self.externa), "")
        for updates in ({"tipo_solucion": "erp"}, {"activa": False}, {"estado_licencia": "suspendida"}, {"estado_licencia": "vencida"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica", activa=True, estado_licencia="activa")
            Empresa.objects.filter(pk=self.empresa.pk).update(**updates)
            self.empresa.refresh_from_db()
            self.assertEqual(self.destination(), "")

    def test_no_role_or_explicit_local_none_provides_no_installed_operational_destination(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=None)
        self.assertEqual(self.destination(), "")
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=self.granular)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        self.assertEqual(self.destination(), "")
        self.assertEqual(self.destination(self.empresas["medical_spa"]), reverse("agenda_mobile", args=["medical_spa"]))

    def test_desktop_dashboard_stays_http200_and_embeds_only_authorized_installed_destination(self):
        self.set_role(puede_inventario=True)
        for company in self.empresas.values():
            self.home(company)  # Prepare original Home initializers outside measurement.
            page = self.immutable(reverse("dashboard", args=[company.slug]))
            self.assertEqual(page.status_code, 200)
            self.assertFalse(page.has_header("Location"))
            self.assertTemplateUsed(page, "core/dashboard_premium.html")
            self.assertNotContains(page, 'class="mobile-home mobile-app-screen active"')
            expected = reverse("agenda_mobile", args=[company.slug])
            self.assertEqual(page.context["clinical_installed_app_url"], expected)
            markup = _ClinicInterfaceMarkup(page.content)
            self.assertEqual(json.loads(markup.scripts["clinicalInstalledAppUrl"]), expected)
            scripts = [attrs for tag, attrs in markup.nodes if tag == "script" and attrs.get("src", "").endswith("crm/installed_app_entry.js")]
            self.assertEqual(len(scripts), 1)

    def test_dashboard_without_calendar_embeds_hub_destination_and_does_not_redirect_on_server(self):
        role = RolSistema.objects.create(nombre="Ficha clínica sin agenda QA", codigo="clinic-interface-patient-reader", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_pacientes=True)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
        page = self.immutable(reverse("dashboard", args=[self.empresa.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["clinical_installed_app_url"], reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertFalse(page.has_header("Location"))

    def test_external_erp_dashboard_does_not_get_clinical_installed_redirect_or_data(self):
        page = self.immutable(reverse("dashboard", args=[self.externa.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["clinical_installed_app_url"], "")
        self.assertNotContains(page, 'id="clinicalInstalledAppUrl"')
        self.assertNotContains(page, 'crm/installed_app_entry.js')

    def test_nonclinical_profile_does_not_gain_embedded_stock_or_installed_clinical_destination(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        for company in self.empresas.values():
            Empresa.objects.filter(pk=company.pk).update(tipo_solucion="erp")
            company.refresh_from_db()
            self.assertEqual(self.destination(company), "")
            page = self.home(company)
            self.assertFalse(page.context["hospital_mia_app_premium"])
            self.assertFalse(page.context["puede_ver_productos_app"])
            self.assertNotContains(page, 'id="productos-app"')
            Empresa.objects.filter(pk=company.pk).update(tipo_solucion="clinica")
            company.refresh_from_db()

    def test_manifest_and_service_worker_keep_own_company_scope_after_every_switch(self):
        self.set_role(puede_inventario=True)
        for company in self.empresas.values():
            manifest = self.immutable(reverse("agenda_mobile_manifest", args=[company.slug]))
            worker = self.immutable(reverse("agenda_mobile_service_worker", args=[company.slug]))
            self.assertEqual((manifest.status_code, worker.status_code), (200, 200))
            self.assertEqual(manifest.json()["start_url"], reverse("agenda_mobile", args=[company.slug]))
            self.assertEqual(manifest.json()["scope"], reverse("dashboard", args=[company.slug]))
            self.assertEqual(worker["Service-Worker-Allowed"], reverse("dashboard", args=[company.slug]))


class InstalledClinicEntrySourceTests(SimpleTestCase):
    def test_shared_entry_script_is_installed_only_same_origin_and_no_backend_transport(self):
        source = (Path(__file__).parent / "static" / "crm" / "installed_app_entry.js").read_text(encoding="utf-8")
        self.assertIn("navigator.standalone === true", source)
        self.assertIn("(display-mode: standalone)", source)
        self.assertIn("clinicalInstalledAppUrl", source)
        self.assertIn("target.origin !== window.location.origin", source)
        self.assertIn("window.location.replace(target.href)", source)
        for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "document.cookie"):
            self.assertNotIn(forbidden, source)

    def test_unknown_panel_falls_back_before_hiding_all_screens_and_switch_keeps_original_panel_rules(self):
        source = (Path(__file__).parent / "templates" / "crm" / "agenda_mobile.html").read_text(encoding="utf-8")
        start = source.index("const openAppPanel=id=>")
        end = source.index("const closeAppPanels=", start)
        function = source[start:end]
        self.assertLess(function.index("if(!panel||!appScreens.includes(panel))"), function.index("appScreens.forEach"))
        self.assertIn("closeAppPanels();return;", function)
        self.assertIn('activeScreen.id===\"agenda-app\"', source)
        self.assertIn('target.searchParams.set(\"app\",\"agenda\")', source)
        self.assertIn('activeScreen.id===\"patient-profile-app\"?\"pacientes-app\"', source)
