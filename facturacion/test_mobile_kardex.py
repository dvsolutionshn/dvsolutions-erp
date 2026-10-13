"""El historial móvil conserva el Kardex original sin reconstruir existencias."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .models import BodegaInventario, Cliente, CompraInventario, EntradaInventarioDocumento, ExistenciaLoteBodega, Factura, InventarioProducto, LoteInventario, MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoCompra, PagoFactura, Producto, Proveedor
from .services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileKardexIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.productos, cls.bodegas, cls.destinos, cls.proveedores, cls.lotes, cls.generales, cls.traslados = {}, {}, {}, {}, {}, {}, {}, {}
        cls.rol = RolSistema.objects.create(nombre="Sólo Kardex QA", codigo="mobile-kardex-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_inventario=True)
        cls.instant = timezone.make_aware(datetime(2026, 10, 12, 12, 0))
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Clínica Kardex QA {slug}", slug=slug, rtn=f"KARDEX-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            cls.productos[slug] = Producto.objects.create(empresa=empresa, nombre=f"Producto Kardex {slug}", codigo=f"MKDX-{index}", precio=Decimal("10"), controla_inventario=True)
            cls.bodegas[slug] = BodegaInventario.objects.create(empresa=empresa, nombre=f"Bodega histórica {slug}", tipo="principal", activa=False)
            cls.destinos[slug] = BodegaInventario.objects.create(empresa=empresa, nombre=f"Destino histórico {slug}", tipo="otra")
            cls.proveedores[slug] = Proveedor.objects.create(empresa=empresa, nombre=f"Proveedor histórico {slug}", activo=False)
            cls.lotes[slug] = LoteInventario.objects.create(empresa=empresa, producto=cls.productos[slug], proveedor=cls.proveedores[slug], numero_lote=f"HIST-{slug}", activo=False, fecha_vencimiento=date(2020, 1, 1))
        cls.empresa = cls.empresas["hospital_mia"]
        cls.usuario = Usuario.objects.create_user(username="mobile-kardex", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        for empresa in cls.empresas.values():
            slug = empresa.slug
            cls.generales[slug] = MovimientoInventario.objects.create(empresa=empresa, producto=cls.productos[slug], bodega=cls.bodegas[slug], usuario=cls.usuario, fecha=cls.instant, tipo="ajuste_entrada", cantidad=Decimal("10"), existencia_anterior=Decimal("3"), existencia_resultante=Decimal("13"), referencia=f"GENERAL-{slug}")
            cls.traslados[slug] = [
                MovimientoLoteBodega.objects.create(empresa=empresa, lote=cls.lotes[slug], bodega=bodega, usuario=cls.usuario, fecha=cls.instant, tipo=tipo, cantidad=Decimal("2"), existencia_anterior=antes, existencia_resultante=despues, referencia=f"TRASLADO-{slug}")
                for bodega, tipo, antes, despues in ((cls.bodegas[slug], "traslado_salida", Decimal("13"), Decimal("11")), (cls.destinos[slug], "traslado_entrada", Decimal("0"), Decimal("2")))
            ]

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target); mocked = transport.start()
            self.addCleanup(transport.stop); self.addCleanup(mocked.assert_not_called)

    def url(self, empresa=None, mobile=True, **query):
        parameters = {"app": "1"} if mobile else {}
        parameters.update(query)
        return reverse("kardex_inventario", args=[(empresa or self.empresa).slug])+("?"+urlencode(parameters) if parameters else "")

    def role(self, granular=True, empresa=None, **permissions):
        role = RolSistema.objects.create(nombre="Kardex restringido", codigo=f"mobile-kardex-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": role})
        return role

    def directo(self, empresa=None, method="get", data=None, url=None, user=None):
        url = url or self.url(empresa)
        request = RequestFactory().post(url, data or {}) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def snapshot(self):
        # Every installed model, not just movement counts: a passive page must
        # not seed configuration, stock, finance, audit or clinical records.
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values())) for model in apps.get_models(include_auto_created=True))

    def assert_denied(self, url=None, method="get", data=None):
        before = self.snapshot()
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(url=url, method=method, data=data)
        self.assertEqual(self.snapshot(), before)

    def general(self, tipo="entrada", **extra):
        data = {"empresa": self.empresa, "producto": self.productos[self.empresa.slug], "tipo": tipo, "fecha": self.instant, "cantidad": Decimal("1"), "existencia_anterior": Decimal("0"), "existencia_resultante": Decimal("1"), "referencia": f"KARDEX-QA-{MovimientoInventario.objects.count()}"}
        data.update(extra)
        return MovimientoInventario.objects.create(**data)

    def lot(self, tipo="regalia", **extra):
        data = {"empresa": self.empresa, "lote": self.lotes[self.empresa.slug], "bodega": self.bodegas[self.empresa.slug], "tipo": tipo, "fecha": self.instant, "cantidad": Decimal("1"), "existencia_anterior": Decimal("3"), "existencia_resultante": Decimal("2"), "referencia": f"LOTE-QA-{MovimientoLoteBodega.objects.count()}"}
        data.update(extra)
        return MovimientoLoteBodega.objects.create(**data)

    def docs(self, empresa=None):
        empresa = empresa or self.empresa
        with suspender_sincronizacion_clientes_compartidos():
            cliente = Cliente.objects.create(empresa=empresa, nombre=f"Cliente documentos Kardex {empresa.slug}", rtn=f"0801{Cliente.objects.count()+100:010d}")
        factura = Factura.objects.create(empresa=empresa, cliente=cliente, estado="borrador", total=Decimal("10"), total_lempiras=Decimal("10"))
        nota = NotaCredito.objects.create(empresa=empresa, cliente=cliente, factura_origen=factura, estado="borrador", total=Decimal("1"))
        entrada = EntradaInventarioDocumento.objects.create(empresa=empresa, referencia=f"ENT-KARDEX-{empresa.slug}")
        compra = CompraInventario.objects.create(empresa=empresa, proveedor_nombre="Proveedor documental sintético", estado="anulada")
        return {"factura_id": factura.pk, "nota_credito_id": nota.pk, "entrada_documento_id": entrada.pk, "compra_documento_id": compra.pk}

    def test_four_companies_both_role_types_use_app_without_replacing_pc(self):
        for granular in (True, False):
            for empresa in self.empresas.values():
                self.role(granular=granular, empresa=empresa, puede_inventario=True)
                before = self.snapshot()
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "facturacion/mobile_kardex_list.html")
                self.assertNotContains(response, "<table")
                self.assertContains(response, f"GENERAL-{empresa.slug}")
                for otra in self.empresas.values():
                    if otra.pk != empresa.pk:
                        self.assertNotContains(response, f"GENERAL-{otra.slug}")
                web = self.client.get(self.url(empresa, mobile=False))
                self.assertTemplateUsed(web, "facturacion/kardex_premium.html")
                self.assertEqual(response.context["resumen"], web.context["resumen"])
                self.assertEqual(self.snapshot(), before)

    def test_inventory_is_exact_permission_not_products_transfer_or_financial_read(self):
        for granular in (True, False):
            for flag in ("puede_productos", "puede_crear_productos", "puede_transferir_inventario", "puede_ver_facturas", "puede_ver_calendario"):
                self.role(granular=granular, **{flag: True})
                request = RequestFactory().get(self.url()); request.user = Usuario.objects.get(pk=self.usuario.pk)
                from .mobile_kardex import autorizar_kardex_app
                with CaptureQueriesContext(connection) as captured:
                    with self.assertRaises(PermissionDenied):
                        autorizar_kardex_app(request, self.empresa)
                self.assertFalse(any('"facturacion_movimientoinventario"' in row["sql"] or '"facturacion_movimientolotebodega"' in row["sql"] for row in captured))
                self.assert_denied()

    def test_capacity_helper_is_pure_and_does_not_require_optin_or_other_actions(self):
        from .mobile_kardex import kardex_disponible_app
        request = RequestFactory().get(self.url(mobile=False)); request.user = self.usuario
        before = self.snapshot()
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(kardex_disponible_app(request, self.empresa))
        for model in (MovimientoInventario, MovimientoLoteBodega, Producto, InventarioProducto, ExistenciaLoteBodega):
            self.assertFalse(any(f'"{model._meta.db_table}"' in row["sql"] for row in captured))
        self.assertEqual(self.snapshot(), before)

    def test_membership_module_active_and_license_are_enforced_without_middleware(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.assert_denied()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for changes in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**changes)
            self.assert_denied()
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.assert_denied(url=self.url(self.empresas["medical_spa"]))

    def test_superuser_license_exception_still_requires_active_company_and_module(self):
        supervisor = Usuario.objects.create_superuser(username="kardex-super", password="test-only")
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.assertEqual(self.directo(user=supervisor).status_code, 200)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(PermissionDenied):
            self.directo(user=supervisor)

    def test_logged_out_request_cannot_read_history_or_seed_records(self):
        from .mobile_kardex import kardex_disponible_app
        request = RequestFactory().get(self.url()); request.user = AnonymousUser()
        before = self.snapshot()
        self.assertFalse(kardex_disponible_app(request, self.empresa))
        self.assertEqual(Client().get(self.url()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_exact_four_company_and_clinical_optin_leave_other_web_routes_unchanged(self):
        empresa = Empresa.objects.create(nombre="Otra Kardex QA", slug="otra_kardex", rtn="KARDEX-OTRA", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        self.assertTemplateUsed(self.client.get(self.url(empresa)), "facturacion/kardex_premium.html")
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="general")
        self.assertTemplateUsed(self.client.get(self.url()), "facturacion/kardex_premium.html")

    def test_get_and_head_are_no_store_and_have_zero_writes_across_all_models(self):
        before = self.snapshot()
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as captured:
                response = getattr(self.client, method)(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for row in captured))
            if method == "head":
                self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_no_inventory_configuration_or_bodegas_are_initialized_by_history(self):
        self.assertFalse(InventarioProducto.objects.exists())
        self.assertFalse(ExistenciaLoteBodega.objects.exists())
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "_obtener_inventario_producto", side_effect=AssertionError("stock seed")), patch.object(views, "_asegurar_bodegas_farmaceuticas", side_effect=AssertionError("bodega seed")), patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("config seed")):
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertEqual(self.client.head(self.url()).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_empty_history_is_readonly_without_stock_reconstruction(self):
        MovimientoInventario.objects.filter(empresa=self.empresa).delete()
        MovimientoLoteBodega.objects.filter(empresa=self.empresa).delete()
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["resumen"], {"total_movimientos": 0, "entradas": 0, "salidas": 0})
        self.assertEqual(response.context["mobile_kardex_items"], [])
        self.assertEqual(self.snapshot(), before)

    def test_post_and_unsafe_methods_are_denied_before_loading_history(self):
        for method in ("post", "put", "patch", "delete", "options"):
            self.assert_denied(method=method, data={"app": "1", "producto": self.productos[self.empresa.slug].pk, "cantidad": "20"})
        self.assert_denied(url=self.url(mobile=False), method="post", data={"app": "1"})

    def test_duplicate_unknown_query_parameters_are_not_ambiguous(self):
        queries = ("app=1&app=1", "app=0&app=1", "app=1&producto=1&producto=2", "app=1&tipo=entrada&tipo=entrada", "app=1&fecha_desde=2026-10-01&fecha_desde=2026-10-02", "app=1&fecha_hasta=2026-10-12&fecha_hasta=2026-10-12", "app=1&page=1&page=2", "app=1&q=hola", "app=1&accion=ajuste", "app=1&empresa=2", "app=1&bodega=1")
        for query in queries:
            self.assert_denied(url=self.url(mobile=False)+"?"+query)

    def test_product_and_page_ids_are_ascii_canonical_int64_without_fallback(self):
        for key in ("producto", "page"):
            for value in ("0", "-1", "01", "1.0", "1e2", " 1", "1 ", "١", "9223372036854775808", "9"*70, "none"):
                self.assert_denied(url=self.url(**{key: value}))
        self.assert_denied(url=self.url(page=""))

    def test_foreign_or_missing_selected_product_is_denied_before_history_queries(self):
        for product_id in (self.productos["medical_spa"].pk, 9223372036854775807):
            with CaptureQueriesContext(connection) as captured:
                with self.assertRaises(Http404):
                    self.directo(url=self.url(producto=product_id))
            self.assertFalse(any('"facturacion_movimientoinventario"' in row["sql"] for row in captured))

    def test_own_inactive_deleted_noncontrolled_product_filter_remains_usable(self):
        product = self.productos[self.empresa.slug]
        Producto.objects.filter(pk=product.pk).update(controla_inventario=False, activo=False, eliminado=True)
        before = self.snapshot()
        response = self.client.get(self.url(producto=product.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["producto_id"], str(product.pk))
        self.assertNotIn(product.pk, [p.pk for p in response.context["productos"]])
        self.assertEqual(response.context["mobile_kardex_total"], 3)
        self.assertEqual(self.snapshot(), before)

    def test_native_blank_product_type_and_dates_mean_original_all_filters(self):
        response = self.client.get(self.url(producto="", tipo="", fecha_desde="", fecha_hasta=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["resumen"], {"total_movimientos": 3, "entradas": 2, "salidas": 1})
        wrapped = self.client.get(self.url(fecha_desde=" 2026-10-12 ", fecha_hasta=" 2026-10-12 "))
        self.assertEqual(wrapped.status_code, 200)
        self.assertEqual((wrapped.context["fecha_desde"], wrapped.context["fecha_hasta"]), ("2026-10-12", "2026-10-12"))
        self.assertEqual(wrapped.context["resumen"], response.context["resumen"])

    def test_invalid_iso_dates_fail_without_500_or_querying_history(self):
        for key in ("fecha_desde", "fecha_hasta"):
            for value in ("2026-02-30", "2026-13-01", "12/10/2026", "20261012", "2026-1-01", "2026-10-12T00:00", "0000-01-01", "10000-01-01"):
                self.assert_denied(url=self.url(**{key: value}))

    def test_iso_dates_filter_original_datetime_day_inclusively(self):
        self.general(fecha=timezone.make_aware(datetime(2026, 10, 11, 23, 59)), referencia="ANTES-KARDEX")
        self.general(fecha=timezone.make_aware(datetime(2026, 10, 13, 0, 1)), referencia="DESPUES-KARDEX")
        response = self.client.get(self.url(fecha_desde="2026-10-12", fecha_hasta="2026-10-12"))
        self.assertEqual(response.context["mobile_kardex_total"], 3)
        self.assertNotContains(response, "ANTES-KARDEX")
        self.assertNotContains(response, "DESPUES-KARDEX")

    def test_reversed_dates_preserve_original_empty_result_not_swapped(self):
        before = self.snapshot()
        response = self.client.get(self.url(fecha_desde="2026-10-13", fecha_hasta="2026-10-11"))
        self.assertEqual(response.context["resumen"], {"total_movimientos": 0, "entradas": 0, "salidas": 0})
        self.assertEqual((response.context["fecha_desde"], response.context["fecha_hasta"]), ("2026-10-13", "2026-10-11"))
        self.assertEqual(self.snapshot(), before)

    def test_type_filter_uses_original_union_and_rejects_unknown_type(self):
        for tipo, _ in (*MovimientoInventario.TIPOS, ("traslado_entrada", "Traslado entrada"), ("traslado_salida", "Traslado salida")):
            self.assertEqual(self.client.get(self.url(tipo=tipo)).status_code, 200)
        self.assert_denied(url=self.url(tipo="ajuste"))
        self.assert_denied(url=self.url(tipo="inventado"))

    def test_original_summaries_are_counts_not_quantities_and_purchase_reversion_neither(self):
        self.general(tipo="reversion_compra", cantidad=Decimal("999"))
        response = self.client.get(self.url(tipo="reversion_compra"))
        self.assertEqual(response.context["resumen"], {"total_movimientos": 1, "entradas": 0, "salidas": 0})
        self.assertEqual(response.context["mobile_kardex_items"][0]["movimiento"].cantidad, Decimal("999"))

    def test_app_pages_all_history_after_summary_while_pc_still_only_150(self):
        MovimientoInventario.objects.bulk_create([MovimientoInventario(empresa=self.empresa, producto=self.productos[self.empresa.slug], tipo="entrada", cantidad=Decimal("100"), existencia_anterior=Decimal("0"), existencia_resultante=Decimal("100"), referencia=f"PAGINADO-{index:03d}", fecha=self.instant-timedelta(days=1)) for index in range(160)])
        before = self.snapshot()
        response = self.client.get(self.url(page="9", producto=self.productos[self.empresa.slug].pk))
        self.assertEqual((response.context["mobile_kardex_total"], response.context["mobile_kardex_page"], len(response.context["mobile_kardex_items"])), (163, 9, 3))
        self.assertEqual(response.context["resumen"], {"total_movimientos": 163, "entradas": 162, "salidas": 1})
        previous = parse_qs(urlsplit(response.context["mobile_kardex_previous_url"]).query)
        self.assertEqual(previous, {"app": ["1"], "producto": [str(self.productos[self.empresa.slug].pk)], "page": ["8"]})
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(len(web.context["movimientos"]), 150)
        self.assertEqual(web.context["resumen"], response.context["resumen"])
        self.assertEqual(self.snapshot(), before)

    def test_historical_lot_only_regalia_is_visible_without_general_record(self):
        record = self.lot(referencia="REGALIA-HISTORICA-QA")
        response = self.client.get(self.url(tipo="regalia"))
        self.assertEqual(response.context["resumen"], {"total_movimientos": 1, "entradas": 0, "salidas": 1})
        self.assertEqual(response.context["mobile_kardex_items"][0]["movimiento"].pk, record.pk)
        self.assertContains(response, "REGALIA-HISTORICA-QA")

    def test_current_regalia_deduplicates_exact_original_tuple_not_date_or_quantity(self):
        general = self.general(tipo="regalia", bodega=self.bodegas[self.empresa.slug], referencia="REGALIA-DUP-QA", cantidad=Decimal("9"))
        self.lot(referencia="REGALIA-DUP-QA", cantidad=Decimal("2"), fecha=self.instant-timedelta(days=10))
        response = self.client.get(self.url(tipo="regalia"))
        self.assertEqual(response.context["mobile_kardex_total"], 1)
        self.assertEqual([(item["origen"], item["movimiento"].pk) for item in response.context["mobile_kardex_items"]], [("general", general.pk)])
        self.assertEqual(response.context["resumen"]["salidas"], 1)

    def test_blank_reference_and_different_warehouse_do_not_deduplicate(self):
        self.general(tipo="regalia", bodega=self.bodegas[self.empresa.slug], referencia="")
        self.lot(referencia="")
        self.general(tipo="regalia", bodega=self.bodegas[self.empresa.slug], referencia="MISMA-REF-QA")
        self.lot(bodega=self.destinos[self.empresa.slug], referencia="MISMA-REF-QA")
        response = self.client.get(self.url(tipo="regalia"))
        self.assertEqual(response.context["mobile_kardex_total"], 4)

    def test_same_ids_across_models_and_equal_dates_preserve_stable_order_and_dom_identity(self):
        self.assertEqual(self.generales[self.empresa.slug].pk, self.traslados[self.empresa.slug][0].pk)
        response = self.client.get(self.url())
        self.assertEqual([(item["origen"], item["movimiento"].pk) for item in response.context["mobile_kardex_items"]], [("lote", self.traslados[self.empresa.slug][1].pk), ("general", self.generales[self.empresa.slug].pk), ("lote", self.traslados[self.empresa.slug][0].pk)])
        self.assertContains(response, f'id="kardex-general-{self.generales[self.empresa.slug].pk}"', count=1)
        self.assertContains(response, f'id="kardex-lote-{self.traslados[self.empresa.slug][0].pk}"', count=1)

    def test_negative_and_zero_historical_snapshots_are_displayed_without_new_math(self):
        record = self.general(tipo="entrada", cantidad=Decimal("-2.50"), existencia_anterior=Decimal("-7.25"), existencia_resultante=Decimal("0"))
        self.lot(cantidad=Decimal("0"), existencia_anterior=Decimal("50"), existencia_resultante=Decimal("11"))
        before = self.snapshot()
        response = self.client.get(self.url())
        row = next(item["movimiento"] for item in response.context["mobile_kardex_items"] if item["origen"] == "general" and item["movimiento"].pk == record.pk)
        self.assertEqual((row.cantidad, row.existencia_anterior, row.existencia_resultante), (Decimal("-2.50"), Decimal("-7.25"), Decimal("0")))
        self.assertEqual(self.snapshot(), before)

    def test_inactive_deleted_products_lots_warehouses_and_multi_company_actor_are_historical(self):
        actor = Usuario.objects.create_user(username="kardex-historical-actor", password="test-only", empresa=self.empresas["medical_spa"], is_active=False)
        Producto.objects.filter(pk=self.productos[self.empresa.slug].pk).update(activo=False, eliminado=True)
        MovimientoInventario.objects.filter(empresa=self.empresa).update(usuario=actor)
        MovimientoLoteBodega.objects.filter(empresa=self.empresa).update(usuario=actor)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_company_corruption_and_unmerged_lot_types_do_not_block_history(self):
        MovimientoInventario.objects.filter(empresa=self.empresas["medical_spa"]).update(producto=self.productos[self.empresa.slug])
        other_lot = LoteInventario.objects.create(empresa=self.empresa, producto=self.productos["medical_spa"], numero_lote="IGNORADO-FK-QA")
        self.lot(tipo="entrada", lote=other_lot, bodega=self.bodegas["medical_spa"])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url()).context["mobile_kardex_total"], 3)

    def test_all_own_general_foreign_relations_are_denied_before_hidden_filters(self):
        record = self.generales[self.empresa.slug]
        for field, foreign, original in (("producto_id", self.productos["medical_spa"].pk, record.producto_id), ("bodega_id", self.bodegas["medical_spa"].pk, record.bodega_id)):
            MovimientoInventario.objects.filter(pk=record.pk).update(**{field: foreign})
            self.assert_denied(url=self.url(tipo="regalia", page="2"))
            MovimientoInventario.objects.filter(pk=record.pk).update(**{field: original})

    def test_general_orphan_product_warehouse_or_user_cannot_be_hidden_by_join(self):
        record = self.generales[self.empresa.slug]
        for field, original in (("producto_id", record.producto_id), ("bodega_id", record.bodega_id), ("usuario_id", record.usuario_id)):
            try:
                with connection.constraint_checks_disabled():
                    MovimientoInventario.objects.filter(pk=record.pk).update(**{field: 999999999})
                self.assert_denied(url=self.url(fecha_desde="2027-01-01"))
            finally:
                MovimientoInventario.objects.filter(pk=record.pk).update(**{field: original})

    def test_lot_movements_require_own_lot_product_provider_and_warehouse_before_filters(self):
        record = self.traslados[self.empresa.slug][0]; lote = self.lotes[self.empresa.slug]
        cases = ((MovimientoLoteBodega, record.pk, "lote_id", self.lotes["medical_spa"].pk, record.lote_id), (MovimientoLoteBodega, record.pk, "bodega_id", self.bodegas["medical_spa"].pk, record.bodega_id), (LoteInventario, lote.pk, "empresa_id", self.empresas["medical_spa"].pk, lote.empresa_id), (LoteInventario, lote.pk, "producto_id", self.productos["medical_spa"].pk, lote.producto_id), (LoteInventario, lote.pk, "proveedor_id", self.proveedores["medical_spa"].pk, lote.proveedor_id))
        for model, pk, field, foreign, original in cases:
            model.objects.filter(pk=pk).update(**{field: foreign})
            self.assert_denied(url=self.url(tipo="entrada"))
            model.objects.filter(pk=pk).update(**{field: original})

    def test_lot_orphan_relations_and_actors_are_detected_without_inner_join_masking(self):
        record = self.traslados[self.empresa.slug][0]; lote = self.lotes[self.empresa.slug]
        cases = ((MovimientoLoteBodega, record.pk, "lote_id", record.lote_id), (MovimientoLoteBodega, record.pk, "bodega_id", record.bodega_id), (MovimientoLoteBodega, record.pk, "usuario_id", record.usuario_id), (LoteInventario, lote.pk, "producto_id", lote.producto_id), (LoteInventario, lote.pk, "proveedor_id", lote.proveedor_id))
        for model, pk, field, original in cases:
            try:
                with connection.constraint_checks_disabled():
                    model.objects.filter(pk=pk).update(**{field: 999999999})
                self.assert_denied(url=self.url(tipo="entrada", page="2"))
            finally:
                model.objects.filter(pk=pk).update(**{field: original})

    def test_general_optional_financial_documents_must_exist_and_belong_to_company(self):
        own, foreign = self.docs(), self.docs(self.empresas["medical_spa"])
        record = self.generales[self.empresa.slug]
        for field in own:
            MovimientoInventario.objects.filter(pk=record.pk).update(**{field: own[field]})
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            MovimientoInventario.objects.filter(pk=record.pk).update(**{field: foreign[field]})
            self.assert_denied(url=self.url(tipo="regalia"))
            try:
                with connection.constraint_checks_disabled():
                    MovimientoInventario.objects.filter(pk=record.pk).update(**{field: 999999999})
                self.assert_denied()
            finally:
                MovimientoInventario.objects.filter(pk=record.pk).update(**{field: None})

    def test_lot_optional_invoice_is_scoped_but_null_invoice_and_actor_are_allowed(self):
        own, foreign = self.docs(), self.docs(self.empresas["medical_spa"])
        record = self.traslados[self.empresa.slug][0]
        MovimientoLoteBodega.objects.filter(pk=record.pk).update(factura_id=own["factura_id"])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        MovimientoLoteBodega.objects.filter(pk=record.pk).update(factura_id=foreign["factura_id"])
        self.assert_denied()
        MovimientoLoteBodega.objects.filter(pk=record.pk).update(factura=None, usuario=None)
        MovimientoInventario.objects.filter(empresa=self.empresa).update(bodega=None, usuario=None)
        LoteInventario.objects.filter(pk=self.lotes[self.empresa.slug].pk).update(proveedor=None)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_document_guard_does_not_read_or_validate_unused_money_customer_or_payments(self):
        own = self.docs(); foreign = self.docs(self.empresas["medical_spa"])
        foreign_client = Factura.objects.get(pk=foreign["factura_id"]).cliente_id
        Factura.objects.filter(pk=own["factura_id"]).update(cliente_id=foreign_client, total=Decimal("-10"))
        NotaCredito.objects.filter(pk=own["nota_credito_id"]).update(cliente_id=foreign_client)
        MovimientoInventario.objects.filter(pk=self.generales[self.empresa.slug].pk).update(**own)
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(self.url()).status_code, 200)
        selects = [row["sql"] for row in captured if row["sql"].lstrip().upper().startswith("SELECT")]
        for model in (Cliente, PagoFactura, PagoCompra):
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects))

    def test_mobile_has_only_get_filters_no_post_financial_or_pc_stock_links(self):
        response = self.client.get(self.url())
        self.assertNotContains(response, 'method="post"')
        self.assertNotContains(response, 'href=""')
        self.assertNotContains(response, f'href="{reverse("inventario_facturacion", args=[self.empresa.slug])}"')
        for endpoint in ("ajustar_inventario", "entrada_inventario", "traslado_inventario_farmaceutico"):
            self.assertNotContains(response, f'href="{reverse(endpoint, args=[self.empresa.slug])}"')
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertContains(response, reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))

    def test_hub_exposes_history_without_calendar_product_or_patient_grants(self):
        for granular in (True, False):
            self.role(granular=granular, puede_inventario=True)
            response = self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug]))
            self.assertEqual(response.status_code, 200)
            item = next(item for item in response.json()["modulos"] if item["key"] == "kardex")
            self.assertEqual(item["url"], self.url())
            self.assertFalse(any(item["key"] in {"agenda", "pacientes", "facturacion"} for item in response.json()["modulos"]))
        self.role(puede_productos=True)
        self.assertFalse(any(item["key"] == "kardex" for item in self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug])).json()["modulos"]))

    def test_native_metadata_links_escape_filter_values_without_unknown_js_or_xss(self):
        self.assert_denied(url=self.url(tipo='<script>alert("QA")</script>'))
        record = self.general(observacion='<script>alert("QA")</script>', referencia="<b>Referencia literal</b>")
        response = self.client.get(self.url(producto=record.producto_id))
        self.assertNotContains(response, '<script>alert("QA")</script>')
        self.assertNotContains(response, "<b>Referencia literal</b>")

    def test_valid_csrf_cannot_bypass_readonly_and_unflagged_pc_post_remains_original(self):
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario)
        before = self.snapshot()
        token = "a"*32; strict.cookies["csrftoken"] = token
        response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": token, "accion": "ajustar", "cantidad": "999"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        web = self.client.post(self.url(mobile=False), {"accion": "ignorada"})
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "facturacion/kardex_premium.html")
        self.assertEqual(self.snapshot(), before)
