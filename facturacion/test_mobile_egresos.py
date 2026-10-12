"""Read-only mobile disbursements preserve purchase-payment documents and PDF."""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
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
from .models import CAI, Cliente, ClienteContable, CompraInventario, ComprobanteEgresoCompra, ConfiguracionFacturacionEmpresa, Factura, LineaCompraInventario, MovimientoInventario, MovimientoLoteBodega, PagoCompra, PagoFactura, Producto, Proveedor, ReciboPago


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileEgresosIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ROUTES = ("egresos_dashboard", "ver_comprobante_egreso", "descargar_comprobante_egreso_pdf")
    PDF = b"%PDF-1.4\nSynthetic original disbursement renderer bytes\n%%EOF"

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.cuentas, cls.proveedores, cls.productos, cls.compras, cls.pagos, cls.comprobantes = {}, {}, {}, {}, {}, {}, {}
        cls.rol = RolSistema.objects.create(nombre="Sólo egresos", codigo="mobile-egresos-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_egresos=True)
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Egresos QA {slug}", slug=slug, rtn=f"MEGR-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            contable = CuentaContable.objects.create(empresa=empresa, codigo="1101-QA", nombre=f"Contable egresos {slug}", tipo="activo")
            cls.cuentas[slug] = CuentaFinanciera.objects.create(empresa=empresa, nombre=f"Banco egresos {slug}", cuenta_contable=contable)
            cls.proveedores[slug] = Proveedor.objects.create(empresa=empresa, nombre=f"Proveedor egresos {slug}")
            cls.productos[slug] = Producto.objects.create(empresa=empresa, nombre=f"Producto egresos {slug}", codigo=f"MEGR-{index}", precio=Decimal("1000"))
            compra = CompraInventario.objects.create(empresa=empresa, proveedor=cls.proveedores[slug], proveedor_nombre=f"Proveedor histórico {slug}", estado="aplicada", fecha_documento=date(2026, 10, 1), cuenta_financiera_pago=cls.cuentas[slug])
            cls.compras[slug] = compra
            LineaCompraInventario.objects.create(compra=compra, producto=cls.productos[slug], cantidad=Decimal("1"), costo_unitario=Decimal("1000"))
            for index, metodo in enumerate(("efectivo", "tarjeta", "transferencia", "cheque")):
                pago = PagoCompra.objects.create(compra=compra, fecha=date(2026, 10, 2+index), monto=Decimal("10")+index, metodo=metodo, referencia=f"EGREF-{slug}-{index}", observacion=f"Observación histórica {index}\nSegunda línea", cuenta_financiera=cls.cuentas[slug])
                cls.pagos[(slug, index)], cls.comprobantes[(slug, index)] = pago, pago.comprobante
        cls.empresa = cls.empresas["hospital_mia"]
        cls.comprobante = cls.comprobantes[(cls.empresa.slug, 1)]
        cls.usuario = Usuario.objects.create_user(username="mobile-egresos", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
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

    def asignar_rol(self, empresa=None, granular=True, **permissions):
        rol = RolSistema.objects.create(nombre="Egresos restringidos", codigo=f"mobile-egresos-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, view="ver_comprobante_egreso", empresa=None, comprobante=None, mobile=True, **parameters):
        empresa = empresa or self.empresa
        comprobante = comprobante or self.comprobantes.get((empresa.slug, 1), self.comprobante)
        args = [empresa.slug] if view == "egresos_dashboard" else [empresa.slug, comprobante.pk]
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        return reverse(view, args=args)+("?"+urlencode(query) if query else "")

    def directo(self, view="ver_comprobante_egreso", empresa=None, comprobante=None, method="get", url=None):
        url = url or self.url(view, empresa, comprobante)
        request = RequestFactory().generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def assert_rejected(self, callback):
        try:
            response = callback()
        except (PermissionDenied, Http404):
            return
        self.assertIn(response.status_code, (403, 404, 405))

    def snapshot(self):
        financial = tuple(list(model.objects.order_by("pk").values()) for model in (ComprobanteEgresoCompra, CompraInventario, PagoCompra, Proveedor, CuentaContable, CuentaFinanciera, AsientoContable, LineaAsientoContable, CAI, Producto, LineaCompraInventario, MovimientoInventario, MovimientoLoteBodega, Factura, PagoFactura, ReciboPago, Cliente))
        counts = tuple(model.objects.count() for model in (ConfiguracionAvanzadaEmpresa, ConfiguracionFacturacionEmpresa, ConfiguracionContableEmpresa, ConfiguracionClinica, ConfiguracionCRM, Paciente, ProfesionalSalud, RecetaMedica, CitaCliente, OpcionServicioAgenda, RegistroAuditoria))
        return financial, counts

    def new_document(self, empresa=None, reference="Egreso paginado QA", metodo="efectivo"):
        empresa = empresa or self.empresa
        pago = PagoCompra.objects.create(compra=self.compras[empresa.slug], fecha=date(2026, 10, 10), monto=Decimal("1"), metodo=metodo, referencia=reference, cuenta_financiera=self.cuentas[empresa.slug])
        return pago.comprobante

    def test_four_clinics_mobile_list_detail_and_original_web_templates_are_separate(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            for route, mobile_template, web_template in (("egresos_dashboard", "facturacion/mobile_egreso_list.html", "facturacion/egresos_premium.html"), ("ver_comprobante_egreso", "facturacion/mobile_egreso_detail.html", "facturacion/ver_comprobante_egreso_premium.html")):
                app = self.client.get(self.url(route, empresa))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, mobile_template)
                self.assertNotContains(app, "<table")
                self.assertIn("no-store", app["Cache-Control"])
                web = self.client.get(self.url(route, empresa, mobile=False))
                self.assertEqual(web.status_code, 200)
                self.assertTemplateUsed(web, web_template)
                self.assertTemplateNotUsed(web, mobile_template)
        self.assertEqual(self.snapshot(), before)

    def test_pdf_uses_original_renderer_template_html_bytes_filename_and_attachment(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            self.html.reset_mock()
            with patch.object(views, "render_to_string", wraps=views.render_to_string) as template:
                app = self.client.get(self.url("descargar_comprobante_egreso_pdf", empresa))
                app_html = self.html.call_args.kwargs
                template.assert_called_once()
                self.assertEqual(template.call_args.args[0], "facturacion/comprobante_egreso_pdf.html")
                self.assertEqual(template.call_args.args[1]["comprobante"].pk, self.comprobantes[(empresa.slug, 1)].pk)
                self.assertEqual(self.html.return_value.write_pdf.call_count, 1)
            web = self.client.get(self.url("descargar_comprobante_egreso_pdf", empresa, mobile=False))
            self.assertEqual((app.status_code, app.content, app["Content-Type"], app["Content-Disposition"]), (web.status_code, self.PDF, "application/pdf", web["Content-Disposition"]))
            self.assertEqual(app_html, self.html.call_args.kwargs)
            self.assertIn("attachment", app["Content-Disposition"])
            self.assertIn("no-store", app["Cache-Control"])
        self.assertEqual(self.snapshot(), before)

    def test_get_head_never_create_configuration_catalog_accounts_or_financial_clinical_data(self):
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalogue, patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa") as config:
            for route in self.ROUTES:
                for method in ("get", "head"):
                    response = getattr(self.client, method)(self.url(route))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    if method == "head":
                        self.assertEqual(response.content, b"")
            accounts.assert_not_called()
            catalogue.assert_not_called()
            config.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_granular_egresos_permission_alone_does_not_grant_purchase_provider_payment_or_receipts(self):
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        for route, identifier in (("ver_compra", self.compras[self.empresa.slug].pk), ("ver_proveedor_facturacion", self.proveedores[self.empresa.slug].pk)):
            self.assertIn(self.client.get(reverse(route, args=[self.empresa.slug, identifier])).status_code, (302, 403))
        self.assertIn(self.client.get(reverse("recibos_dashboard", args=[self.empresa.slug])+"?app=1").status_code, (302, 403))
        page = self.client.get(self.url())
        self.assertFalse(page.context["app_home_disponible"])
        self.assertNotContains(page, 'rel="manifest"')

    def test_legacy_egresos_permission_remains_independent_of_purchases_and_agenda(self):
        self.asignar_rol(granular=False, puede_egresos=True)
        before = self.snapshot()
        for route in self.ROUTES:
            response = self.client.get(self.url(route))
            self.assertEqual(response.status_code, 200)
            if route != "descargar_comprobante_egreso_pdf":
                self.assertFalse(response.context["app_home_disponible"])
                self.assertNotContains(response, 'rel="manifest"')
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_permissions_and_missing_role_block_direct_backend_before_document_queries(self):
        for permissions in ({"puede_compras": True}, {"puede_proveedores": True}, {"puede_recibos": True}, {"puede_registrar_pagos_clientes": True}, {"puede_inventario": True}, {}):
            self.asignar_rol(**permissions)
            before = self.snapshot()
            for route in self.ROUTES:
                with CaptureQueriesContext(connection) as queries:
                    self.assert_rejected(lambda: self.directo(route))
                self.assertFalse(any(f'"{model._meta.db_table}"' in item["sql"] for item in queries for model in (ComprobanteEgresoCompra, CompraInventario, PagoCompra)))
            self.assertEqual(self.snapshot(), before)
        self.html.assert_not_called()

    def test_direct_billing_module_company_membership_activity_and_license_are_required(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for route in self.ROUTES:
            self.assert_rejected(lambda: self.directo(route))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for change in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**change)
            self.assert_rejected(lambda: self.directo())
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        foreign = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(foreign)
        self.assert_rejected(lambda: self.directo(empresa=foreign))

    def test_pure_capability_is_readonly_and_rejects_anonymous_or_nonclinical_company(self):
        from .mobile_egresos import egresos_disponibles_app
        request = RequestFactory().get("/")
        request.user = self.usuario
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(egresos_disponibles_app(request, self.empresa))
        self.assertFalse(any(f'"{model._meta.db_table}"' in item["sql"] for item in queries for model in (ComprobanteEgresoCompra, CompraInventario, PagoCompra, CuentaFinanciera)))
        request.user = AnonymousUser()
        self.assertFalse(egresos_disponibles_app(request, self.empresa))
        request.user = self.usuario
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="general")
        self.assertFalse(egresos_disponibles_app(request, Empresa.objects.get(pk=self.empresa.pk)))
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica")
        self.assertEqual(self.snapshot(), before)

    def test_other_clinical_company_app_flag_keeps_original_web_not_mobile(self):
        empresa = Empresa.objects.create(nombre="Otra clínica egresos QA", slug="otra_clinica_egresos", rtn="MEGR-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        response = self.client.get(self.url("egresos_dashboard", empresa))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/egresos_premium.html")
        self.assertTemplateNotUsed(response, "facturacion/mobile_egreso_list.html")

    def test_mobile_routes_are_get_head_only_even_when_other_permissions_allow_purchases(self):
        self.asignar_rol(puede_egresos=True, puede_compras=True, puede_proveedores=True)
        before = self.snapshot()
        for route in self.ROUTES:
            for method in ("post", "put", "patch", "delete"):
                self.assert_rejected(lambda: self.directo(route, method=method))
                self.assertIn(getattr(self.client, method)(self.url(route)).status_code, (302, 403, 405))
        self.assertEqual(self.snapshot(), before)
        self.html.assert_not_called()

    def test_closed_singleton_list_and_detail_parameters_reject_duplicates_unknowns_and_forbidden_filters(self):
        before = self.snapshot()
        for route in self.ROUTES:
            prefix = self.url(route).split("?")[0]
            queries = ("app=1&app=1", "app=1&empresa=1", "app=1&accion=eliminar", "app=1&compra_id=1")
            if route == "egresos_dashboard":
                queries += ("app=1&q=one&q=two", "app=1&page=1&page=1", "app=1&metodo=efectivo&metodo=tarjeta", "app=1&metodo=cheque", "app=1&metodo=unknown")
            else:
                queries += ("app=1&q=one", "app=1&page=1", "app=1&metodo=efectivo")
            for query in queries:
                self.assert_rejected(lambda: self.directo(route, url=prefix+"?"+query))
        self.assertEqual(self.snapshot(), before)
        self.html.assert_not_called()

    def test_noncanonical_page_and_document_ids_are_denied_before_query_or_renderer(self):
        before = self.snapshot()
        for value in ("", "0", "01", "-1", "1e2", "١", "9223372036854775808"):
            self.assert_rejected(lambda: self.directo("egresos_dashboard", url=self.url("egresos_dashboard", page=value)))
        for route in self.ROUTES[1:]:
            original = self.url(route)
            for value in ("0", "00"+str(self.comprobante.pk), "9223372036854775808"):
                altered = original.replace(f"/egresos/{self.comprobante.pk}/", f"/egresos/{value}/")
                self.assert_rejected(lambda: self.directo(route, url=altered))
        self.assertEqual(self.snapshot(), before)
        self.html.assert_not_called()

    def test_foreign_document_id_is_not_visible_in_own_company_detail_or_pdf(self):
        foreign = self.comprobantes[("medical_spa", 1)]
        before = self.snapshot()
        for route in self.ROUTES[1:]:
            self.assert_rejected(lambda: self.directo(route, comprobante=foreign))
            self.assertEqual(self.client.get(self.url(route, comprobante=foreign)).status_code, 404)
        self.assertEqual(self.snapshot(), before)
        self.html.assert_not_called()

    def test_list_search_and_totals_are_isolated_from_other_company_even_with_shared_reference(self):
        ComprobanteEgresoCompra.objects.filter(pk=self.comprobantes[("medical_spa", 1)].pk).update(referencia=self.comprobante.referencia)
        response = self.client.get(self.url("egresos_dashboard", q=self.comprobante.referencia))
        self.assertEqual([row.pk for row in response.context["egresos"]], [self.comprobante.pk])
        self.assertNotContains(response, self.comprobantes[("medical_spa", 1)].numero_comprobante)

    def test_all_own_documents_are_validated_before_search_filter_or_requested_page(self):
        hidden = self.comprobantes[(self.empresa.slug, 0)]
        ComprobanteEgresoCompra.objects.filter(pk=hidden.pk).update(compra=self.compras["medical_spa"])
        before = self.snapshot()
        for query in ({"q": "No existe"}, {"page": "100"}, {"q": self.comprobante.numero_comprobante, "metodo": "tarjeta"}):
            self.assert_rejected(lambda: self.directo("egresos_dashboard", url=self.url("egresos_dashboard", **query)))
        self.assertEqual(self.snapshot(), before)

    def test_foreign_document_purchase_provider_and_payment_relations_deny_list_detail_pdf(self):
        # Imported payment without a document: its ID remains a valid foreign
        # target without violating the unrelated one-to-one unique constraint.
        imported_payment = PagoCompra(compra=self.compras["medical_spa"], monto=Decimal("1"), fecha=date(2026, 10, 1))
        PagoCompra.objects.bulk_create([imported_payment])
        for field, value in (("compra_id", self.compras["medical_spa"].pk), ("proveedor_id", self.proveedores["medical_spa"].pk), ("pago_id", imported_payment.pk)):
            original = getattr(self.comprobante, field)
            try:
                ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(**{field: value})
                before = self.snapshot()
                for route in self.ROUTES:
                    self.assert_rejected(lambda: self.directo(route))
                self.assertEqual(self.snapshot(), before)
            finally:
                ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(**{field: original})
        self.html.assert_not_called()

    def test_orphan_required_document_keys_are_not_hidden_by_inner_joins(self):
        for field in ("compra_id", "proveedor_id", "pago_id"):
            original = getattr(self.comprobante, field)
            with connection.constraint_checks_disabled():
                try:
                    ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(**{field: 2147483647})
                    before = self.snapshot()
                    for route in self.ROUTES:
                        self.assert_rejected(lambda: self.directo(route))
                    self.assertEqual(self.snapshot(), before)
                finally:
                    ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(**{field: original})
        self.html.assert_not_called()

    def test_purchase_provider_company_and_orphan_are_checked_before_document_presentation(self):
        compra = self.compras[self.empresa.slug]
        original = compra.proveedor_id
        for value in (self.proveedores["medical_spa"].pk, 2147483647):
            with connection.constraint_checks_disabled():
                try:
                    CompraInventario.objects.filter(pk=compra.pk).update(proveedor_id=value)
                    for route in self.ROUTES:
                        self.assert_rejected(lambda: self.directo(route))
                finally:
                    CompraInventario.objects.filter(pk=compra.pk).update(proveedor_id=original)
        self.html.assert_not_called()

    def test_payment_must_belong_to_same_purchase_even_when_both_purchases_are_own(self):
        another = CompraInventario.objects.create(empresa=self.empresa, proveedor_nombre="Otra compra QA", estado="aplicada")
        pago = self.pagos[(self.empresa.slug, 1)]
        PagoCompra.objects.filter(pk=pago.pk).update(compra=another)
        before = self.snapshot()
        for route in self.ROUTES:
            self.assert_rejected(lambda: self.directo(route))
        self.assertEqual(self.snapshot(), before)

    def test_payment_accounts_and_contable_must_exist_in_own_company(self):
        pago = self.pagos[(self.empresa.slug, 1)]
        for model, record, field in ((PagoCompra, pago, "cuenta_financiera_id"),):
            original = getattr(record, field)
            try:
                model.objects.filter(pk=record.pk).update(**{field: self.cuentas["medical_spa"].pk})
                for route in self.ROUTES:
                    self.assert_rejected(lambda: self.directo(route))
            finally:
                model.objects.filter(pk=record.pk).update(**{field: original})
        account = self.cuentas[self.empresa.slug]
        original = account.cuenta_contable_id
        try:
            CuentaFinanciera.objects.filter(pk=account.pk).update(cuenta_contable_id=self.cuentas["medical_spa"].cuenta_contable_id)
            for route in self.ROUTES:
                self.assert_rejected(lambda: self.directo(route))
        finally:
            CuentaFinanciera.objects.filter(pk=account.pk).update(cuenta_contable_id=original)
        self.html.assert_not_called()

    def test_orphan_accounts_and_contable_are_detected_without_financial_inner_join(self):
        for model, record, field in ((PagoCompra, self.pagos[(self.empresa.slug, 1)], "cuenta_financiera_id"), (CuentaFinanciera, self.cuentas[self.empresa.slug], "cuenta_contable_id")):
            original = getattr(record, field)
            with connection.constraint_checks_disabled():
                try:
                    model.objects.filter(pk=record.pk).update(**{field: 2147483647})
                    before = self.snapshot()
                    for route in self.ROUTES:
                        self.assert_rejected(lambda: self.directo(route))
                    self.assertEqual(self.snapshot(), before)
                finally:
                    model.objects.filter(pk=record.pk).update(**{field: original})
        self.html.assert_not_called()

    def test_provider_cliente_contable_must_not_reference_foreign_or_orphan_client(self):
        external = Empresa.objects.create(nombre="Administradora QA", slug="dubon_asociados", rtn="MEGR-DUBON")
        client = ClienteContable.objects.create(empresa=external, nombre="Cliente contable ajeno QA")
        provider = self.proveedores[self.empresa.slug]
        for value in (client.pk, 2147483647):
            with connection.constraint_checks_disabled():
                try:
                    Proveedor.objects.filter(pk=provider.pk).update(cliente_contable_id=value)
                    for route in self.ROUTES:
                        self.assert_rejected(lambda: self.directo(route))
                finally:
                    Proveedor.objects.filter(pk=provider.pk).update(cliente_contable_id=None)
        self.html.assert_not_called()

    def test_null_historical_provider_and_accounts_are_legitimate_without_new_catalogue(self):
        CompraInventario.objects.filter(pk=self.compras[self.empresa.slug].pk).update(proveedor=None, cuenta_financiera_pago=None)
        ComprobanteEgresoCompra.objects.filter(empresa=self.empresa).update(proveedor=None)
        PagoCompra.objects.filter(compra=self.compras[self.empresa.slug]).update(cuenta_financiera=None)
        before = self.snapshot()
        for route in self.ROUTES:
            response = self.client.get(self.url(route))
            self.assertEqual(response.status_code, 200)
            if route != "descargar_comprobante_egreso_pdf":
                self.assertContains(response, self.comprobante.proveedor_nombre)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_historical_provider_financial_and_contable_accounts_remain_readable(self):
        Proveedor.objects.filter(pk=self.proveedores[self.empresa.slug].pk).update(activo=False)
        CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(activa=False)
        CuentaContable.objects.filter(pk=self.cuentas[self.empresa.slug].cuenta_contable_id).update(activa=False)
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_document_snapshot_fields_legitimately_differ_from_current_provider_purchase_and_payment(self):
        alternate_provider = Proveedor.objects.create(empresa=self.empresa, nombre="Proveedor documental distinto QA")
        ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(proveedor=alternate_provider)
        Proveedor.objects.filter(pk=self.proveedores[self.empresa.slug].pk).update(nombre="Proveedor actual renombrado")
        CompraInventario.objects.filter(pk=self.compras[self.empresa.slug].pk).update(proveedor_nombre="Nombre compra distinto")
        PagoCompra.objects.filter(pk=self.pagos[(self.empresa.slug, 1)].pk).update(fecha=date(2025, 1, 1), monto=Decimal("99"), metodo="efectivo", referencia="Referencia pago distinta", observacion="Observación pago distinta")
        before = self.snapshot()
        detail = self.client.get(self.url())
        self.assertEqual(detail.status_code, 200)
        for value in (self.comprobante.proveedor_nombre, self.comprobante.referencia, self.comprobante.observacion.splitlines()[0]):
            self.assertContains(detail, value)
        self.assertEqual(detail.context["comprobante"].monto, Decimal("11"))
        self.assertNotContains(detail, "Referencia pago distinta")
        self.assertEqual(self.client.get(self.url("descargar_comprobante_egreso_pdf")).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_list_is_server_paginated_twenty_and_stable_with_tied_creation_dates(self):
        docs = [self.new_document(reference=f"Paginado {index}") for index in range(24)]
        ComprobanteEgresoCompra.objects.filter(empresa=self.empresa).update(fecha_creacion=timezone.now())
        expected = list(ComprobanteEgresoCompra.objects.filter(empresa=self.empresa).order_by("-fecha_creacion", "-id").values_list("pk", flat=True))
        first = self.client.get(self.url("egresos_dashboard"))
        second = self.client.get(self.url("egresos_dashboard", page="2"))
        self.assertEqual([row.pk for row in first.context["egresos"]], expected[:20])
        self.assertEqual([row.pk for row in second.context["egresos"]], expected[20:])
        self.assertEqual(first.context["egresos"].paginator.per_page, 20)
        self.assertEqual(first.context["egresos"].paginator.count, 28)
        self.assertEqual(first.context["egresos"][0].pk, docs[-1].pk)

    def test_search_is_applied_server_side_before_pagination_for_each_original_search_field(self):
        for query in (self.comprobante.numero_comprobante.lower(), self.comprobante.referencia.lower(), self.compras[self.empresa.slug].numero_compra.lower(), self.comprobante.proveedor_nombre.lower()):
            response = self.client.get(self.url("egresos_dashboard", q=" "+query+" "))
            self.assertEqual(response.status_code, 200)
            self.assertIn(self.comprobante.pk, [row.pk for row in response.context["egresos"]])
        docs = [self.new_document(reference="Filtro servidor común") for _ in range(23)]
        for _ in range(22):
            self.new_document(reference="No coincide con filtro")
        page = self.client.get(self.url("egresos_dashboard", q="Filtro servidor común", page="2"))
        self.assertEqual(page.context["egresos"].paginator.count, len(docs))
        self.assertEqual(len(page.context["egresos"]), 3)
        self.assertEqual({row.referencia for row in page.context["egresos"]}, {"Filtro servidor común"})

    def test_only_three_original_method_filters_but_cheque_remains_visible_in_all_documents(self):
        all_docs = self.client.get(self.url("egresos_dashboard"))
        self.assertContains(all_docs, "Cheque")
        self.assertIn(self.comprobantes[(self.empresa.slug, 3)].pk, [row.pk for row in all_docs.context["egresos"]])
        for metodo in ("efectivo", "transferencia", "tarjeta"):
            response = self.client.get(self.url("egresos_dashboard", metodo=metodo))
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row.metodo for row in response.context["egresos"]}, {metodo})
        self.assertNotContains(all_docs, '<option value="cheque"')

    def test_filtered_total_is_full_hnl_dataset_not_just_twenty_visible_rows(self):
        docs = [self.new_document(reference="Total servidor", metodo="transferencia") for _ in range(23)]
        page = self.client.get(self.url("egresos_dashboard", q="Total servidor", metodo="transferencia"))
        self.assertEqual(page.context["mobile_egreso_total"], len(docs))
        self.assertEqual(page.context["egresos"].paginator.count, len(docs))
        self.assertEqual(len(page.context["egresos"]), 20)
        self.assertContains(page, "L.")
        self.assertFalse(any(field.name == "moneda" for field in CompraInventario._meta.fields))

    def test_pagination_links_keep_company_app_search_and_original_method_filter(self):
        for _ in range(23):
            self.new_document(reference="Paginación y filtro", metodo="tarjeta")
        first = self.client.get(self.url("egresos_dashboard", q="Paginación y filtro", metodo="tarjeta"))
        next_url = first.context["mobile_egreso_next_url"]
        self.assertEqual(urlsplit(next_url).path, reverse("egresos_dashboard", args=[self.empresa.slug]))
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["Paginación y filtro"], "metodo": ["tarjeta"], "page": ["2"]})
        second = self.client.get(next_url)
        previous = second.context["mobile_egreso_previous_url"]
        self.assertEqual(parse_qs(urlsplit(previous).query)["page"], ["1"])

    def test_empty_list_remains_mobile_without_seed_create_or_web_purchase_link(self):
        response = self.client.get(self.url("egresos_dashboard", q="Nada coincide QA"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["egresos"].paginator.count, 0)
        self.assertTemplateUsed(response, "facturacion/mobile_egreso_list.html")
        self.assertNotContains(response, "<table")
        self.assertNotContains(response, reverse("crear_compra", args=[self.empresa.slug]))

    def test_detail_and_list_only_link_mobile_egreso_or_original_pdf_never_web_purchase_provider(self):
        self.asignar_rol(puede_egresos=True, puede_compras=True, puede_proveedores=True)
        for route in self.ROUTES[:2]:
            response = self.client.get(self.url(route))
            self.assertNotContains(response, reverse("ver_compra", args=[self.empresa.slug, self.compras[self.empresa.slug].pk]))
            self.assertNotContains(response, reverse("ver_proveedor_facturacion", args=[self.empresa.slug, self.proveedores[self.empresa.slug].pk]))
        listing = self.client.get(self.url("egresos_dashboard"))
        self.assertContains(listing, self.url())
        self.assertContains(self.client.get(self.url()), self.url("descargar_comprobante_egreso_pdf"))

    def test_hub_permission_visibility_is_exact_and_safe_without_granting_calendar(self):
        from crm.mobile_modules import construir_contexto_modulos_app
        request = RequestFactory().get("/")
        request.user = self.usuario
        before = self.snapshot()
        cards = {item["key"]: item for item in construir_contexto_modulos_app(request, self.empresa)["modulos_app"]}
        self.assertEqual((cards["egresos"]["estado"], cards["egresos"]["url"]), ("disponible", self.url("egresos_dashboard")))
        self.assertNotIn("recibos", cards)
        self.assertNotIn("compras", cards)
        self.assertEqual(self.snapshot(), before)
        self.asignar_rol(puede_compras=True)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertNotIn("egresos", {item["key"]: item for item in construir_contexto_modulos_app(request, self.empresa)["modulos_app"]})

    def test_logout_redirects_and_csrf_does_not_enable_writes_on_readonly_endpoints(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(protected.get(self.url(route)).status_code, 200)
            self.assertIn(protected.post(self.url(route), {"app": "1", "accion": "eliminar"}).status_code, (302, 403, 405))
        self.assertEqual(self.snapshot(), before)
        self.client.logout()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 302)

    def test_snapshot_text_and_search_are_escaped_without_inventing_document_actions(self):
        ComprobanteEgresoCompra.objects.filter(pk=self.comprobante.pk).update(proveedor_nombre="<script>egreso_synthetic()</script>", observacion="<img src=x onerror=egreso_synthetic()>")
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;egreso_synthetic()&lt;/script&gt;")
        self.assertNotContains(response, "<script>egreso_synthetic()")
        self.assertNotContains(response, 'method="post"')
        search = self.client.get(self.url("egresos_dashboard", q='<script>search_synthetic()</script>'))
        self.assertNotContains(search, "<script>search_synthetic()")
