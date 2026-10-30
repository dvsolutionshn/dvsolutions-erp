"""Home/Existencias links reuse the existing APP ProductoForm and saver.

Home retains its original initializers; only prepared editor/standalone reads
are measured as immutable. All records and optional writes are synthetic.
"""
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from core.models import EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from clinica.models import ConfiguracionClinica, ProfesionalSalud
from facturacion import test_mobile_product_management as _management_tests
from facturacion import views as billing_views
from facturacion.forms import ProductoForm
from facturacion.mobile_product_management import gestion_productos_disponible_app
from facturacion.models import CAI, ExistenciaLoteBodega, Factura, InventarioProducto, MovimientoInventario, MovimientoLoteBodega, PagoFactura, PerfilFarmaceuticoProducto, Producto
from .mobile_inventory import productos_inventario_app
from .models import ConfiguracionCRM


class _InventoryEditMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes, self.scripts = [], {}
        self.current_script = None
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs))
        if tag == "script" and attrs.get("id"):
            self.current_script = attrs["id"]
            self.scripts[self.current_script] = ""

    def handle_data(self, data):
        if self.current_script: self.scripts[self.current_script] += data

    def handle_endtag(self, tag):
        if tag == "script": self.current_script = None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class MobileInventoryProductEditTests(TestCase):
    SLUGS = _management_tests.MobileProductManagementIntegrationTests.SLUGS
    NOW = datetime(2026, 10, 23, 18, 0, tzinfo=datetime_timezone.utc)
    BUSINESS_ALLOWED = _management_tests.MobileProductManagementIntegrationTests.BUSINESS_ALLOWED
    ROUTES = _management_tests.MobileProductManagementIntegrationTests.ROUTES

    @classmethod
    def setUpTestData(cls):
        _management_tests.MobileProductManagementIntegrationTests.setUpTestData.__func__(cls)
        cls.agenda, _ = Modulo.objects.get_or_create(codigo="agenda_citas", defaults={"nombre": "Agenda bridge QA"})
        cls.profesionales = {}
        for company in cls.empresas.values():
            EmpresaModulo.objects.get_or_create(empresa=company, modulo=cls.agenda)
            ConfiguracionClinica.objects.get_or_create(empresa=company)
            ConfiguracionCRM.objects.get_or_create(empresa=company)
            cls.profesionales[company.slug] = ProfesionalSalud.objects.create(empresa=company, nombre=f"Profesional puente {company.slug}")
        RolSistema.objects.filter(pk__in=[cls.granular.pk, cls.legacy.pk]).update(puede_inventario=True, puede_ver_calendario=True, puede_citas=True)
        cls.producto_b = Producto.objects.create(empresa=cls.empresa, nombre="Segundo producto propio Hospital QA", codigo="BRIDGE-HM-B", precio="80.00", controla_inventario=True, impuesto_predeterminado=cls.tax)
        PerfilFarmaceuticoProducto.objects.create(empresa=cls.empresa, producto=cls.producto_b, categoria=cls.categorias[cls.empresa.slug])
        InventarioProducto.objects.create(empresa=cls.empresa, producto=cls.producto_b, existencias="0")
        cls.productos_extra = {cls.empresa.slug: cls.producto_b}

    url = _management_tests.MobileProductManagementIntegrationTests.url
    data = _management_tests.MobileProductManagementIntegrationTests.data
    snapshot = _management_tests.MobileProductManagementIntegrationTests.snapshot
    changes = _management_tests.MobileProductManagementIntegrationTests.changes
    no_writes = _management_tests.MobileProductManagementIntegrationTests.no_writes

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url("edit")  # Resolve metadata, never invoke a view.
        pdf = patch.object(billing_views, "HTML", side_effect=AssertionError("No native PDF"))
        pdf_mock = pdf.start(); self.addCleanup(pdf.stop); self.addCleanup(pdf_mock.assert_not_called)
        for name in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "crm.views.urlopen", "crm.services._post_whatsapp", "crm.services._post_multipart", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(name, side_effect=AssertionError(f"No bridge transport: {name}"))
            mock = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def set_role(self, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Puente inventario QA", codigo=f"inventory-edit-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, puede_ver_calendario=True, puede_citas=True, **flags)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete()
        return role

    def home(self, company=None):
        # This trained GET may initialize clinical/configuration/account data.
        # Do not misrepresent its original effects as globally read-only.
        company = company or self.empresa
        response = self.client.get(reverse("agenda_mobile", args=[company.slug]))
        self.assertEqual(response.status_code, 200)
        return response

    def immutable(self, url, method="get", data=None):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url, data=data or {})
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)
        return response

    def direct(self, url):
        request = RequestFactory().get(url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def stock_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (InventarioProducto, ExistenciaLoteBodega, MovimientoInventario, MovimientoLoteBodega))

    def financial_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (Factura, PagoFactura, CAI))

    def test_home_four_companies_both_roles_share_original_read_inventory_panel(self):
        for granular in (True, False):
            self.set_role(granular, puede_inventario=True, puede_editar_productos=True)
            for company in self.empresas.values():
                response = self.home(company)
                expected = reverse("agenda_mobile_inventory", args=[company.slug])
                self.assertEqual(response.context["app_inventory_products_url"], expected)
                cards = [attrs for tag, attrs in _InventoryEditMarkup(response.content).nodes if tag == "a" and "mobile-home-card inventory" in attrs.get("class", "")]
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0]["href"], "#productos-app")
                self.assertEqual(cards[0]["data-app-open"], "productos-app")
                self.assertTrue(response.context["puede_ver_productos_app"])
                self.assertEqual(response.context["inventario_productos_app_payload"], productos_inventario_app(company))
                self.assertContains(response, 'id="productos-app"')

    def test_hospital_edit_map_is_separate_exact_own_dto_ids_and_trusted_app_urls(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        response = self.home()
        original = productos_inventario_app(self.empresa)
        self.assertEqual(response.context["inventario_productos_app_payload"], original)
        expected = {str(item["id"]): reverse("editar_producto_facturacion", args=[self.empresa.slug, item["id"]])+"?app=1" for item in original}
        self.assertEqual(response.context["inventario_productos_app_editar_urls"], expected)
        self.assertTrue(response.context["puede_editar_productos_app"])
        markup = _InventoryEditMarkup(response.content)
        self.assertEqual(json.loads(markup.scripts["mobileInventoryProductEditUrls"]), expected)
        button = next(attrs for tag, attrs in markup.nodes if attrs.get("id") == "inventoryProductEdit")
        self.assertIn("hidden", button)
        self.assertNotIn("href", button)
        self.assertEqual(button["aria-disabled"], "true")
        for url in expected.values(): self.assertEqual(self.immutable(url).status_code, 200)

    def test_standalone_four_by_two_follows_existing_edit_link_to_full_original_dynamic_form(self):
        for granular in (True, False):
            self.set_role(granular, puede_inventario=True, puede_editar_productos=True)
            for company in self.empresas.values():
                inventory = self.immutable(reverse("agenda_mobile_inventory", args=[company.slug]))
                row = next(row for row in inventory.context["mobile_inventory_rows"] if row["producto"]["id"] == self.productos[company.slug].pk)
                url = row["app_product_edit_url"]
                self.assertEqual(url, self.url("edit", company))
                editor = self.immutable(url)
                self.assertEqual(editor.status_code, 200)
                self.assertTemplateUsed(editor, "facturacion/mobile_product_form.html")
                original = ProductoForm(instance=self.productos[company.slug], empresa=company)
                self.assertEqual(set(editor.context["form"].fields), set(original.fields))
                self.assertEqual([name for _, name in editor.context["form"].bodega_stock_fields], [name for _, name in original.bodega_stock_fields])
                self.assertEqual(editor.context["app_product_form_url"], url)
                self.assertFalse(editor.context["can_view_products"])

    def test_inventory_read_create_or_catalogue_permission_does_not_substitute_edit(self):
        for granular in (True, False):
            for extra in ({}, {"puede_crear_productos": True}, {"puede_productos": True}):
                self.set_role(granular, puede_inventario=True, **extra)
                response = self.home()
                self.assertEqual(response.context["inventario_productos_app_editar_urls"], {})
                self.assertFalse(response.context["puede_editar_productos_app"])
                self.assertNotContains(response, 'id="inventoryProductEdit"')
                inventory = self.immutable(reverse("agenda_mobile_inventory", args=[self.empresa.slug]))
                self.assertTrue(all(not row.get("app_product_edit_url") for row in inventory.context["mobile_inventory_rows"]))
                with self.assertRaises(PermissionDenied): self.direct(self.url("edit"))

    def test_editor_only_stays_authorized_directly_without_inventory_home_or_catalogue_grants(self):
        for granular in (True, False):
            self.set_role(granular, puede_editar_productos=True)
            for company in self.empresas.values():
                home = self.home(company)
                self.assertEqual(home.context["app_inventory_products_url"], "")
                self.assertFalse(home.context["puede_editar_productos_app"])
                self.assertEqual(home.context["inventario_productos_app_payload"], [])
                self.assertFalse(any("mobile-home-card inventory" in attrs.get("class", "") for _, attrs in _InventoryEditMarkup(home.content).nodes))
                editor = self.immutable(self.url("edit", company))
                self.assertEqual(editor.status_code, 200)
                self.assertEqual(editor.context["app_products_url"], "")
                self.assertFalse(editor.context["can_view_products"])

    def test_local_company_override_and_explicit_none_never_fall_back_to_global_edit_role(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        company = self.empresas["medical_spa"]
        limited = RolSistema.objects.create(nombre="Inventario sin edición local", codigo="inventory-edit-local-none", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_inventario=True, puede_ver_calendario=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=company, rol_sistema=limited)
        inventory = self.immutable(reverse("agenda_mobile_inventory", args=[company.slug]))
        self.assertTrue(all(not row.get("app_product_edit_url") for row in inventory.context["mobile_inventory_rows"]))
        with self.assertRaises(PermissionDenied): self.direct(self.url("edit", company))
        self.assertTrue(self.home().context["puede_editar_productos_app"])
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        request = RequestFactory().get(self.url("edit")); request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertFalse(gestion_productos_disponible_app(request, self.empresa)["editar"])

    def test_bridge_cannot_select_foreign_product_or_relax_existing_closed_editor_query(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        response = self.home()
        urls = response.context["inventario_productos_app_editar_urls"]
        self.assertNotIn(str(self.productos["medical_spa"].pk), urls)
        before = self.snapshot()
        with self.assertRaises(Http404): self.direct(self.url("edit", product=self.productos["medical_spa"]))
        with patch.object(billing_views, "ProductoForm", side_effect=AssertionError("No constructor on forged query")) as form:
            with self.assertRaises(PermissionDenied): self.direct(next(iter(urls.values()))+"&next=/")
            form.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_post_from_hospital_bridge_preserves_native_draft_and_trusted_identity(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        home = self.home()
        url = home.context["inventario_productos_app_editar_urls"][str(self.producto.pk)]
        original_name = self.producto.nombre
        body = self.data(product=self.producto, nombre="", precio="not-a-price", descripcion="<script>draft-only</script>")
        with patch.object(ProductoForm, "save", side_effect=AssertionError("Invalid form must not save")) as save:
            response = self.immutable(url, method="post", data=body)
            save.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].is_bound)
        for name, value in body.items(): self.assertEqual(response.context["form"].data[name], value)
        self.assertEqual(response.context["producto_actual"]["nombre"], original_name)
        self.assertNotContains(response, "<script>draft-only</script>")

    def test_valid_bridge_edit_four_by_two_uses_original_saver_once_and_preserves_stock_other_tenants(self):
        for granular in (True, False):
            self.set_role(granular, puede_inventario=True, puede_editar_productos=True)
            for company in self.empresas.values():
                inventory = self.immutable(reverse("agenda_mobile_inventory", args=[company.slug]))
                url = next(row["app_product_edit_url"] for row in inventory.context["mobile_inventory_rows"] if row["producto"]["id"] == self.productos[company.slug].pk)
                product = self.productos[company.slug]
                body = self.data(company, product, codigo=product.codigo, nombre=f"Edición por puente {company.slug}")
                stocks, finance = self.stock_snapshot(), self.financial_snapshot()
                other = list(Producto.objects.exclude(empresa=company).order_by("pk").values())
                original = ProductoForm.save
                with patch.object(ProductoForm, "save", autospec=True, side_effect=original) as save:
                    result = self.client.post(url, body)
                self.assertEqual(result.status_code, 302)
                save.assert_called_once()
                self.assertEqual(result.url, url)  # Original writer-only PRG, no new return parameter.
                self.assertEqual(self.stock_snapshot(), stocks)
                self.assertEqual(self.financial_snapshot(), finance)
                self.assertEqual(list(Producto.objects.exclude(empresa=company).order_by("pk").values()), other)

    def test_dynamic_stock_omission_remains_rejected_before_original_saver(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True)
        url = self.home().context["inventario_productos_app_editar_urls"][str(self.producto.pk)]
        body = self.data(product=self.producto, codigo=self.producto.codigo)
        name = next(key for key in body if key.startswith("stock_bodega_"))
        body.pop(name)
        before = self.snapshot()
        with patch.object(ProductoForm, "save", side_effect=AssertionError("Missing stock must not save")) as save:
            result = self.client.post(url, body)
            save.assert_not_called()
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_hospital_quick_entry_and_original_stock_billing_payloads_are_not_expanded(self):
        self.set_role(puede_inventario=True, puede_editar_productos=True, puede_ajustar_inventario=True)
        for company in self.empresas.values():
            finance, stock = self.financial_snapshot(), self.stock_snapshot()
            home = self.home(company)
            hospital = company.slug == "hospital_mia"
            self.assertEqual(home.context["puede_alimentar_productos_app"], hospital)
            self.assertTrue(home.context["puede_ver_productos_app"])
            self.assertEqual(home.context["inventario_productos_app_payload"], productos_inventario_app(company))
            self.assertEqual(set(home.context["inventario_productos_app_editar_urls"]), {str(item["id"]) for item in home.context["inventario_productos_app_payload"]})
            if hospital:
                self.assertContains(home, 'id="inventoryQuickEntryOpen"')
                self.assertEqual(home.context["inventario_productos_app_payload"], productos_inventario_app(company))
                self.assertTrue(all("edit_url" not in item for item in home.context["inventario_productos_app_payload"]))
            else:
                self.assertNotContains(home, 'id="inventoryQuickEntryOpen"')
            self.assertEqual(self.financial_snapshot(), finance)
            self.assertEqual(self.stock_snapshot(), stock)

    def test_original_unflagged_editor_catalogue_and_inline_create_templates_remain_unchanged(self):
        for url, template in ((self.url("edit", mobile=False), "facturacion/crear_producto.html"), (reverse("productos_facturacion", args=[self.empresa.slug]), "facturacion/productos_premium.html"), (self.url("create", mobile=False, modal="1"), "facturacion/crear_producto_rapido_modal.html")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            self.assertTemplateNotUsed(response, "facturacion/mobile_product_form.html")
