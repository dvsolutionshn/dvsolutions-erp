"""Read-only mobile purchases retain the existing projections and PDF engine."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from clinica.models import ConfiguracionClinica, Paciente, ProfesionalSalud, RecetaMedica
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM, OpcionServicioAgenda
from . import views
from .models import BodegaInventario, CAI, Cliente, ClienteContable, CompraInventario, ComprobanteEgresoCompra, ConfiguracionFacturacionEmpresa, CuentaAcumuladoCompra, EntradaInventarioDocumento, Factura, InventarioProducto, LineaCompraInventario, MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoCompra, PagoFactura, Producto, Proveedor, ReciboPago, TipoImpuesto


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePurchasesIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ROUTES = ("compras_dashboard", "ver_compra", "descargar_compra_pdf")
    PDF = b"%PDF-1.4\nSynthetic unchanged purchase renderer bytes\n%%EOF"

    @classmethod
    def setUpTestData(cls):
        cls.module, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.role = RolSistema.objects.create(nombre="Sólo consulta compras QA", codigo="mobile-purchases-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_compras=True)
        cls.empresas, cls.accounts, cls.suppliers, cls.products, cls.purchases, cls.lines, cls.payments, cls.warehouses, cls.movements = {}, {}, {}, {}, {}, {}, {}, {}, {}
        cls.tax = TipoImpuesto.objects.create(nombre="Impuesto global compras QA", porcentaje=Decimal("15"))
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(nombre=f"Clínica compras QA {slug}", slug=slug, rtn=f"MCOM-{index}", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=company, modulo=cls.module)
            cls.empresas[slug] = company
            account = CuentaContable.objects.create(empresa=company, codigo="1101-PUR", nombre=f"Contable compras {slug}", tipo="activo")
            cls.accounts[slug] = CuentaFinanciera.objects.create(empresa=company, nombre=f"Banco compras {slug}", cuenta_contable=account)
            supplier = Proveedor.objects.create(empresa=company, nombre=f"Proveedor actual compras {slug}", cuenta_contable=account)
            cls.suppliers[slug] = supplier
            product = Producto.objects.create(empresa=company, nombre=f"Producto compras {slug}", codigo=f"MCOM-{index}", precio=Decimal("100"))
            cls.products[slug] = product
            purchase = CompraInventario.objects.create(empresa=company, proveedor=supplier, proveedor_nombre=f"Proveedor histórico compras {slug}", referencia_documento=f"PURREF-{slug}", fecha_documento=date(2026, 10, 1), condicion_pago="credito", dias_credito=15, estado="aplicada", cuenta_financiera_pago=cls.accounts[slug], observacion="Observación histórica\nSegunda línea conservada")
            cls.purchases[slug] = purchase
            cls.lines[slug] = LineaCompraInventario.objects.create(compra=purchase, producto=product, cantidad=Decimal("2"), costo_unitario=Decimal("100"), descuento_porcentaje=Decimal("10"), impuesto=cls.tax, comentario="Línea histórica")
            cls.payments[slug] = PagoCompra.objects.create(compra=purchase, monto=Decimal("50"), fecha=date(2026, 10, 2), metodo="cheque", cuenta_financiera=cls.accounts[slug], referencia=f"PAGO-{slug}")
            cls.warehouses[slug] = BodegaInventario.objects.create(empresa=company, nombre=f"Bodega compras {slug}", tipo="principal")
            cls.movements[slug] = MovimientoInventario.objects.create(empresa=company, compra_documento=purchase, producto=product, bodega=cls.warehouses[slug], tipo="entrada_compra", cantidad=Decimal("2"), existencia_anterior=Decimal("0"), existencia_resultante=Decimal("2"))
        cls.empresa = cls.empresas["hospital_mia"]
        cls.purchase = cls.purchases[cls.empresa.slug]
        cls.usuario = Usuario.objects.create_user(username="mobile-purchases", password="test-only", empresa=cls.empresa, rol_sistema=cls.role)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        pdf = patch.object(views, "HTML")
        self.html = pdf.start()
        self.html.return_value.write_pdf.return_value = self.PDF
        self.addCleanup(pdf.stop)
        logo = patch.object(views, "_obtener_logo_url", return_value="")
        logo.start()
        self.addCleanup(logo.stop)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target)
            mock = transport.start()
            self.addCleanup(transport.stop)
            self.addCleanup(mock.assert_not_called)

    def role_for(self, granular=True, **permissions):
        role = RolSistema.objects.create(nombre="Compras restringidas QA", codigo=f"mobile-purchases-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=self.empresa, defaults={"rol_sistema": role})
        return role

    def url(self, route="ver_compra", empresa=None, purchase=None, mobile=True, **parameters):
        empresa = empresa or self.empresa
        purchase = purchase or self.purchases.get(empresa.slug, self.purchase)
        arguments = [empresa.slug] if route == self.ROUTES[0] else [empresa.slug, purchase.pk]
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        return reverse(route, args=arguments)+("?"+urlencode(query) if query else "")

    def direct(self, route="ver_compra", method="get", url=None, empresa=None, purchase=None, data=None):
        target = url or self.url(route, empresa, purchase)
        request = RequestFactory().post(target, data or {}) if method == "post" else RequestFactory().generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(target).path)
        return match.func(request, **match.kwargs)

    def denied(self, callback):
        try:
            response = callback()
        except (PermissionDenied, Http404):
            return
        self.assertIn(response.status_code, (403, 404, 405))

    def snapshot(self):
        records = tuple(list(model.objects.order_by("pk").values()) for model in (CompraInventario, LineaCompraInventario, PagoCompra, ComprobanteEgresoCompra, MovimientoInventario, MovimientoLoteBodega, InventarioProducto, Proveedor, ClienteContable, CuentaAcumuladoCompra, TipoImpuesto, BodegaInventario, CuentaContable, CuentaFinanciera, AsientoContable, LineaAsientoContable, CAI, Producto, Cliente, Factura, NotaCredito, EntradaInventarioDocumento, PagoFactura, ReciboPago))
        counts = tuple(model.objects.count() for model in (ConfiguracionAvanzadaEmpresa, ConfiguracionFacturacionEmpresa, ConfiguracionContableEmpresa, ConfiguracionClinica, ConfiguracionCRM, Paciente, ProfesionalSalud, RecetaMedica, CitaCliente, OpcionServicioAgenda, RegistroAuditoria))
        return records, counts

    def new_purchase(self, amount=Decimal("20"), state="aplicada", reference="Compra paginada QA", document_date=None):
        purchase = CompraInventario.objects.create(empresa=self.empresa, proveedor=self.suppliers[self.empresa.slug], proveedor_nombre="Proveedor paginado histórico", referencia_documento=reference, fecha_documento=document_date or date(2026, 10, 3), condicion_pago="credito", dias_credito=5, estado=state)
        LineaCompraInventario.objects.create(compra=purchase, producto=self.products[self.empresa.slug], cantidad=Decimal("1"), costo_unitario=amount)
        return purchase

    def assert_corrupt_relation(self, model, record, field, bad_value):
        original = getattr(record, field)
        with connection.constraint_checks_disabled():
            try:
                model.objects.filter(pk=record.pk).update(**{field: bad_value})
                before = self.snapshot()
                self.html.reset_mock()
                for route in self.ROUTES:
                    target = self.url(route, q="No coincide", page="100") if route == self.ROUTES[0] else self.url(route)
                    self.denied(lambda: self.direct(route, url=target))
                self.html.assert_not_called()
                self.assertEqual(self.snapshot(), before)
            finally:
                model.objects.filter(pk=record.pk).update(**{field: original})

    def test_four_companies_mobile_and_original_pc_templates_are_separate(self):
        before = self.snapshot()
        for company in self.empresas.values():
            for route, app_template, web_template in ((self.ROUTES[0], "facturacion/mobile_purchase_list.html", "facturacion/compras_cxp_premium.html"), (self.ROUTES[1], "facturacion/mobile_purchase_detail.html", "facturacion/ver_compra_cxp_premium.html")):
                app = self.client.get(self.url(route, company))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, app_template)
                self.assertNotContains(app, "<table")
                self.assertIn("no-store", app["Cache-Control"])
                web = self.client.get(self.url(route, company, mobile=False))
                self.assertTemplateUsed(web, web_template)
                self.assertTemplateNotUsed(web, app_template)
        self.assertEqual(self.snapshot(), before)

    def test_pdf_retains_original_renderer_html_template_bytes_filename_and_inline_header(self):
        before = self.snapshot()
        for company in self.empresas.values():
            self.html.reset_mock()
            with patch.object(views, "render_to_string", wraps=views.render_to_string) as template:
                app = self.client.get(self.url(self.ROUTES[2], company))
                app_html = self.html.call_args.kwargs
                template.assert_called_once()
                self.assertEqual(template.call_args.args[0], "facturacion/compra_pdf.html")
                self.assertEqual(template.call_args.args[1]["compra"].pk, self.purchases[company.slug].pk)
                self.assertEqual(self.html.return_value.write_pdf.call_count, 1)
            web = self.client.get(self.url(self.ROUTES[2], company, mobile=False))
            self.assertEqual((app.content, app["Content-Type"], app["Content-Disposition"]), (self.PDF, "application/pdf", web["Content-Disposition"]))
            self.assertEqual(app_html, self.html.call_args.kwargs)
            self.assertIn("inline", app["Content-Disposition"])
            self.assertIn("no-store", app["Cache-Control"])
        self.assertEqual(self.snapshot(), before)

    def test_get_head_no_configuration_accounts_catalogue_stock_financial_or_audit_writes(self):
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalogue, patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa") as config:
            for route in self.ROUTES:
                for method in ("get", "head"):
                    result = getattr(self.client, method)(self.url(route))
                    self.assertEqual(result.status_code, 200)
                    if method == "head":
                        self.assertEqual(result.content, b"")
                    self.assertIn("no-store", result["Cache-Control"])
            accounts.assert_not_called()
            catalogue.assert_not_called()
            config.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_capability_helper_does_not_query_purchases_payments_suppliers_or_inventory(self):
        from .mobile_purchases import compras_disponibles_app
        request = RequestFactory().get("/")
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(compras_disponibles_app(request, self.empresa))
        for model in (CompraInventario, PagoCompra, Proveedor, Producto, MovimientoInventario):
            self.assertFalse(any(f'"{model._meta.db_table}"' in query["sql"] for query in captured))

    def test_exact_purchase_permission_both_role_types_requires_no_supplier_egreso_payment_or_agenda_grant(self):
        for granular in (True, False):
            self.role_for(granular=granular, puede_compras=True)
            before = self.snapshot()
            for route in self.ROUTES:
                self.assertEqual(self.client.get(self.url(route)).status_code, 200)
            self.assertEqual(self.snapshot(), before)

    def test_unrelated_permissions_empty_role_and_anonymous_cannot_bypass_direct_guards(self):
        for permissions in ({}, {"puede_inventario": True}, {"puede_proveedores": True}, {"puede_egresos": True}, {"puede_registrar_pagos_proveedores": True}, {"puede_productos": True}):
            self.role_for(**permissions)
            for route in self.ROUTES:
                self.denied(lambda: self.direct(route))
        self.role_for(puede_compras=True)
        request = RequestFactory().get(self.url())
        request.user = AnonymousUser()
        self.assertEqual(views.ver_compra(request, self.empresa.slug, self.purchase.pk).status_code, 302)

    def test_company_membership_activity_license_and_billing_module_are_guarded_without_middleware(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=False)
        for route in self.ROUTES:
            self.denied(lambda: self.direct(route))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=True)
        for values in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values)
            self.denied(lambda: self.direct())
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.denied(lambda: self.direct(empresa=self.empresas["medical_spa"]))

    def test_other_company_app_flag_keeps_original_purchase_web_views(self):
        company = Empresa.objects.create(nombre="Otra clínica compras", slug="otra_clinica_compras_qa", rtn="MCOM-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=company, modulo=self.module)
        self.usuario.empresas_acceso.add(company)
        purchase = CompraInventario.objects.create(empresa=company, proveedor_nombre="Proveedor otra clínica", estado="borrador")
        for route, template in ((self.ROUTES[0], "facturacion/compras_cxp_premium.html"), (self.ROUTES[1], "facturacion/ver_compra_cxp_premium.html")):
            response = self.client.get(self.url(route, company, purchase))
            self.assertTemplateUsed(response, template)

    def test_original_seven_summaries_cover_every_filtered_purchase_before_twenty_row_pagination(self):
        for index in range(26):
            self.new_purchase(amount=Decimal(index+2), state=("aplicada", "borrador", "anulada")[index % 3])
        before = self.snapshot()
        app = self.client.get(self.url(self.ROUTES[0]))
        web = self.client.get(self.url(self.ROUTES[0], mobile=False))
        self.assertEqual(app.context["resumen"], web.context["resumen"])
        self.assertEqual(set(app.context["resumen"]), {"total", "aplicadas", "borradores", "unidades", "monto", "saldo_total", "por_pagar"})
        self.assertEqual(app.context["resumen"]["total"], 27)
        self.assertEqual(len(app.context["compras"]), 20)
        self.assertEqual(app.context["compras"].paginator.count, 27)
        self.assertEqual(self.snapshot(), before)

    def test_original_three_state_filters_match_web_on_whole_results(self):
        for state in ("borrador", "aplicada", "anulada"):
            self.new_purchase(state=state)
        for state in ("", "borrador", "aplicada", "anulada"):
            app = self.client.get(self.url(self.ROUTES[0], estado=state))
            web = self.client.get(self.url(self.ROUTES[0], mobile=False, estado=state))
            self.assertEqual(app.context["resumen"], web.context["resumen"])
            self.assertEqual([row.pk for row in app.context["compras"]], [row.pk for row in web.context["compras"]])

    def test_payment_filters_use_original_properties_including_empty_total_as_paid(self):
        pending = self.new_purchase()
        paid = self.new_purchase()
        PagoCompra.objects.create(compra=paid, monto=Decimal("20"))
        zero = CompraInventario.objects.create(empresa=self.empresa, proveedor_nombre="Documento sin líneas", estado="borrador")
        expected = {"pendiente": {pending.pk}, "parcial": {self.purchase.pk}, "pagado": {paid.pk, zero.pk}}
        for state, identifiers in expected.items():
            app = self.client.get(self.url(self.ROUTES[0], pago=state))
            web = self.client.get(self.url(self.ROUTES[0], mobile=False, pago=state))
            self.assertEqual({row.pk for row in app.context["compras"]}, identifiers)
            self.assertEqual(app.context["resumen"], web.context["resumen"])

    def test_search_uses_three_original_document_fields_and_preserves_historical_supplier_snapshot(self):
        for query in (self.purchase.numero_compra.lower(), self.purchase.proveedor_nombre.lower(), self.purchase.referencia_documento.lower()):
            app = self.client.get(self.url(self.ROUTES[0], q=" "+query+" "))
            self.assertEqual([row.pk for row in app.context["compras"]], [self.purchase.pk])
        app = self.client.get(self.url(self.ROUTES[0], q=self.suppliers[self.empresa.slug].nombre))
        self.assertEqual(len(app.context["compras"]), 0)

    def test_stable_date_id_twenty_row_pagination_preserves_all_original_filters(self):
        docs = [self.new_purchase(reference="Paginación homogénea") for _ in range(23)]
        first = self.client.get(self.url(self.ROUTES[0], q="Paginación homogénea", estado="aplicada", pago="pendiente"))
        self.assertEqual([row.pk for row in first.context["compras"]], [row.pk for row in reversed(docs[3:])])
        next_url = first.context["mobile_purchase_next_url"]
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["Paginación homogénea"], "estado": ["aplicada"], "pago": ["pendiente"], "page": ["2"]})
        second = self.client.get(next_url)
        self.assertEqual([row.pk for row in second.context["compras"]], [row.pk for row in reversed(docs[:3])])

    def test_detail_retains_original_purchase_lines_payments_movements_and_hnl(self):
        before = self.snapshot()
        app = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(app.context["compra"].pk, web.context["compra"].pk)
        self.assertEqual([row.pk for row in app.context["pagos"]], [row.pk for row in web.context["pagos"]])
        self.assertEqual([row.pk for row in app.context["movimientos"]], [row.pk for row in web.context["movimientos"]])
        self.assertEqual((app.context["compra"].total_documento, app.context["compra"].saldo_pendiente), (Decimal("207"), Decimal("157")))
        self.assertContains(app, "L.")
        self.assertNotContains(app, "$ ")
        self.assertFalse(any(field.name == "moneda" for field in CompraInventario._meta.fields))
        self.assertEqual(self.snapshot(), before)

    def test_original_total_line_fallback_is_used_without_recalculating_or_persisting_old_rows(self):
        LineaCompraInventario.objects.filter(pk=self.lines[self.empresa.slug].pk).update(subtotal=0, impuesto_monto=0, cantidad=Decimal("2"), costo_unitario=Decimal("45.1234"))
        before = self.snapshot()
        app = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(app.context["compra"].total_documento, Decimal("90.25"))
        self.assertEqual(app.context["compra"].saldo_pendiente, web.context["compra"].saldo_pendiente)
        self.assertEqual(self.snapshot(), before)

    def test_historical_tax_discount_amounts_and_payment_metadata_are_not_rewritten_on_read(self):
        TipoImpuesto.objects.filter(pk=self.tax.pk).update(porcentaje=Decimal("18"), activo=False)
        ComprobanteEgresoCompra.objects.filter(pago=self.payments[self.empresa.slug]).update(proveedor_nombre="Nombre documental antiguo", monto=Decimal("9"), fecha=date(2026, 9, 1), referencia="Referencia documental diferente")
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url()).context["compra"].total_documento, Decimal("207"))
        self.assertEqual(self.client.get(self.url(self.ROUTES[2])).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_mobile_detail_exposes_no_write_actions_or_desktop_fallback_even_for_fully_authorized_user(self):
        self.role_for(puede_compras=True, puede_crear_compras=True, puede_editar_compras=True, puede_aplicar_compras=True, puede_anular_compras=True, puede_registrar_pagos_proveedores=True)
        app = self.client.get(self.url())
        for flag in ("can_create_purchase", "can_edit_purchase", "can_apply_purchase", "can_void_purchase", "can_delete_purchase", "can_pay_purchase", "can_revert_purchase_payment"):
            self.assertFalse(app.context[flag])
        for route in ("editar_compra", "aplicar_compra", "anular_compra", "eliminar_compra_anulada", "registrar_pago_compra"):
            self.assertNotContains(app, reverse(route, args=[self.empresa.slug, self.purchase.pk]))
        self.assertNotContains(app, reverse("crear_compra", args=[self.empresa.slug]))
        self.assertNotContains(app, reverse("revertir_pago_compra", args=[self.empresa.slug, self.purchase.pk, self.payments[self.empresa.slug].pk]))

    def test_nullable_and_inactive_historical_relationships_remain_readable_without_reactivation(self):
        actor = Usuario.objects.create_user(username="purchase-multicompany-actor", empresa=self.empresas["medical_spa"])
        Proveedor.objects.filter(pk=self.suppliers[self.empresa.slug].pk).update(activo=False, cuenta_habitual_por=actor)
        CuentaFinanciera.objects.filter(pk=self.accounts[self.empresa.slug].pk).update(activa=False)
        CuentaContable.objects.filter(pk=self.accounts[self.empresa.slug].cuenta_contable_id).update(activa=False)
        BodegaInventario.objects.filter(pk=self.warehouses[self.empresa.slug].pk).update(activa=False)
        MovimientoInventario.objects.filter(pk=self.movements[self.empresa.slug].pk).update(usuario=actor)
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        CompraInventario.objects.filter(pk=self.purchase.pk).update(proveedor=None, cuenta_financiera_pago=None)
        ComprobanteEgresoCompra.objects.filter(pago=self.payments[self.empresa.slug]).update(proveedor=None)
        PagoCompra.objects.filter(pk=self.payments[self.empresa.slug].pk).update(cuenta_financiera=None)
        MovimientoInventario.objects.filter(pk=self.movements[self.empresa.slug].pk).update(bodega=None, usuario=None)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_supplier_and_egreso_links_require_their_actual_read_permission_and_stay_mobile(self):
        document = self.payments[self.empresa.slug].comprobante
        initial = self.client.get(self.url())
        self.assertFalse(initial.context["can_view_supplier"])
        self.assertFalse(initial.context["can_view_egresos"])
        self.assertEqual(initial.context["mobile_purchase_payments"][0]["comprobante_url"], "")
        self.assertEqual(initial.context["mobile_purchase_payments"][0]["comprobante_numero"], "")
        self.role_for(puede_compras=True, puede_proveedores=True, puede_egresos=True)
        before = self.snapshot()
        authorized = self.client.get(self.url())
        self.assertTrue(authorized.context["can_view_supplier"])
        self.assertTrue(authorized.context["can_view_egresos"])
        supplier_url = reverse("ver_proveedor_facturacion", args=[self.empresa.slug, self.suppliers[self.empresa.slug].pk])+"?app=1"
        egreso_url = reverse("ver_comprobante_egreso", args=[self.empresa.slug, document.pk])+"?app=1"
        self.assertEqual(authorized.context["app_purchase_supplier_url"], supplier_url)
        self.assertEqual(authorized.context["mobile_purchase_payments"][0]["comprobante_url"], egreso_url)
        self.assertContains(authorized, supplier_url)
        self.assertContains(authorized, egreso_url)
        self.assertEqual(self.snapshot(), before)

    def test_foreign_or_orphan_supplier_is_denied_before_filters_detail_or_pdf(self):
        for value in (self.suppliers["medical_spa"].pk, 2147483647):
            self.assert_corrupt_relation(CompraInventario, self.purchase, "proveedor_id", value)

    def test_supplier_optional_client_contable_company_and_existence_are_checked(self):
        external = Empresa.objects.create(nombre="Administradora compras QA", slug="dubon_asociados", rtn="MCOM-DUBON")
        client = ClienteContable.objects.create(empresa=external, nombre="Cliente externo compras QA")
        for value in (client.pk, 2147483647):
            self.assert_corrupt_relation(Proveedor, self.suppliers[self.empresa.slug], "cliente_contable_id", value)
        own = ClienteContable(empresa=self.empresa, nombre="Cliente contable histórico importado")
        ClienteContable.objects.bulk_create([own])
        Proveedor.objects.filter(pk=self.suppliers[self.empresa.slug].pk).update(cliente_contable=own, cuenta_contable=None)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_supplier_hidden_contable_habitual_and_actor_foreign_or_orphan_relations_are_denied(self):
        external = Empresa.objects.create(nombre="Administradora habitual compras QA", slug="dubon_asociados", rtn="MCOM-HAB")
        client = ClienteContable.objects.create(empresa=external, nombre="Cliente habitual extranjero")
        habitual = CuentaAcumuladoCompra.objects.create(cliente_contable=client, nombre="Cuenta habitual extranjera", grupo="gasto")
        supplier = self.suppliers[self.empresa.slug]
        for field, value in (("cuenta_contable_id", self.accounts["medical_spa"].cuenta_contable_id), ("cuenta_contable_id", 2147483647), ("cuenta_habitual_id", habitual.pk), ("cuenta_habitual_id", 2147483647), ("cuenta_habitual_por_id", 2147483647)):
            self.assert_corrupt_relation(Proveedor, supplier, field, value)

    def test_line_products_must_exist_in_company_and_tax_must_exist_in_global_catalogue(self):
        line = self.lines[self.empresa.slug]
        for field, value in (("producto_id", self.products["medical_spa"].pk), ("producto_id", 2147483647), ("impuesto_id", 2147483647)):
            self.assert_corrupt_relation(LineaCompraInventario, line, field, value)
        self.assertFalse(any(field.name == "empresa" for field in TipoImpuesto._meta.fields))
        tax = TipoImpuesto.objects.create(nombre="Otro impuesto GLOBAL compras", porcentaje=Decimal("18"), activo=False)
        LineaCompraInventario.objects.filter(pk=line.pk).update(impuesto=tax)
        self.assertEqual(self.client.get(self.url(self.ROUTES[2])).status_code, 200)

    def test_purchase_and_payment_accounts_require_own_existing_contable_even_when_inactive(self):
        for model, record, field in ((CompraInventario, self.purchase, "cuenta_financiera_pago_id"), (PagoCompra, self.payments[self.empresa.slug], "cuenta_financiera_id")):
            for value in (self.accounts["medical_spa"].pk, 2147483647):
                self.assert_corrupt_relation(model, record, field, value)
        account = self.accounts[self.empresa.slug]
        for value in (self.accounts["medical_spa"].cuenta_contable_id, 2147483647):
            self.assert_corrupt_relation(CuentaFinanciera, account, "cuenta_contable_id", value)

    def test_all_egresos_linked_to_purchase_or_its_payment_are_checked_even_with_wrong_company(self):
        document = self.payments[self.empresa.slug].comprobante
        self.assert_corrupt_relation(ComprobanteEgresoCompra, document, "empresa_id", self.empresas["medical_spa"].pk)
        self.assert_corrupt_relation(ComprobanteEgresoCompra, document, "compra_id", self.purchases["medical_spa"].pk)
        other_own_purchase = self.new_purchase(reference="Otra compra propia del egreso")
        self.assert_corrupt_relation(ComprobanteEgresoCompra, document, "compra_id", other_own_purchase.pk)

    def test_egreso_wrong_or_orphan_payment_and_provider_are_denied_without_snapshot_equality_rules(self):
        document = self.payments[self.empresa.slug].comprobante
        unpaid = PagoCompra(compra=self.purchases["medical_spa"], monto=Decimal("1"), fecha=date(2026, 10, 1))
        PagoCompra.objects.bulk_create([unpaid])
        for field, value in (("pago_id", unpaid.pk), ("pago_id", 2147483647), ("proveedor_id", self.suppliers["medical_spa"].pk), ("proveedor_id", 2147483647)):
            self.assert_corrupt_relation(ComprobanteEgresoCompra, document, field, value)

    def test_movement_linked_to_purchase_cannot_have_foreign_or_orphan_company(self):
        movement = self.movements[self.empresa.slug]
        for value in (self.empresas["medical_spa"].pk, 2147483647):
            self.assert_corrupt_relation(MovimientoInventario, movement, "empresa_id", value)

    def test_movement_product_warehouse_and_nullable_actor_relations_are_checked_before_display(self):
        movement = self.movements[self.empresa.slug]
        for field, value in (("producto_id", self.products["medical_spa"].pk), ("producto_id", 2147483647), ("bodega_id", self.warehouses["medical_spa"].pk), ("bodega_id", 2147483647), ("usuario_id", 2147483647)):
            self.assert_corrupt_relation(MovimientoInventario, movement, field, value)

    def test_optional_movement_invoice_credit_note_and_entry_documents_must_exist_in_company(self):
        documents = {}
        for index, slug in enumerate((self.empresa.slug, "medical_spa"), 1):
            company = self.empresas[slug]
            client = Cliente.objects.create(empresa=company, nombre=f"Cliente histórico movimiento {slug}", rtn=f"08011999002{index:03d}")
            invoice = Factura(empresa=company, cliente=client, estado="anulada", subtotal=Decimal("1"), total=Decimal("1"), total_lempiras=Decimal("1"))
            Factura.objects.bulk_create([invoice])
            note = NotaCredito(empresa=company, factura_origen=invoice, cliente=client, estado="anulada")
            NotaCredito.objects.bulk_create([note])
            entry = EntradaInventarioDocumento.objects.create(empresa=company, referencia=f"Entrada histórica {slug}", estado="aplicada")
            documents[slug] = {"factura_id": invoice.pk, "nota_credito_id": note.pk, "entrada_documento_id": entry.pk}
        movement = self.movements[self.empresa.slug]
        for field, foreign_id in documents["medical_spa"].items():
            self.assert_corrupt_relation(MovimientoInventario, movement, field, foreign_id)
            self.assert_corrupt_relation(MovimientoInventario, movement, field, 2147483647)
        MovimientoInventario.objects.filter(pk=movement.pk).update(**documents[self.empresa.slug])
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_integrity_is_validated_before_search_state_or_page_can_hide_corrupt_own_purchase(self):
        bad = self.new_purchase(reference="No se presenta al buscar", state="borrador")
        CompraInventario.objects.filter(pk=bad.pk).update(proveedor=self.suppliers["medical_spa"])
        before = self.snapshot()
        for filters in ({"q": "PURREF"}, {"estado": "aplicada"}, {"pago": "parcial"}, {"page": "100"}):
            self.denied(lambda: self.direct(self.ROUTES[0], url=self.url(self.ROUTES[0], **filters)))
        self.assertEqual(self.snapshot(), before)

    def test_foreign_unknown_and_noncanonical_purchase_ids_are_denied_before_pdf_renderer(self):
        for route in self.ROUTES[1:]:
            self.denied(lambda: self.direct(route, purchase=self.purchases["medical_spa"]))
            for value in ("0", "00"+str(self.purchase.pk), "9223372036854775808", "2147483647"):
                target = self.url(route).replace(f"/{self.purchase.pk}/", f"/{value}/")
                self.denied(lambda: self.direct(route, url=target))
        self.html.assert_not_called()

    def test_closed_unique_mobile_queries_reject_unknown_params_filters_and_duplicate_app(self):
        before = self.snapshot()
        for route in self.ROUTES:
            for query in ("app=1&app=1", "app=1&empresa=1", "app=1&next=/", "app=1&accion=aplicar", "app=1&compra_id=1"):
                self.denied(lambda: self.direct(route, url=self.url(route).split("?")[0]+"?"+query))
        for query in ("app=1&q=a&q=b", "app=1&estado=invalid", "app=1&pago=invalid", "app=1&page=1&page=1"):
            self.denied(lambda: self.direct(self.ROUTES[0], url=self.url(self.ROUTES[0]).split("?")[0]+"?"+query))
        for value in ("", "0", "01", "-1", "1e2", "١", "9223372036854775808"):
            self.denied(lambda: self.direct(self.ROUTES[0], url=self.url(self.ROUTES[0], page=value)))
        for route in self.ROUTES[1:]:
            self.denied(lambda: self.direct(route, url=self.url(route, q="forbidden")))
        self.assertEqual(self.snapshot(), before)

    def test_mobile_read_routes_reject_all_write_methods_metadata_and_files_without_mutations(self):
        before = self.snapshot()
        for route in self.ROUTES:
            for method in ("post", "put", "patch", "delete"):
                self.denied(lambda: self.direct(route, method=method, data={"app": "1", "accion": "aplicar", "archivo": SimpleUploadedFile("invalid.txt", b"invalid")}))
        self.assertEqual(self.snapshot(), before)

    def test_unflagged_web_read_and_post_render_behavior_is_unchanged_and_has_no_mobile_template(self):
        before = self.snapshot()
        for route in self.ROUTES[:2]:
            response = self.client.post(self.url(route, mobile=False), {})
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/compras_cxp_premium.html" if route == self.ROUTES[0] else "facturacion/ver_compra_cxp_premium.html")
        self.assertEqual(self.snapshot(), before)

    def test_csrf_logout_and_read_only_method_guard_do_not_grant_write_capability(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(client.get(self.url()).status_code, 200)
        for route in self.ROUTES:
            self.assertIn(client.post(self.url(route), {"app": "1"}).status_code, (302, 403, 405))
        self.assertEqual(self.snapshot(), before)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_module_card_and_navigation_are_authorized_mobile_read_only_without_other_grants(self):
        for granular in (True, False):
            self.role_for(granular=granular, puede_compras=True)
            before = self.snapshot()
            response = self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug]))
            self.assertEqual(response.status_code, 200)
            cards = {item["key"]: item for item in response.json()["modulos"]}
            self.assertEqual(cards["compras"]["url"], self.url(self.ROUTES[0]))
            self.assertEqual(cards["compras"]["estado"], "parcial")
            for forbidden in ("egresos", "proveedores", "cobros", "recibos", "pacientes", "agenda"):
                self.assertNotIn(forbidden, cards)
            page = self.client.get(self.url())
            self.assertFalse(page.context["app_home_disponible"])
            self.assertNotContains(page, 'rel="manifest"')
            self.assertEqual(self.snapshot(), before)

    def test_html_escapes_document_snapshots_and_does_not_leak_other_company_search_results(self):
        CompraInventario.objects.filter(pk=self.purchase.pk).update(proveedor_nombre="<script>purchase_synthetic()</script>")
        detail = self.client.get(self.url())
        self.assertContains(detail, "&lt;script&gt;purchase_synthetic()&lt;/script&gt;")
        self.assertNotContains(detail, "<script>purchase_synthetic()</script>")
        page = self.client.get(self.url(self.ROUTES[0], q=self.purchases["medical_spa"].numero_compra))
        self.assertEqual(len(page.context["compras"]), 0)

    def test_empty_own_purchase_list_does_not_seed_or_read_other_company_documents(self):
        movement = self.movements[self.empresa.slug]
        MovimientoInventario.objects.filter(pk=movement.pk).delete()
        ComprobanteEgresoCompra.objects.filter(compra=self.purchase).delete()
        PagoCompra.objects.filter(compra=self.purchase).delete()
        self.purchase.delete()
        before = self.snapshot()
        result = self.client.get(self.url(self.ROUTES[0]))
        self.assertEqual(result.context["resumen"]["total"], 0)
        self.assertEqual(len(result.context["compras"]), 0)
        self.assertNotContains(result, self.purchases["medical_spa"].numero_compra)
        self.assertEqual(self.snapshot(), before)
