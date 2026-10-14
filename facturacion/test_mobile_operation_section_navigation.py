"""Operation navigation uses existing permissions and handlers, not new finance.

Fixtures are synthetic. Fiscal documents are inserted without invoking fiscal
savers. Prepared GETs are immutable; the original mutable-form initializers
remain covered separately by their integration suites.
"""
from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.mobile_modules import construir_contexto_modulos_app
from . import views
from .mobile_product_management import gestion_productos_disponible_app, volver_gestion_producto_app
from .mobile_products import productos_disponibles_app
from .mobile_stock_operations import stock_disponible_app, volver_stock_app
from .mobile_suppliers import proveedores_disponibles_app, volver_proveedor_app
from .models import (
    BodegaInventario, Cliente, CompraInventario, ComprobanteEgresoCompra,
    ExistenciaLoteBodega, Factura, InventarioProducto, LineaCompraInventario,
    LoteInventario, MovimientoInventario, MovimientoLoteBodega, PagoCompra,
    PagoFactura, Producto, Proveedor, ReciboPago,
)


class _OperationMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.form = None
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form": self.form = attrs.get("id")
        self.nodes.append((tag, {**attrs, "_form": self.form}))

    def handle_endtag(self, tag):
        if tag == "form": self.form = None

    @property
    def hrefs(self):
        return [attrs["href"] for tag, attrs in self.nodes if tag == "a" and "href" in attrs]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileOperationSectionNavigationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 14, 18, 0, tzinfo=datetime_timezone.utc)
    PDF = b"%PDF-1.4\nSynthetic operation navigation bytes\n%%EOF"
    PRODUCT_KEYS = {"id", "nombre", "codigo", "tipo_label", "unidad_label", "precio", "activo", "descripcion", "categoria", "principio_activo", "controlado", "refrigerado", "impuesto_label"}
    TILES = {
        "catalogo_productos": "productos_facturacion", "crear_producto": "crear_producto_facturacion",
        "entradas_lotes": "crear_lote_inventario", "traslado_avanzado": "traslado_inventario_farmaceutico",
        "kardex": "kardex_inventario", "transferencias": "traslado_rapido_farmaceutico",
        "recibos": "recibos_dashboard", "proveedores": "proveedores_facturacion", "compras": "compras_dashboard",
    }
    FULL_PERMISSIONS = {
        "puede_productos": True, "puede_crear_productos": True, "puede_editar_productos": True,
        "puede_inventario": True, "puede_transferir_inventario": True, "puede_recibos": True,
        "puede_proveedores": True, "puede_crear_proveedores": True, "puede_editar_proveedores": True,
        "puede_compras": True,
    }

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.billing, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Operación sintética"})
            cls.empresas, cls.configs, cls.productos, cls.productos_sin_stock = {}, {}, {}, {}
            cls.bodegas, cls.lotes, cls.existencias, cls.proveedores, cls.compras = {}, {}, {}, {}, {}
            cls.clientes, cls.facturas, cls.pagos, cls.recibos, cls.pagos_compra, cls.egresos = {}, {}, {}, {}, {}, {}
            for index, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Operación QA {slug}", slug=slug, rtn=f"OP-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company
                EmpresaModulo.objects.create(empresa=company, modulo=cls.billing)
                cls.configs[slug] = ConfiguracionAvanzadaEmpresa.objects.create(empresa=company, usa_bodegas_internas=True, usa_inventario_farmaceutico=True)
                names = (("Bodega Principal", "principal"), ("Bodega Clinica", "provisional")) if slug == "luque_aestetic" else (("Bodega General", "principal"), ("Bodega Hospital", "provisional"), ("Vitrina", "vitrina")) if slug in {"hospital_mia", "medical_spa"} else (("Bodega principal", "principal"), ("Bodega provisional", "provisional"), ("Vitrina", "vitrina"))
                cls.bodegas[slug] = [BodegaInventario.objects.create(empresa=company, nombre=name, tipo=kind) for name, kind in names]
                product = Producto.objects.create(empresa=company, nombre=f"Producto Operación {slug}", codigo=f"OP-P-{index}", precio=Decimal("115"), controla_inventario=True, descripcion="Descripción completa\nLiteral <script>NO_EJECUTAR</script>")
                cls.productos[slug] = product
                cls.productos_sin_stock[slug] = Producto.objects.create(empresa=company, nombre=f"Servicio histórico {slug}", codigo=f"OP-S-{index}", precio=Decimal("50"), tipo_item="servicio", controla_inventario=False, activo=False)
                provider = Proveedor.objects.create(empresa=company, nombre=f"Proveedor Operación {slug}", rtn=f"OP-PROV-{index}")
                cls.proveedores[slug] = provider
                lot = LoteInventario.objects.create(empresa=company, producto=product, numero_lote=f"OP-LOTE-{index}", fecha_vencimiento=date(2027, 8, 31), proveedor=provider)
                cls.lotes[slug] = lot
                cls.existencias[slug] = [ExistenciaLoteBodega.objects.create(empresa=company, lote=lot, bodega=warehouse, cantidad=Decimal(20 if number == 0 else 5)) for number, warehouse in enumerate(cls.bodegas[slug])]
                InventarioProducto.objects.create(empresa=company, producto=product, existencias=Decimal(20+5*(len(cls.bodegas[slug])-1)))
                purchase = CompraInventario.objects.create(empresa=company, proveedor=provider, proveedor_nombre=f"Proveedor histórico {slug}", estado="aplicada", fecha_documento=date(2026, 10, 1), condicion_pago="credito", dias_credito=15)
                cls.compras[slug] = purchase
                LineaCompraInventario.objects.create(compra=purchase, producto=product, cantidad=Decimal("2"), costo_unitario=Decimal("40"))
                cls.pagos_compra[slug] = PagoCompra.objects.bulk_create([PagoCompra(compra=purchase, fecha=date(2026, 10, 2), monto=Decimal("10"), metodo="efectivo")])[0]
                cls.egresos[slug] = ComprobanteEgresoCompra.objects.bulk_create([ComprobanteEgresoCompra(empresa=company, compra=purchase, pago=cls.pagos_compra[slug], proveedor=provider, proveedor_nombre=provider.nombre, numero_comprobante=f"EGR-OP-{index:08d}", fecha=date(2026, 10, 2), monto=Decimal("10"))])[0]
                customer = Cliente.objects.bulk_create([Cliente(empresa=company, nombre=f"Cliente Operación {slug}", rtn=f"08011999003{index:02d}")])[0]
                cls.clientes[slug] = customer
                invoice = Factura.objects.bulk_create([Factura(empresa=company, cliente=customer, numero_factura=f"001-001-01-{index:08d}", fecha_emision=date(2026, 10, 1), estado="borrador", subtotal=Decimal("100"), impuesto=Decimal("15"), total=Decimal("115"), total_lempiras=Decimal("115"))])[0]
                cls.facturas[slug] = invoice
                payment = PagoFactura.objects.bulk_create([PagoFactura(factura=invoice, fecha=date(2026, 10, 2), monto=Decimal("25"), metodo="efectivo")])[0]
                cls.pagos[slug] = payment
                cls.recibos[slug] = ReciboPago.objects.bulk_create([ReciboPago(empresa=company, factura=invoice, cliente=customer, pago=payment, numero_recibo=f"REC-OP-{index:08d}", fecha=date(2026, 10, 3), monto=Decimal("25"), referencia="Referencia documental", concepto="Concepto documental original")])[0]
            cls.empresa = cls.empresas["hospital_mia"]
            cls.granular = RolSistema.objects.create(nombre="Operación granular QA", codigo="op-nav-granular", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **cls.FULL_PERMISSIONS)
            cls.legacy = RolSistema.objects.create(nombre="Operación legado QA", codigo="op-nav-legacy", **cls.FULL_PERMISSIONS)
            cls.usuarios = {}
            for mode, role in (("granular", cls.granular), ("legacy", cls.legacy)):
                user = Usuario.objects.create_user(username=f"op-nav-{mode}", password="test-only", empresa=cls.empresa, rol_sistema=role, last_login=cls.NOW)
                user.empresas_acceso.add(*cls.empresas.values())
                cls.usuarios[mode] = user
            cls.usuario = cls.usuarios["granular"]
            cls.externa = Empresa.objects.create(nombre="ERP fuera de Operación móvil", slug="operacion_externa", rtn="OP-EXT", tipo_solucion="erp", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.billing)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.login(self.usuario)
        self.url("productos_facturacion")  # Resolve URL metadata before mocks; never invoke a view.
        renderer = patch.object(views, "HTML")
        self.html = renderer.start(); self.addCleanup(renderer.stop)
        self.html.return_value.write_pdf.return_value = self.PDF
        logo = patch.object(views, "_obtener_logo_url", return_value="")
        logo.start(); self.addCleanup(logo.stop)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden operation transport: {target}"))
            mock = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def login(self, user):
        self.actor = user
        self.client.force_login(user)

    def role(self, company=None, granular=True, **permissions):
        company = company or self.empresa
        role = RolSistema.objects.create(nombre="Operación restringida QA", codigo=f"op-nav-role-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=company, defaults={"rol_sistema": role})
        return role

    def url(self, route, company=None, obj=None, mobile=True, **query):
        company = company or self.empresa
        args = [company.slug] + ([obj.pk if hasattr(obj, "pk") else obj] if obj is not None else [])
        parameters = ({"app": "1", **query} if mobile else query)
        return reverse(route, args=args) + ("?"+urlencode(parameters, doseq=True) if parameters else "")

    def request(self, company=None):
        company = company or self.empresa
        request = RequestFactory().get(self.url("productos_facturacion", company))
        request.user = Usuario.objects.get(pk=self.actor.pk)
        return request

    def tiles(self, company=None):
        company = company or self.empresa
        return {item["key"]: item for item in construir_contexto_modulos_app(self.request(company), company)["modulos_app"]}

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}

    def passive_get(self, url, method="get"):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertEqual(self.snapshot(), before, url)
        for query in queries:
            self.assertNotRegex(query["sql"].lstrip(), r"(?i)^(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b")
        return response, queries

    def assert_no_stock_queries(self, queries):
        for model in (InventarioProducto, BodegaInventario, LoteInventario, ExistenciaLoteBodega, MovimientoInventario, MovimientoLoteBodega):
            self.assertFalse(any(f'"{model._meta.db_table}"' in query["sql"] for query in queries), model._meta.label)

    def direct(self, url):
        request = RequestFactory().get(url)
        request.user = Usuario.objects.get(pk=self.actor.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def test_nine_operation_tiles_use_exact_original_routes_four_companies_both_roles(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                with self.subTest(user=user.username, company=company.slug):
                    items = self.tiles(company)
                    for key, route in self.TILES.items():
                        self.assertEqual(items[key]["estado"], "disponible")
                        self.assertEqual(items[key]["categoria"], "Operación")
                        self.assertEqual(items[key]["url"], self.url(route, company))
                    self.assertTrue({"pacientes", "agenda", "facturacion", "inventario", "egresos", "cxc", "cxp"}.isdisjoint(items))

    def test_every_operation_tile_destination_is_existing_authorized_get_without_writes(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                for key in self.TILES:
                    with self.subTest(user=user.username, company=company.slug, key=key):
                        response, _ = self.passive_get(self.tiles(company)[key]["url"])
                        self.assertIn("no-store", response["Cache-Control"])
                        self.assertNotContains(response, "<table")

    def test_standalone_and_metadata_keep_registry_rows_without_operational_queries(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                response, queries = self.passive_get(reverse("agenda_mobile_modules_screen", args=[company.slug]))
                self.assertEqual({item["key"]: item for item in response.context["modulos_app"]}, self.tiles(company))
                self.assert_no_stock_queries(queries)
                data, queries = self.passive_get(reverse("agenda_mobile_modules", args=[company.slug]))
                self.assert_no_stock_queries(queries)
                self.assertEqual({item["key"] for item in data.json()["modulos"]}, set(self.tiles(company)))

    def test_catalogue_inventory_and_per_product_kardex_links_are_real_and_projection_unchanged(self):
        for company in self.empresas.values():
            response, queries = self.passive_get(self.url("productos_facturacion", company))
            self.assert_no_stock_queries(queries)
            self.assertEqual(response.context["app_inventory_url"], reverse("agenda_mobile_inventory", args=[company.slug]))
            self.passive_get(response.context["app_inventory_url"])
            self.assertEqual(list(response.context["productos"]), response.context["mobile_product_items"])
            for row in response.context["mobile_product_rows"]:
                self.assertEqual(set(row["item"]), self.PRODUCT_KEYS)
                expected = self.url("kardex_inventario", company, producto=row["item"]["id"])
                self.assertEqual(row["actions"]["kardex_url"], expected)
                self.passive_get(expected)

    def test_product_only_catalogue_does_not_grant_inventory_or_read_stock(self):
        for granular in (True, False):
            self.role(granular=granular, puede_productos=True)
            response, queries = self.passive_get(self.url("productos_facturacion"))
            self.assert_no_stock_queries(queries)
            self.assertEqual(response.context["app_inventory_url"], "")
            self.assertTrue(all(not row["actions"]["kardex_url"] and not row["actions"]["stock_entry_url"] for row in response.context["mobile_product_rows"]))
            self.assertNotIn("kardex", self.tiles())
            self.assertNotIn("entradas_lotes", self.tiles())
            with self.assertRaises(PermissionDenied): self.direct(self.url("kardex_inventario"))

    def test_catalogue_stock_entry_requires_active_inventory_product_but_kardex_is_historical(self):
        response, _ = self.passive_get(self.url("productos_facturacion"))
        rows = {row["item"]["id"]: row["actions"] for row in response.context["mobile_product_rows"]}
        product = self.productos[self.empresa.slug]
        historical = self.productos_sin_stock[self.empresa.slug]
        self.assertTrue(rows[product.pk]["stock_entry_url"])
        self.assertEqual(parse_qs(urlsplit(rows[product.pk]["stock_entry_url"]).query)["producto"], [str(product.pk)])
        self.passive_get(rows[product.pk]["stock_entry_url"])
        self.assertEqual(rows[historical.pk]["stock_entry_url"], "")
        self.assertTrue(rows[historical.pk]["kardex_url"])

    def test_inventory_rows_keep_original_dto_and_authorized_editor_and_lot_bridges(self):
        for company in self.empresas.values():
            response, _ = self.passive_get(reverse("agenda_mobile_inventory", args=[company.slug]))
            self.assertEqual([row["producto"] for row in response.context["mobile_inventory_rows"]], response.context["productos"])
            for row in response.context["mobile_inventory_rows"]:
                self.assertEqual(parse_qs(urlsplit(row["app_stock_entry_url"]).query)["producto"], [str(row["producto"]["id"])])
                self.passive_get(row["app_stock_entry_url"])
                self.passive_get(row["app_product_edit_url"])

    def test_inventory_permission_alone_authorizes_both_stock_forms_and_their_existing_return(self):
        for granular in (True, False):
            self.role(granular=granular, puede_inventario=True)
            for route in ("crear_lote_inventario", "traslado_inventario_farmaceutico"):
                response, _ = self.passive_get(self.url(route))
                back = reverse("agenda_mobile_inventory", args=[self.empresa.slug])
                self.assertEqual(response.context["app_stock_back_url"], back)
                self.passive_get(back)
                self.assertEqual(volver_stock_app(self.empresa).url, back)
            self.assertNotIn("catalogo_productos", self.tiles())
            self.assertFalse(productos_disponibles_app(self.request(), self.empresa))

    def test_product_writer_only_return_is_own_authorized_form_not_a_directory(self):
        for flag, route in (("puede_crear_productos", "crear_producto_facturacion"), ("puede_editar_productos", "editar_producto_facturacion")):
            self.role(**{flag: True})
            product = self.productos[self.empresa.slug]
            url = self.url(route, obj=product if flag == "puede_editar_productos" else None)
            response, _ = self.passive_get(url)
            self.assertFalse(response.context["can_view_products"])
            self.assertEqual(response.context["app_products_url"], "")
            result = volver_gestion_producto_app(self.request(), self.empresa, product if flag == "puede_editar_productos" else None)
            self.assertEqual(result.url, url)
            self.assertNotIn("catalogo_productos", self.tiles())

    def test_quick_transfer_permission_does_not_invent_inventory_read_for_granular_actor(self):
        self.role(puede_transferir_inventario=True)
        response, _ = self.passive_get(self.url("traslado_rapido_farmaceutico"))
        self.assertTemplateUsed(response, "facturacion/mobile_transfer.html")
        self.assertIn("transferencias", self.tiles())
        self.assertNotIn("kardex", self.tiles())
        self.assertFalse(stock_disponible_app(self.request(), self.empresa))
        with self.assertRaises(PermissionDenied): self.direct(self.url("crear_lote_inventario"))

    def test_supplier_purchase_links_four_companies_both_roles_use_original_aggregates(self):
        for user in self.usuarios.values():
            self.login(user)
            for company in self.empresas.values():
                response, _ = self.passive_get(self.url("ver_proveedor_facturacion", company, self.proveedores[company.slug]))
                self.assertTrue(response.context["can_view_purchase"])
                self.assertEqual(set(response.context["resumen"]), {"compras", "aplicadas", "borradores", "monto", "saldo_pendiente", "por_pagar", "vencidas"})
                rows = response.context["mobile_supplier_purchase_items"]
                self.assertEqual([row["compra"] for row in rows], list(response.context["compras"]))
                for row in rows:
                    self.assertEqual(set(row), {"compra", "detalle_url", "pdf_url"})
                    self.assertEqual(row["detalle_url"], self.url("ver_compra", company, row["compra"]))
                    self.assertEqual(row["pdf_url"], self.url("descargar_compra_pdf", company, row["compra"]))
                    self.passive_get(row["detalle_url"])
                    pdf, _ = self.passive_get(row["pdf_url"])
                    self.assertEqual(pdf.content, self.PDF)

    def test_supplier_only_keeps_all_cards_and_summaries_but_no_purchase_or_pdf_links(self):
        for granular in (True, False):
            for company in self.empresas.values():
                self.role(company, granular=granular, puede_proveedores=True)
                response, _ = self.passive_get(self.url("ver_proveedor_facturacion", company, self.proveedores[company.slug]))
                self.assertFalse(response.context["can_view_purchase"])
                self.assertEqual(len(response.context["mobile_supplier_purchase_items"]), 1)
                self.assertEqual(response.context["resumen"]["compras"], 1)
                for row in response.context["mobile_supplier_purchase_items"]:
                    self.assertEqual((row["detalle_url"], row["pdf_url"]), ("", ""))
                hrefs = _OperationMarkup(response.content).hrefs
                self.assertNotIn(self.url("ver_compra", company, self.compras[company.slug]), hrefs)
                self.assertNotIn(self.url("descargar_compra_pdf", company, self.compras[company.slug]), hrefs)
                self.assertNotIn("compras", self.tiles(company))
                with self.assertRaises(PermissionDenied):
                    self.direct(self.url("ver_compra", company, self.compras[company.slug]))

    def test_supplier_writers_have_no_history_reads_and_editor_only_tile_stays_pending(self):
        for flag, route in (("puede_crear_proveedores", "crear_proveedor_facturacion"), ("puede_editar_proveedores", "editar_proveedor_facturacion")):
            self.role(**{flag: True})
            provider = self.proveedores[self.empresa.slug]
            url = self.url(route, obj=provider if flag == "puede_editar_proveedores" else None)
            response, queries = self.passive_get(url)
            self.assertFalse(response.context["can_view_suppliers"])
            self.assertFalse(any(f'"{CompraInventario._meta.db_table}"' in query["sql"] for query in queries))
            result = volver_proveedor_app(self.request(), self.empresa, provider, editar=flag == "puede_editar_proveedores")
            self.assertEqual(result.url, url)
            tile = self.tiles()["proveedores"]
            self.assertEqual(tile["estado"], "pendiente" if flag == "puede_editar_proveedores" else "disponible")
            self.assertEqual(tile["url"], "" if flag == "puede_editar_proveedores" else url)

    def test_purchase_consultation_never_offers_egreso_or_financial_writes_even_with_permission(self):
        for granular in (True, False):
            self.role(granular=granular, puede_compras=True, puede_egresos=True)
            response, _ = self.passive_get(self.url("ver_compra", obj=self.compras[self.empresa.slug]))
            self.assertFalse(response.context["can_view_egresos"])
            self.assertFalse(response.context["can_view_supplier"])
            self.assertEqual(response.context["app_purchase_supplier_url"], "")
            self.assertEqual(len(response.context["mobile_purchase_payments"]), 1)
            for item in response.context["mobile_purchase_payments"]:
                self.assertEqual((item["comprobante_numero"], item["comprobante_url"], item["comprobante_pdf_url"]), ("", "", ""))
            for action in ("create", "edit", "apply", "void", "delete", "pay"):
                self.assertFalse(response.context[f"can_{action}_purchase"])
                self.assertEqual(response.context[f"app_purchase_{action}_url"], "")
            self.assertFalse(response.context["can_revert_purchase_payment"])
            self.assertNotIn("egresos", self.tiles())

    def test_purchase_supplier_back_bridge_requires_real_read_permission(self):
        purchase = self.compras[self.empresa.slug]
        response, _ = self.passive_get(self.url("ver_compra", obj=purchase))
        self.assertTrue(response.context["can_view_supplier"])
        expected = self.url("ver_proveedor_facturacion", obj=self.proveedores[self.empresa.slug])
        self.assertEqual(response.context["app_purchase_supplier_url"], expected)
        self.passive_get(expected)
        self.role(puede_compras=True, puede_editar_proveedores=True)
        response, _ = self.passive_get(self.url("ver_compra", obj=purchase))
        self.assertFalse(response.context["can_view_supplier"])
        self.assertEqual(response.context["app_purchase_supplier_url"], "")

    def test_receipt_only_documentary_chain_and_pdf_do_not_grant_invoice_or_payments(self):
        for granular in (True, False):
            for company in self.empresas.values():
                self.role(company, granular=granular, puede_recibos=True)
                receipt = self.recibos[company.slug]
                for route in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                    response, _ = self.passive_get(self.url(route, company, None if route == "recibos_dashboard" else receipt))
                    if route == "ver_recibo":
                        self.assertFalse(response.context["can_view_invoice"])
                        self.assertNotIn(self.url("ver_factura", company, self.facturas[company.slug]), _OperationMarkup(response.content).hrefs)
                    if route == "descargar_recibo_pdf": self.assertEqual(response.content, self.PDF)
                self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_registrar_pagos_clientes", company))

    def test_receipt_share_entry_is_only_original_protected_pdf_in_detail(self):
        receipt = self.recibos[self.empresa.slug]
        detail, _ = self.passive_get(self.url("ver_recibo", obj=receipt))
        nodes = _OperationMarkup(detail.content).nodes
        entry = next(attrs for _, attrs in nodes if attrs.get("id") == "mobileReceiptShare")
        self.assertEqual(entry["data-pdf-url"], self.url("descargar_recibo_pdf", obj=receipt))
        self.assertNotIn("recipient", entry)
        listing, _ = self.passive_get(self.url("recibos_dashboard"))
        self.assertNotContains(listing, 'id="mobileReceiptShare"')

    def test_local_role_override_and_explicit_none_remove_navigation_without_grants(self):
        provider_role = self.role(puede_proveedores=True)
        self.assertEqual(set(self.tiles()).intersection(self.TILES), {"proveedores"})
        self.assertEqual(Usuario.objects.get(pk=self.actor.pk).rol_para_empresa(self.empresa).pk, provider_role.pk)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.actor, empresa=self.empresa).update(rol_sistema=None)
        self.assertTrue(set(self.tiles()).isdisjoint(self.TILES))
        self.assertFalse(gestion_productos_disponible_app(self.request(), self.empresa)["crear"])
        self.assertFalse(proveedores_disponibles_app(self.request(), self.empresa)["ver"])

    def test_membership_license_module_and_profile_gates_deny_before_product_or_stock_seed(self):
        company = self.empresa
        cases = (("activa", False), ("estado_licencia", "suspendida"), ("tipo_solucion", "erp"))
        for field, value in cases:
            original = getattr(company, field)
            Empresa.objects.filter(pk=company.pk).update(**{field: value})
            selected = Empresa.objects.get(pk=company.pk)
            self.assertFalse(stock_disponible_app(self.request(selected), selected))
            self.assertFalse(productos_disponibles_app(self.request(selected), selected))
            Empresa.objects.filter(pk=company.pk).update(**{field: original})
        EmpresaModulo.objects.filter(empresa=company, modulo=self.billing).update(activo=False)
        self.assertFalse(stock_disponible_app(self.request(), company))
        self.assertFalse(productos_disponibles_app(self.request(), company))
        EmpresaModulo.objects.filter(empresa=company, modulo=self.billing).update(activo=True)
        outsider = Usuario.objects.create_user(username="op-nav-no-member", password="test-only", empresa=self.externa, rol_sistema=self.granular)
        self.login(outsider)
        before = self.snapshot()
        with patch.object(views, "_asegurar_bodegas_farmaceuticas") as seed:
            with self.assertRaises(PermissionDenied): self.direct(self.url("crear_lote_inventario"))
            seed.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_foreign_bridge_object_ids_are_denied_without_data_mutations(self):
        foreign = self.empresas["medical_spa"]
        urls = (self.url("editar_producto_facturacion", obj=self.productos[foreign.slug]), self.url("ver_proveedor_facturacion", obj=self.proveedores[foreign.slug]), self.url("ver_compra", obj=self.compras[foreign.slug]), self.url("ver_recibo", obj=self.recibos[foreign.slug]))
        before = self.snapshot()
        for url in urls:
            with self.assertRaises((PermissionDenied, Http404)): self.direct(url)
        self.assertEqual(self.snapshot(), before)

    def test_native_navigation_uses_app_routes_no_desktop_fallback_or_unrelated_actions(self):
        for route, obj in (("productos_facturacion", None), ("proveedores_facturacion", None), ("ver_proveedor_facturacion", self.proveedores[self.empresa.slug]), ("compras_dashboard", None), ("ver_compra", self.compras[self.empresa.slug]), ("recibos_dashboard", None), ("ver_recibo", self.recibos[self.empresa.slug])):
            response, _ = self.passive_get(self.url(route, obj=obj))
            self.assertNotContains(response, "<table")
            for href in _OperationMarkup(response.content).hrefs:
                if not href.startswith("/"): continue
                path = urlsplit(href).path
                match = resolve(path)
                if match.url_name in set(self.TILES.values()) | {"ver_compra", "descargar_compra_pdf", "ver_proveedor_facturacion", "editar_proveedor_facturacion", "editar_producto_facturacion", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"}:
                    self.assertEqual(parse_qs(urlsplit(href).query).get("app"), ["1"])
            self.assertNotContains(response, '<script>NO_EJECUTAR</script>')

    def test_original_web_supplier_and_purchase_templates_and_financial_links_are_unchanged(self):
        self.role(puede_proveedores=True, puede_compras=True, puede_egresos=True)
        supplier = self.client.get(self.url("ver_proveedor_facturacion", obj=self.proveedores[self.empresa.slug], mobile=False))
        purchase = self.client.get(self.url("ver_compra", obj=self.compras[self.empresa.slug], mobile=False))
        self.assertTemplateUsed(supplier, "facturacion/ver_proveedor_cxp_premium.html")
        self.assertTemplateUsed(purchase, "facturacion/ver_compra_cxp_premium.html")
        self.assertTemplateNotUsed(supplier, "facturacion/mobile_supplier_detail.html")
        self.assertTemplateNotUsed(purchase, "facturacion/mobile_purchase_detail.html")
        self.assertContains(purchase, reverse("ver_comprobante_egreso", args=[self.empresa.slug, self.egresos[self.empresa.slug].pk]))

    def test_receipt_purchase_and_transfer_capabilities_do_not_offer_calendar_or_patient_access(self):
        self.role(puede_recibos=True, puede_compras=True, puede_transferir_inventario=True)
        items = self.tiles()
        self.assertTrue({"recibos", "compras", "transferencias"}.issubset(items))
        self.assertTrue({"expediente", "registrar_historia", "evaluaciones", "tratamientos", "terapias", "registrar_terapia", "camara", "postquirurgicas", "usuarios"}.isdisjoint(items))
        user = Usuario.objects.get(pk=self.actor.pk)
        for flag in ("puede_ver_historia_clinica", "puede_ver_pacientes", "puede_citas", "puede_ver_facturas", "puede_registrar_pagos_clientes"):
            self.assertFalse(user.tiene_permiso_erp(flag, self.empresa), flag)

    def test_company_administrator_original_bypass_keeps_company_scope_and_same_destinations(self):
        admin = Usuario.objects.create_user(username="op-nav-company-admin", password="test-only", empresa=self.empresa, es_administrador_empresa=True, last_login=self.NOW)
        admin.empresas_acceso.add(*self.empresas.values())
        self.login(admin)
        for company in self.empresas.values():
            items = self.tiles(company)
            for key, route in self.TILES.items():
                self.assertEqual(items[key]["url"], self.url(route, company))
            self.passive_get(self.url("productos_facturacion", company))
        self.assertFalse(stock_disponible_app(self.request(self.externa), self.externa))
        self.assertFalse(productos_disponibles_app(self.request(self.externa), self.externa))
        admin.refresh_from_db()
        self.assertIsNone(admin.rol_sistema_id)
        self.assertFalse(admin.is_superuser)
