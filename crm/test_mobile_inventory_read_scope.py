"""APP inventory never joins a displayed product to another company's total.

Reuse only synthetic fixture construction, not another TestCase's test methods.
The catalogue remains independent of inventory reads and all financial savers.
"""
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from facturacion import test_mobile_operation_section_navigation as _operation_tests
from facturacion.models import InventarioProducto, Producto
from .mobile_inventory import productos_inventario_app


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileInventoryReadScopeTests(TestCase):
    SLUGS = _operation_tests.MobileOperationSectionNavigationTests.SLUGS
    NOW = _operation_tests.MobileOperationSectionNavigationTests.NOW
    PDF = _operation_tests.MobileOperationSectionNavigationTests.PDF
    FULL_PERMISSIONS = _operation_tests.MobileOperationSectionNavigationTests.FULL_PERMISSIONS
    SENTINEL = Decimal("98765.43")

    @classmethod
    def setUpTestData(cls):
        _operation_tests.MobileOperationSectionNavigationTests.setUpTestData.__func__(cls)

    login = _operation_tests.MobileOperationSectionNavigationTests.login
    url = _operation_tests.MobileOperationSectionNavigationTests.url
    role = _operation_tests.MobileOperationSectionNavigationTests.role
    snapshot = _operation_tests.MobileOperationSectionNavigationTests.snapshot
    assert_no_stock_queries = _operation_tests.MobileOperationSectionNavigationTests.assert_no_stock_queries

    def setUp(self):
        _operation_tests.MobileOperationSectionNavigationTests.setUp(self)

    def inventory_url(self, company):
        return reverse("agenda_mobile_inventory", args=[company.slug])

    def get_unchanged(self, url, status=200):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status, url)
        self.assertEqual(self.snapshot(), before)
        for entry in queries:
            self.assertNotRegex(entry["sql"].lstrip(), r"(?i)^(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b")
        return response, queries

    def assert_not_joined(self, queries):
        for entry in queries:
            self.assertNotIn(f'JOIN "{InventarioProducto._meta.db_table}"', entry["sql"])

    def test_foreign_inventory_company_four_clinics_both_roles_denied_before_join_without_leak(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                with self.subTest(actor=user.username, company=company.slug):
                    other = next(item for item in self.empresas.values() if item.pk != company.pk)
                    stock = InventarioProducto.objects.get(producto=self.productos[company.slug])
                    original = stock.empresa_id, stock.existencias
                    InventarioProducto.objects.filter(pk=stock.pk).update(empresa=other, existencias=self.SENTINEL)
                    try:
                        response, queries = self.get_unchanged(self.inventory_url(company), status=403)
                        self.assert_not_joined(queries)
                        self.assertNotContains(response, str(self.SENTINEL), status_code=403)
                        self.assertNotContains(response, self.productos[company.slug].nombre, status_code=403)
                    finally:
                        InventarioProducto.objects.filter(pk=stock.pk).update(empresa_id=original[0], existencias=original[1])

    def test_orphan_inventory_company_is_not_hidden_by_related_join(self):
        for company in self.empresas.values():
            stock = InventarioProducto.objects.get(producto=self.productos[company.slug])
            original = stock.empresa_id
            InventarioProducto.objects.filter(pk=stock.pk).update(empresa_id=2**62)
            try:
                before = self.snapshot()
                reset_queries()
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(PermissionDenied): productos_inventario_app(company)
                self.assert_not_joined(queries)
                self.assertEqual(self.snapshot(), before)
                response, _ = self.get_unchanged(self.inventory_url(company), status=403)
                self.assertNotContains(response, self.productos[company.slug].nombre, status_code=403)
            finally:
                InventarioProducto.objects.filter(pk=stock.pk).update(empresa_id=original)

    def test_missing_inventory_is_legitimate_zero_and_read_does_not_create_it(self):
        for company in self.empresas.values():
            product = self.productos[company.slug]
            InventarioProducto.objects.filter(producto=product).delete()
            response, _ = self.get_unchanged(self.inventory_url(company))
            item = next(row for row in response.context["productos"] if row["id"] == product.pk)
            self.assertEqual(item["existencia"], 0.0)
            self.assertEqual(item["stock_minimo"], 0.0)
            self.assertEqual(item["estado_stock"], "sin_stock")
            self.assertTrue(item["sin_stock"])
            self.assertFalse(InventarioProducto.objects.filter(producto=product).exists())

    def test_correct_inventory_preserves_original_dto_wrappers_lots_and_quantities(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                expected = productos_inventario_app(company)
                response, _ = self.get_unchanged(self.inventory_url(company))
                self.assertEqual(response.context["productos"], expected)
                self.assertEqual([row["producto"] for row in response.context["mobile_inventory_rows"]], expected)
                self.assertEqual(len(expected), 1)
                item = expected[0]
                self.assertEqual(item["id"], self.productos[company.slug].pk)
                self.assertEqual(item["existencia"], float(InventarioProducto.objects.get(producto_id=item["id"]).existencias))
                self.assertEqual([lot["numero"] for lot in item["lotes"]], [self.lotes[company.slug].numero_lote])
                self.assertEqual({row["nombre"] for row in item["bodegas"]}, {warehouse.nombre for warehouse in self.bodegas[company.slug]})

    def test_inactive_deleted_non_inventory_and_service_products_do_not_poison_display_scope(self):
        flags = ({"activo": False}, {"eliminado": True}, {"controla_inventario": False}, {"tipo_item": "servicio"})
        company = self.empresa
        foreign = self.empresas["medical_spa"]
        for index, excluded in enumerate(flags):
            for orphan in (False, True):
                product = Producto.objects.create(empresa=company, nombre=f"Fuera del scope {index}-{orphan}", codigo=f"INV-SCOPE-{index}-{orphan}", precio=Decimal("10"), controla_inventario=True)
                Producto.objects.filter(pk=product.pk).update(**excluded)
                stock = InventarioProducto.objects.create(empresa=foreign, producto=product, existencias=self.SENTINEL)
                if orphan: InventarioProducto.objects.filter(pk=stock.pk).update(empresa_id=2**62)
                try:
                    response, _ = self.get_unchanged(self.inventory_url(company))
                    self.assertNotIn(product.pk, {item["id"] for item in response.context["productos"]})
                    self.assertNotContains(response, str(self.SENTINEL))
                finally:
                    if orphan: InventarioProducto.objects.filter(pk=stock.pk).update(empresa=foreign)

    def test_corruption_of_another_company_does_not_block_healthy_company(self):
        other = self.empresas["medical_spa"]
        stock = InventarioProducto.objects.get(producto=self.productos[other.slug])
        InventarioProducto.objects.filter(pk=stock.pk).update(empresa=self.empresa, existencias=self.SENTINEL)
        response, _ = self.get_unchanged(self.inventory_url(self.empresa))
        self.assertNotContains(response, str(self.SENTINEL))
        self.assertEqual({item["id"] for item in response.context["productos"]}, {self.productos[self.empresa.slug].pk})
        self.get_unchanged(self.inventory_url(other), status=403)

    def test_catalogue_still_reads_no_stock_even_when_its_inventory_bridge_is_denied(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                stock = InventarioProducto.objects.get(producto=self.productos[company.slug])
                other = next(item for item in self.empresas.values() if item.pk != company.pk)
                original = stock.empresa_id, stock.existencias
                InventarioProducto.objects.filter(pk=stock.pk).update(empresa=other, existencias=self.SENTINEL)
                try:
                    response, queries = self.get_unchanged(self.url("productos_facturacion", company))
                    self.assert_no_stock_queries(queries)
                    self.assertNotContains(response, str(self.SENTINEL))
                    for item in response.context["mobile_product_items"]:
                        self.assertEqual(set(item), _operation_tests.MobileOperationSectionNavigationTests.PRODUCT_KEYS)
                    self.assertEqual(response.context["app_inventory_url"], self.inventory_url(company))
                    self.get_unchanged(response.context["app_inventory_url"], status=403)
                finally:
                    InventarioProducto.objects.filter(pk=stock.pk).update(empresa_id=original[0], existencias=original[1])

    def test_catalogue_permission_does_not_grant_inventory_or_make_corrupt_total_visible(self):
        for granular in (True, False):
            self.role(granular=granular, puede_productos=True)
            stock = InventarioProducto.objects.get(producto=self.productos[self.empresa.slug])
            InventarioProducto.objects.filter(pk=stock.pk).update(empresa=self.empresas["medical_spa"], existencias=self.SENTINEL)
            response, queries = self.get_unchanged(self.url("productos_facturacion"))
            self.assert_no_stock_queries(queries)
            self.assertEqual(response.context["app_inventory_url"], "")
            self.assertTrue(all(row["actions"]["kardex_url"] == "" for row in response.context["mobile_product_rows"]))
            # The original granular middleware redirects before this view;
            # the legacy view returns 403. Neither reaches the inventory read.
            with patch("crm.views.productos_inventario_app", side_effect=AssertionError("Unauthorized inventory read")) as guarded:
                denied, queries = self.get_unchanged(self.inventory_url(self.empresa), status=302 if granular else 403)
                guarded.assert_not_called()
            self.assert_not_joined(queries)
            self.assertNotContains(denied, str(self.SENTINEL), status_code=denied.status_code)
            before = self.snapshot()
            with self.assertRaises(PermissionDenied): productos_inventario_app(self.empresa)
            self.assertEqual(self.snapshot(), before)
