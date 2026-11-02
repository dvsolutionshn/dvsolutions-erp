"""Inline mobile purchase catalog creation over existing saves, synthetic only."""
import json
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from contabilidad.models import AsientoContable, CuentaContable, CuentaFinanciera, LineaAsientoContable
from core.models import Empresa, EmpresaModulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import test_mobile_purchase_create as create_fixture
from . import views
from .models import (
    BodegaInventario, CAI, CompraInventario, ComprobanteEgresoCompra, EntradaInventarioDocumento,
    ExistenciaLoteBodega, Factura, InventarioProducto, LineaCompraInventario, LoteInventario,
    MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoCompra, PagoFactura,
    Producto, Proveedor, ReciboPago, TipoImpuesto,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePurchaseInlineCreateTests(TestCase):
    SLUGS = create_fixture.MobilePurchaseCreateTests.SLUGS
    snapshot = create_fixture.MobilePurchaseCreateTests.snapshot
    set_permissions = create_fixture.MobilePurchaseCreateTests.set_permissions
    purchase_payload = create_fixture.MobilePurchaseCreateTests.payload
    invoice_snapshot = create_fixture.MobilePurchaseCreateTests.invoice_snapshot
    ROUTES = {"supplier": "compra_crear_proveedor_rapido", "product": "compra_crear_producto_rapido"}

    @classmethod
    def setUpTestData(cls):
        create_fixture.MobilePurchaseCreateTests.setUpTestData.__func__(cls)

    def setUp(self):
        create_fixture.MobilePurchaseCreateTests.setUp(self)
        self.set_permissions(puede_crear_compras=True, puede_crear_proveedores=True, puede_crear_productos=True)

    def url(self, kind="supplier", company=None, mobile=True):
        company = company or self.empresa
        return reverse(self.ROUTES[kind], args=[company.slug]) + ("?app=1" if mobile else "")

    def item_payload(self, kind="supplier", company=None, **changes):
        company = company or self.empresa
        if kind == "supplier":
            data = {
                "nombre": f"Proveedor inline QA {company.slug}", "rtn": "08011999000123",
                "contacto": "Contacto QA", "telefono": "99998888", "correo": "  PROVEEDOR@EXAMPLE.INVALID  ",
                "ciudad": "Tegucigalpa", "condicion_pago": "credito", "dias_credito": "15",
            }
        else:
            data = {
                "nombre": f"Producto inline QA {company.slug}", "codigo": "MPCI-SHARED",
                "precio": "87.35", "unidad_medida": "caja", "impuesto_id": str(self.tax.pk),
            }
        data.update(changes)
        return data

    def post(self, kind="supplier", company=None, data=None, target=None, client=None, raw=None, content_type="application/json"):
        data = self.item_payload(kind, company) if data is None else data
        return (client or self.client).post(
            target or self.url(kind, company), raw if raw is not None else json.dumps(data),
            content_type=content_type,
        )

    def direct(self, kind="supplier", company=None, method="POST", raw=None, target=None, user=None, content_type="application/json"):
        target = target or self.url(kind, company)
        raw = json.dumps(self.item_payload(kind, company)) if raw is None else raw
        request = RequestFactory().generic(method, target, raw, content_type=content_type)
        request.user = user if user is not None else Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(target).path)
        return match.func(request, **match.kwargs)

    def protected_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (
            Empresa, Usuario, RolSistema, UsuarioEmpresaPermiso,
            CompraInventario, LineaCompraInventario, PagoCompra, ComprobanteEgresoCompra,
            MovimientoInventario, MovimientoLoteBodega, LoteInventario, ExistenciaLoteBodega,
            BodegaInventario, EntradaInventarioDocumento, CAI, Factura, NotaCredito,
            PagoFactura, ReciboPago, CuentaContable, CuentaFinanciera, AsientoContable, LineaAsientoContable,
        ))

    def assert_json_denied(self, response, statuses=(400, 403)):
        self.assertIn(response.status_code, statuses)
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        self.assertFalse(json.loads(response.content)["ok"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_four_companies_supplier_create_original_fields_payload_and_audit(self):
        protected = self.protected_snapshot()
        products = list(Producto.objects.order_by("pk").values())
        stocks = list(InventarioProducto.objects.order_by("pk").values())
        for company in self.empresas.values():
            response = self.post("supplier", company)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            supplier = Proveedor.objects.get(pk=body["proveedor"]["id"])
            self.assertEqual((supplier.empresa_id, supplier.nombre, supplier.condicion_pago, supplier.dias_credito), (company.pk, self.item_payload("supplier", company)["nombre"], "credito", 15))
            self.assertEqual(supplier.correo, "proveedor@example.invalid")
            self.assertEqual(body, {"ok": True, "proveedor": views._compra_proveedor_payload(supplier)})
            self.assertIn("no-store", response["Cache-Control"])
            audit = RegistroAuditoria.objects.get(modelo="proveedor", objeto_id=str(supplier.pk), accion="crear")
            self.assertEqual((audit.empresa_id, audit.usuario_id, audit.metodo_http), (company.pk, self.usuario.pk, "POST"))
        self.assertEqual(self.protected_snapshot(), protected)
        self.assertEqual(list(Producto.objects.order_by("pk").values()), products)
        self.assertEqual(list(InventarioProducto.objects.order_by("pk").values()), stocks)

    def test_four_companies_product_create_original_payload_zero_stock_and_audit(self):
        protected = self.protected_snapshot()
        suppliers = list(Proveedor.objects.order_by("pk").values())
        stock_before = list(InventarioProducto.objects.order_by("pk").values())
        new_ids = []
        for company in self.empresas.values():
            response = self.post("product", company)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            product = Producto.objects.get(pk=body["producto"]["id"])
            self.assertEqual((product.empresa_id, product.precio, product.unidad_medida), (company.pk, Decimal("87.35"), "caja"))
            self.assertEqual((product.tipo_item, product.controla_inventario, product.activo), ("producto", True, True))
            self.assertEqual(body, {"ok": True, "producto": views._pos_producto_payload(product, self.tax)})
            self.assertEqual((product.inventario.empresa_id, product.inventario.existencias), (company.pk, Decimal("0")))
            self.assertIn("no-store", response["Cache-Control"])
            audit = RegistroAuditoria.objects.get(modelo="producto", objeto_id=str(product.pk), accion="crear")
            self.assertEqual((audit.empresa_id, audit.usuario_id, audit.metodo_http), (company.pk, self.usuario.pk, "POST"))
            new_ids.append(product.pk)
        self.assertEqual(self.protected_snapshot(), protected)
        self.assertEqual(list(Proveedor.objects.order_by("pk").values()), suppliers)
        self.assertEqual(list(InventarioProducto.objects.exclude(producto_id__in=new_ids).order_by("pk").values()), stock_before)
        self.assertEqual(Producto.objects.filter(pk__in=new_ids, codigo="MPCI-SHARED").count(), 4)

    def test_inline_entities_can_be_used_in_original_purchase_saver_without_affecting_invoices(self):
        protected = self.invoice_snapshot()
        for company in self.empresas.values():
            supplier = self.post("supplier", company).json()["proveedor"]
            product = self.post("product", company).json()["producto"]
            data = self.purchase_payload(company)
            data.update({"proveedor": str(supplier["id"]), "lineas_compra-0-producto": str(product["id"])})
            response = self.client.post(reverse("crear_compra", args=[company.slug])+"?app=1", data)
            self.assertEqual(response.status_code, 302)
            purchase = CompraInventario.objects.get(empresa=company, referencia_documento=data["referencia_documento"])
            self.assertEqual((purchase.proveedor_id, purchase.proveedor_nombre, purchase.total_documento), (supplier["id"], supplier["nombre"], Decimal("258.75")))
            self.assertEqual(purchase.lineas.get().producto_id, product["id"])
            self.assertEqual(purchase.estado, "borrador")
            self.assertFalse(MovimientoInventario.objects.filter(compra_documento=purchase).exists())
        self.assertEqual(self.invoice_snapshot(), protected)

    def test_both_action_permissions_are_independent_for_legacy_and_granular_users(self):
        for granular in (False, True):
            for kind, permission in (("supplier", "puede_crear_proveedores"), ("product", "puede_crear_productos")):
                for flags in ({}, {"puede_crear_compras": True}, {permission: True}, {"puede_compras": True, permission: True}):
                    self.set_permissions(granular=granular, **flags)
                    before = self.snapshot()
                    self.assert_json_denied(self.direct(kind), (403,))
                    self.assertEqual(self.snapshot(), before)
                self.set_permissions(granular=granular, puede_crear_compras=True, **{permission: True})
                response = self.direct(kind, raw=json.dumps(self.item_payload(kind, nombre=f"Permisos {kind} {granular}", **({"codigo": f"MPCI-{granular}"} if kind == "product" else {}))))
                self.assertEqual(response.status_code, 200)

    def test_ui_flags_and_action_urls_are_hidden_without_corresponding_permission(self):
        form_url = reverse("crear_compra", args=[self.empresa.slug])+"?app=1"
        for flags in (
            {"puede_crear_compras": True},
            {"puede_crear_compras": True, "puede_crear_proveedores": True},
            {"puede_crear_compras": True, "puede_crear_productos": True},
        ):
            self.set_permissions(**flags)
            response = self.client.get(form_url)
            self.assertEqual(response.status_code, 200)
            for kind, permission, context_flag, context_url in (
                ("supplier", "puede_crear_proveedores", "can_create_purchase_supplier", "app_purchase_supplier_create_url"),
                ("product", "puede_crear_productos", "can_create_purchase_product", "app_purchase_product_create_url"),
            ):
                allowed = bool(flags.get(permission))
                self.assertEqual(response.context[context_flag], allowed)
                self.assertEqual(response.context[context_url], self.url(kind) if allowed else "")
                if not allowed:
                    self.assertNotContains(response, self.url(kind))

    def test_client_legacy_granular_v2_and_v3_need_actions_not_the_consult_permission(self):
        versions = (
            ("legacy", False, {}), ("granular", True, {}),
            ("v2", True, {"usa_permisos_clinicos_v2": True}),
            ("v3", True, {"usa_permisos_clinicos_v2": True, "usa_permisos_acciones_v3": True}),
        )
        for label, granular, version in versions:
            for kind, permission in (("supplier", "puede_crear_proveedores"), ("product", "puede_crear_productos")):
                for denied in (
                    {permission: True}, {"puede_crear_compras": True},
                    {"puede_crear_compras": True, permission: True, "solo_lectura_inventario": True},
                ):
                    self.set_permissions(granular=granular, **version, **denied)
                    before = self.snapshot()
                    response = self.post(kind)
                    # A read-only role is rejected by the unchanged transversal
                    # middleware before the mobile JSON helper is reached.
                    if response.status_code == 302:
                        self.assertEqual(response["Location"], reverse("dashboard", kwargs={"slug": self.empresa.slug}))
                    else:
                        self.assert_json_denied(response, (403,))
                    self.assertEqual(self.snapshot(), before)
                self.set_permissions(granular=granular, puede_crear_compras=True, **version, **{permission: True})
                data = self.item_payload(kind, nombre=f"Client inline {label} {kind}")
                if kind == "product":
                    data["codigo"] = f"MPCI-CLIENT-{label}"
                response = self.post(kind, data=data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])

    def test_company_permissions_membership_activity_license_module_and_profile_are_guarded(self):
        other = self.empresas["medical_spa"]
        self.set_permissions(companies=[other])
        for kind in self.ROUTES:
            self.assert_json_denied(self.direct(kind, company=other), (403,))
        self.set_permissions(puede_crear_compras=True, puede_crear_proveedores=True, puede_crear_productos=True)
        self.usuario.empresas_acceso.remove(other)
        for kind in self.ROUTES:
            self.assert_json_denied(self.direct(kind, company=other), (403,))
        for changes in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timezone.timedelta(days=1)}, {"tipo_solucion": "erp"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**changes)
            for kind in self.ROUTES:
                self.assert_json_denied(self.direct(kind), (403,))
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None, tipo_solucion="clinica")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=False)
        for kind in self.ROUTES:
            self.assert_json_denied(self.direct(kind), (403,))

    def test_read_only_roles_cannot_use_inline_creates_even_with_action_flags(self):
        self.set_permissions(puede_crear_compras=True, puede_crear_productos=True, puede_crear_proveedores=True, solo_lectura_inventario=True)
        before = self.snapshot()
        for kind in self.ROUTES:
            self.assert_json_denied(self.direct(kind), (403,))
        self.assertEqual(self.snapshot(), before)

    def test_app_inline_routes_do_not_enable_creation_for_a_fifth_company(self):
        company = Empresa.objects.create(nombre="Clínica fuera alcance inline QA", slug="inline_clinica_externa_qa", rtn="MPCI-EXT", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=company, modulo=self.module)
        self.usuario.empresas_acceso.add(company)
        self.set_permissions(companies=[company], puede_crear_compras=True, puede_crear_proveedores=True, puede_crear_productos=True)
        before = self.snapshot()
        for kind in self.ROUTES:
            self.assert_json_denied(self.direct(kind, company=company), (403,))
        self.assertEqual(self.snapshot(), before)

    def test_only_post_and_authenticated_users_are_accepted(self):
        before = self.snapshot()
        for kind in self.ROUTES:
            for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.direct(kind, method=method).status_code, 405)
            self.assertEqual(self.direct(kind, user=AnonymousUser()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_app_marker_is_unique_exact_and_extra_queries_are_rejected(self):
        before = self.snapshot()
        for kind in self.ROUTES:
            path = reverse(self.ROUTES[kind], args=[self.empresa.slug])
            for query in ("app=0", "app=1&app=1", "app=1&empresa=medical_spa", "app=1&next=https://example.invalid/"):
                self.assert_json_denied(self.direct(kind, target=path+"?"+query))
        self.assertEqual(self.snapshot(), before)

    def test_json_shape_encoding_duplicate_keys_and_bad_media_are_rejected(self):
        before = self.snapshot()
        for kind in self.ROUTES:
            for raw in ("[]", "null", "true", "42", '"texto"', "{", '{"nombre":"A","nombre":"B"}', b'{"nombre":"\xff"}'):
                self.assert_json_denied(self.direct(kind, raw=raw), (400,))
            self.assert_json_denied(self.direct(kind, content_type="text/plain"), (400,))
        self.assertEqual(self.snapshot(), before)

    def test_only_original_fields_can_be_sent_and_company_identity_stock_fields_are_rejected(self):
        before = self.snapshot()
        for kind in self.ROUTES:
            for key, value in (
                ("id", str(self.products["medical_spa"].pk)), ("empresa", str(self.empresas["medical_spa"].pk)),
                ("empresa_id", str(self.empresas["medical_spa"].pk)), ("activo", "false"),
                ("stock", "100"), ("cantidad", "100"), ("distribucion_bodegas", {}),
                ("cuenta_contable", str(self.accounts["medical_spa"].cuenta_contable_id)),
                ("cliente_contable", "1"), ("permisos", {}),
            ):
                data = self.item_payload(kind)
                data[key] = value
                self.assert_json_denied(self.direct(kind, raw=json.dumps(data)), (400,))
        self.assertEqual(self.snapshot(), before)

    def test_malformed_text_types_never_reach_original_saves(self):
        before = self.snapshot()
        for kind in self.ROUTES:
            for value in (True, False, None, 17, [], {}, {"nombre": "Anidado"}):
                data = self.item_payload(kind, nombre=value)
                self.assert_json_denied(self.direct(kind, raw=json.dumps(data)), (400,))
        self.assertEqual(self.snapshot(), before)

    def test_supplier_lengths_email_conditions_and_credit_days_are_validated(self):
        before = self.snapshot()
        for changes in (
            {"nombre": ""}, {"nombre": "N"*201}, {"rtn": "1"*21}, {"contacto": "N"*151},
            {"telefono": "1"*51}, {"ciudad": "N"*101}, {"correo": "correo-no-valido"},
            {"condicion_pago": "inventada"}, {"dias_credito": "-1"}, {"dias_credito": "1.5"},
            {"dias_credito": "Infinity"}, {"dias_credito": "NaN"}, {"dias_credito": True},
            {"dias_credito": "9"*100}, {"correo": []},
        ):
            self.assert_json_denied(self.direct("supplier", raw=json.dumps(self.item_payload("supplier", **changes))), (400,))
        self.assertEqual(self.snapshot(), before)

    def test_product_lengths_choices_tax_and_price_are_validated(self):
        before = self.snapshot()
        inactive = TipoImpuesto.objects.create(nombre="Impuesto inactivo inline QA", porcentaje=Decimal("18"), activo=False)
        before = self.snapshot()
        for changes in (
            {"nombre": ""}, {"nombre": "N"*201}, {"codigo": "N"*51}, {"unidad_medida": "inventada"},
            {"impuesto_id": "999999999"}, {"impuesto_id": str(inactive.pk)}, {"impuesto_id": "no-numero"},
            {"precio": "-1"}, {"precio": "Infinity"}, {"precio": "-Infinity"}, {"precio": "NaN"},
            {"precio": "sNaN"}, {"precio": "9999999999999999999999999"}, {"precio": True},
            {"precio": []}, {"precio": {}}, {"precio": None}, {"precio": 5},
        ):
            self.assert_json_denied(self.direct("product", raw=json.dumps(self.item_payload("product", **changes))), (400,))
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_company_product_code_does_not_create_orphan_stock(self):
        self.post("product")
        before = self.snapshot()
        response = self.post("product", data=self.item_payload("product", nombre="Otro nombre mismo código"))
        self.assert_json_denied(response, (400, 409))
        self.assertEqual(self.snapshot(), before)

    def test_product_still_requires_an_active_tax_before_original_save(self):
        TipoImpuesto.objects.update(activo=False)
        before = self.snapshot()
        response = self.post("product", data=self.item_payload("product", impuesto_id=""))
        self.assert_json_denied(response, (400,))
        self.assertEqual(self.snapshot(), before)

    def test_product_stock_initializer_failure_rolls_back_product_and_audit(self):
        before = self.snapshot()
        with patch.object(views, "_obtener_inventario_producto", side_effect=RuntimeError("Synthetic stock failure")):
            response = self.post("product")
        self.assert_json_denied(response, (500,))
        self.assertEqual(self.snapshot(), before)

    def test_csrf_original_failure_redirects_and_valid_token_allows_original_save(self):
        for kind in self.ROUTES:
            protected = Client(enforce_csrf_checks=True)
            protected.force_login(self.usuario)
            before = self.snapshot()
            denied = self.post(kind, client=protected)
            self.assertEqual(denied.status_code, 302)
            self.assertEqual(denied["Location"], reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
            self.assertEqual(self.snapshot(), before)
            form_url = reverse("crear_compra", args=[self.empresa.slug])+"?app=1"
            self.assertEqual(protected.get(form_url).status_code, 200)
            token = protected.cookies["csrftoken"].value
            valid = protected.post(self.url(kind), json.dumps(self.item_payload(kind)), content_type="application/json", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(valid.status_code, 200)
            self.assertTrue(valid.json()["ok"])

    def test_unmarked_web_endpoints_keep_the_original_json_payload_and_save(self):
        # Calling the views directly excludes mobile-only guards and avoids
        # changing the original middleware policy for these desktop actions.
        for kind in self.ROUTES:
            response = self.direct(kind, target=self.url(kind, mobile=False))
            self.assertEqual(response.status_code, 200)
            body = json.loads(response.content)
            self.assertTrue(body["ok"])
            if kind == "supplier":
                supplier = Proveedor.objects.get(pk=body["proveedor"]["id"])
                self.assertEqual(body["proveedor"], views._compra_proveedor_payload(supplier))
            else:
                product = Producto.objects.get(pk=body["producto"]["id"])
                self.assertEqual(body["producto"], views._pos_producto_payload(product, self.tax))

    def test_client_unmarked_legacy_and_v2_keep_original_denial_without_consult_permission(self):
        for granular, version in ((False, {}), (True, {"usa_permisos_clinicos_v2": True})):
            self.set_permissions(
                granular=granular, **version, puede_crear_compras=True,
                puede_crear_proveedores=True, puede_crear_productos=True,
            )
            for kind in self.ROUTES:
                before = self.snapshot()
                path = self.url(kind, mobile=False)
                unmarked = self.post(kind, target=path)
                self.assertEqual(unmarked.status_code, 302)
                self.assertEqual(unmarked["Location"], reverse("dashboard", kwargs={"slug": self.empresa.slug}))
                self.assertEqual(self.snapshot(), before)
                invalid_mobile = self.post(kind, target=path+"?app=0")
                self.assert_json_denied(invalid_mobile, (403,))
                self.assertEqual(self.snapshot(), before)

    def test_json_size_boundary_nested_duplicates_and_body_only_marker_do_not_bypass_guards(self):
        from .mobile_purchases import MAX_BYTES_ALTA_RAPIDA_COMPRA

        self.assertEqual(MAX_BYTES_ALTA_RAPIDA_COMPRA, 16*1024)
        for kind in self.ROUTES:
            encoded = json.dumps(self.item_payload(kind)).encode("utf-8")
            exact = encoded + b" "*(MAX_BYTES_ALTA_RAPIDA_COMPRA-len(encoded))
            self.assertEqual(len(exact), MAX_BYTES_ALTA_RAPIDA_COMPRA)
            accepted = self.post(kind, raw=exact)
            self.assertEqual(accepted.status_code, 200)
            self.assertTrue(accepted.json()["ok"])
            before = self.snapshot()
            oversized = self.post(kind, raw=exact+b" ")
            self.assert_json_denied(oversized, (400,))
            self.assertEqual(self.snapshot(), before)
            nested_duplicate = self.post(kind, raw='{"nombre":{"duplicado":"uno","duplicado":"dos"}}')
            self.assert_json_denied(nested_duplicate, (400,))
            self.assertEqual(self.snapshot(), before)
            body_marker = self.item_payload(kind)
            body_marker["app"] = "1"
            missing_query = self.post(kind, target=self.url(kind, mobile=False), data=body_marker)
            self.assert_json_denied(missing_query, (403,))
            self.assertEqual(self.snapshot(), before)
