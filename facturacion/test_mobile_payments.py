"""Mobile collection reuses the payment parser, receipts and accounting saves."""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, RegistroAuditoria, Usuario
from contabilidad.models import AsientoContable, CuentaContable, CuentaFinanciera
from contabilidad.services import asegurar_cuenta_contable_cliente, cargar_catalogo_base_honduras
from . import test_mobile_receipts as receipt_fixtures
from . import views
from .models import CAI, Cliente, Factura, LineaFactura, MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoComisionProveedor, PagoFactura, Producto, Proveedor, ReciboPago


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePaymentsIntegrationTests(TestCase):
    # Reuse synthetic setup helpers through a module, never inherit/import a
    # second discoverable TestCase and never use a production database.
    SLUGS = receipt_fixtures.MobileReceiptsIntegrationTests.SLUGS
    PDF = receipt_fixtures.MobileReceiptsIntegrationTests.PDF
    asignar_rol = receipt_fixtures.MobileReceiptsIntegrationTests.asignar_rol
    snapshot = receipt_fixtures.MobileReceiptsIntegrationTests.snapshot
    financial_snapshot = receipt_fixtures.MobileReceiptsIntegrationTests.financial_snapshot
    side_counts = receipt_fixtures.MobileReceiptsIntegrationTests.side_counts

    @classmethod
    def setUpTestData(cls):
        receipt_fixtures.MobileReceiptsIntegrationTests.setUpTestData.__func__(cls)
        cls.rol.puede_recibos = False
        cls.rol.puede_registrar_pagos_clientes = True
        cls.rol.save(update_fields=["puede_recibos", "puede_registrar_pagos_clientes"])
        cls.cais = {}
        for empresa in cls.empresas.values():
            cls.cais[empresa.slug] = CAI.objects.create(empresa=empresa, numero_cai=f"CAI-MPAY-{empresa.slug}", establecimiento="001", punto_emision="001", tipo_documento="01", rango_inicial=1, rango_final=100000, correlativo_actual=0, fecha_activacion=date(2020, 1, 1), fecha_limite=date(2030, 12, 31))
            cargar_catalogo_base_honduras(empresa)
            asegurar_cuenta_contable_cliente(cls.clientes[empresa.slug])

    def setUp(self):
        receipt_fixtures.MobileReceiptsIntegrationTests.setUp(self)

    def url(self, empresa=None, factura=None, edit=False, pago=None, mobile=True, **parameters):
        empresa = empresa or self.empresa
        factura = factura or self.facturas[empresa.slug]
        if edit:
            pago = pago or self.pagos[(empresa.slug, 1)]
        route = reverse("editar_pago_factura" if edit else "registrar_pago", args=[empresa.slug, factura.pk]+([pago.pk] if edit else []))
        params = {"app": "1"} if mobile else {}
        params.update(parameters)
        return route+("?"+urlencode(params) if params else "")

    def datos(self, empresa=None, mixed=False, **extra):
        empresa = empresa or self.empresa
        data = {"app": "1", "fecha": "2026-10-12", "retencion_isr": "0", "retencion_isv": "0", "porcentaje_comision": "0", "monto_comision": "0", "proveedor_comision": "", "cuenta_financiera_impuesto": ""}
        if mixed:
            for metodo, monto in (("efectivo", "40"), ("tarjeta", "60"), ("transferencia", "50")):
                data.update({f"monto_{metodo}": monto, f"cuenta_{metodo}": str(self.cuentas[empresa.slug].pk), f"referencia_{metodo}": f"QA-{metodo}"})
        else:
            data.update({"monto": "100", "metodo": "transferencia", "cuenta_financiera": str(self.cuentas[empresa.slug].pk), "referencia": "QA-PAGO-NUEVO"})
        data.update(extra)
        return data

    def directo(self, empresa=None, factura=None, edit=False, pago=None, method="get", data=None, url=None):
        url = url or self.url(empresa, factura, edit=edit, pago=pago)
        body = self.datos(empresa) if data is None else data
        if isinstance(body, QueryDict):
            body = {key: body.getlist(key) for key in body}
        request = RequestFactory().post(url, body) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def stock_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (CAI, Producto, LineaFactura, MovimientoInventario, MovimientoLoteBodega))

    def new_invoice(self, impuesto=Decimal("15")):
        return Factura.objects.create(empresa=self.empresa, cliente=self.clientes[self.empresa.slug], estado="borrador", subtotal=Decimal("100"), impuesto=impuesto, total=Decimal("100")+impuesto, total_lempiras=Decimal("100")+impuesto)

    def test_four_company_mobile_get_edit_and_original_web_modal_templates(self):
        for empresa in self.empresas.values():
            before = self.snapshot()
            for edit in (False, True):
                app = self.client.get(self.url(empresa, edit=edit))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, "facturacion/mobile_payment_form.html")
                self.assertIn("no-store", app["Cache-Control"])
                self.assertNotContains(app, "<table")
            self.assertEqual(self.snapshot(), before)
            for modal, template in ((False, "facturacion/registrar_pago.html"), (True, "facturacion/registrar_pago_modal.html")):
                response = self.client.get(self.url(empresa, mobile=False, **({"modal": "1"} if modal else {})))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template)

    def test_get_head_without_optional_configuration_never_runs_original_seed_helpers(self):
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "_cuentas_financieras_activas_para_pago") as seed, patch.object(views, "asegurar_cuentas_financieras_base_honduras") as base, patch.object(views, "cargar_catalogo_base_honduras") as catalogue:
            for edit in (False, True):
                for method in ("get", "head"):
                    self.assertEqual(getattr(self.client, method)(self.url(edit=edit)).status_code, 200)
            seed.assert_not_called()
            base.assert_not_called()
            catalogue.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_empty_active_account_catalog_is_readonly_presentation_without_creating_accounts(self):
        CuentaFinanciera.objects.filter(empresa=self.empresa).update(activa=False)
        before = self.snapshot()
        for edit in (False, True):
            response = self.client.get(self.url(edit=edit))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(response.context["cuentas_financieras"]), [])
        self.assertEqual(self.snapshot(), before)

    def test_original_web_get_still_calls_its_original_account_seed_path(self):
        with patch.object(views, "_cuentas_financieras_activas_para_pago", wraps=views._cuentas_financieras_activas_para_pago) as original:
            self.assertEqual(self.client.get(self.url(mobile=False)).status_code, 200)
            original.assert_called_once()

    def test_payment_permission_alone_does_not_grant_invoice_receipt_or_patient_reads(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["app_invoice_detail_url"], "")
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        for route, identifier in (("ver_factura", self.facturas[self.empresa.slug].pk), ("ver_recibo", self.recibos[(self.empresa.slug, 1)].pk)):
            self.assertIn(self.client.get(reverse(route, args=[self.empresa.slug, identifier])+"?app=1").status_code, (302, 403))
        self.assertEqual(self.client.post(self.url(), self.datos()).url, self.url())

    def test_unrelated_permissions_cannot_open_or_write_even_without_middleware(self):
        for grants in ({"puede_ver_facturas": True}, {"puede_recibos": True}, {"puede_editar_facturas": True}):
            self.asignar_rol(**grants)
            before = self.snapshot()
            for edit in (False, True):
                for method in ("get", "post"):
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises((PermissionDenied, Http404)):
                            self.directo(edit=edit, method=method)
                    self.assertFalse(any('"facturacion_pagofactura"' in item["sql"] for item in queries))
            self.assertEqual(self.snapshot(), before)

    def test_legacy_payment_permission_is_same_independent_action(self):
        self.asignar_rol(granular=False, puede_registrar_pagos_clientes=True)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url(edit=True)).status_code, 200)
        self.assertEqual(self.client.post(self.url(), self.datos()).url, self.url())

    def test_direct_company_module_membership_active_and_license_guards(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for edit in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(edit=edit)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for invalid in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**invalid)
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo()
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(other)

    def test_valid_simple_all_four_reuses_original_payment_receipt_and_accounting(self):
        for empresa in self.empresas.values():
            before, stock = PagoFactura.objects.count(), self.stock_snapshot()
            result = self.client.post(self.url(empresa), self.datos(empresa))
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result.url, self.url(empresa))
            pago = PagoFactura.objects.get(factura=self.facturas[empresa.slug], referencia="QA-PAGO-NUEVO")
            self.assertEqual((pago.monto, pago.metodo, pago.cuenta_financiera_id, pago.cajero_id, pago.fecha), (Decimal("100"), "transferencia", self.cuentas[empresa.slug].pk, self.usuario.pk, date(2026, 10, 12)))
            self.assertEqual((pago.recibo.monto, pago.recibo.fecha, pago.recibo.referencia), (pago.monto, pago.fecha, pago.referencia))
            self.assertEqual(PagoFactura.objects.count(), before+1)
            self.assertTrue(AsientoContable.objects.filter(empresa=empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro").exists())
            self.assertEqual(self.stock_snapshot(), stock)
        factura_usd = self.new_invoice()
        Factura.objects.filter(pk=factura_usd.pk).update(moneda="USD", tipo_cambio=Decimal("25"), total_lempiras=Decimal("2875"))
        response = self.client.post(self.url(factura=factura_usd), self.datos(referencia="QA-USD"))
        self.assertEqual(response.status_code, 302)
        pago_usd = PagoFactura.objects.get(factura=factura_usd)
        self.assertEqual((pago_usd.monto, pago_usd.recibo.monto), (Decimal("100"), Decimal("100")))
        success = [str(message) for message in get_messages(response.wsgi_request) if message.level == 25][-1]
        self.assertIn("$ 100.00", success)
        self.assertNotIn("L. 100.00", success)

    def test_retentions_isv_split_and_original_commission_calculation_are_preserved(self):
        data = self.datos(retencion_isr="10", retencion_isv="5", separar_isv="on", cuenta_financiera_impuesto=str(self.cuentas[self.empresa.slug].pk), porcentaje_comision="5", proveedor_comision=str(self.proveedores[self.empresa.slug].pk), monto_comision="999")
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        pago = PagoFactura.objects.get(referencia=data["referencia"])
        self.assertEqual((pago.total_retenciones, pago.total_aplicado, pago.monto_comision), (Decimal("15"), Decimal("115"), Decimal("5.75")))
        self.assertTrue(pago.separar_isv)
        self.assertEqual(pago.cuenta_financiera_impuesto_id, self.cuentas[self.empresa.slug].pk)
        asiento = AsientoContable.objects.get(documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro")
        self.assertEqual(sum((line.debe for line in asiento.lineas.all()), Decimal("0")), sum((line.haber for line in asiento.lineas.all()), Decimal("0")))

    def test_retention_only_zero_money_is_legitimate_without_income_account(self):
        factura = self.new_invoice(impuesto=Decimal("0"))
        CuentaFinanciera.objects.filter(empresa=self.empresa).update(activa=False)
        data = self.datos(monto="0", retencion_isr="10", cuenta_financiera="")
        response = self.client.post(self.url(factura=factura), data)
        self.assertEqual(response.status_code, 302, [str(message) for message in get_messages(response.wsgi_request)])
        pago = PagoFactura.objects.get(factura=factura)
        self.assertEqual((pago.monto, pago.total_aplicado, pago.cuenta_financiera_id), (Decimal("0"), Decimal("10"), None))
        self.assertEqual(pago.recibo.monto, Decimal("0"))
        before = self.snapshot()
        mixed = self.datos(mixed=True, cuenta_efectivo="", cuenta_tarjeta="", cuenta_transferencia="")
        response = self.client.post(self.url(factura=factura), mixed)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form_data"]["cuenta_efectivo"], "")
        self.assertEqual(self.snapshot(), before)

    def test_mixed_three_payments_use_same_first_retention_commission_and_receipts(self):
        before = PagoFactura.objects.count()
        data = self.datos(mixed=True, retencion_isr="10", retencion_isv="5", porcentaje_comision="5", proveedor_comision=str(self.proveedores[self.empresa.slug].pk))
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        rows = list(PagoFactura.objects.filter(factura=self.facturas[self.empresa.slug], referencia__startswith="QA-").order_by("pk"))
        self.assertEqual([row.metodo for row in rows], ["efectivo", "tarjeta", "transferencia"])
        self.assertEqual(sum((row.total_aplicado for row in rows), Decimal("0")), Decimal("165"))
        self.assertEqual([row.total_retenciones for row in rows], [Decimal("15"), Decimal("0"), Decimal("0")])
        self.assertEqual([row.monto_comision for row in rows], [Decimal("8.25"), Decimal("0"), Decimal("0")])
        self.assertTrue(all(row.recibo.monto == row.monto for row in rows))
        self.assertEqual(PagoFactura.objects.count(), before+3)

    def test_mixed_retention_without_money_and_invalid_simple_remain_bound_without_writes(self):
        cases = (self.datos(mixed=True, monto_efectivo="", monto_tarjeta="", monto_transferencia="", retencion_isr="10"), self.datos(monto="-1"), self.datos(monto="999999"), self.datos(fecha="2026-02-31"))
        for data in cases:
            before = self.snapshot()
            response = self.client.post(self.url(), data)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_payment_form.html")
            for key, value in data.items():
                self.assertEqual(response.context["form_data"].get(key), value)
            self.assertEqual(self.snapshot(), before)

    def test_nonfinite_and_unrepresentable_money_preserves_raw_without_write_or_server_error(self):
        for field, value in (("monto", "NaN"), ("monto", "Infinity"), ("monto", "1e100"), ("retencion_isr", "-Infinity"), ("porcentaje_comision", "NaN")):
            before = self.snapshot()
            response = self.client.post(self.url(), self.datos(**{field: value}))
            self.assertIn(response.status_code, (200, 403))
            if response.status_code == 200:
                self.assertEqual(response.context["form_data"].get(field), value)
            self.assertEqual(self.snapshot(), before)

    def test_omitted_account_uses_original_default_but_explicit_foreign_inactive_or_missing_never_falls_back(self):
        data = self.datos()
        data.pop("cuenta_financiera")
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        pago = PagoFactura.objects.get(referencia=data["referencia"])
        self.assertIsNotNone(pago.cuenta_financiera_id)
        before = PagoFactura.objects.count()
        mixed = self.datos(mixed=True, cuenta_efectivo="", cuenta_tarjeta="", cuenta_transferencia="")
        self.assertEqual(self.client.post(self.url(), mixed).status_code, 302)
        self.assertEqual(PagoFactura.objects.count(), before+3)
        self.assertTrue(all(pago.cuenta_financiera_id and pago.cuenta_financiera.empresa_id == self.empresa.pk for pago in PagoFactura.objects.filter(factura=self.facturas[self.empresa.slug], referencia__startswith="QA-")))
        for value in (str(self.cuentas["medical_spa"].pk), "2147483647"):
            before = self.snapshot()
            self.assertIn(self.client.post(self.url(), self.datos(cuenta_financiera=value)).status_code, (403, 404))
            self.assertEqual(self.snapshot(), before)
        CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(activa=False)
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(method="post")

    def test_explicit_mixed_and_tax_accounts_or_provider_are_scoped_before_any_save(self):
        for field in ("cuenta_efectivo", "cuenta_tarjeta", "cuenta_transferencia", "cuenta_financiera_impuesto"):
            data = self.datos(mixed=True, **{field: str(self.cuentas["medical_spa"].pk)})
            before = self.snapshot()
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(method="post", data=data)
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(method="post", data=self.datos(porcentaje_comision="5", proveedor_comision=str(self.proveedores["medical_spa"].pk)))

    def test_edit_old_payment_synchronizes_receipt_and_rebuilds_every_invoice_payment(self):
        pago = self.pagos[(self.empresa.slug, 0)]
        before, stock = PagoFactura.objects.count(), self.stock_snapshot()
        with patch.object(views, "_recalcular_y_recontabilizar_pagos_factura", wraps=views._recalcular_y_recontabilizar_pagos_factura) as rebuild:
            response = self.client.post(self.url(edit=True, pago=pago), self.datos(monto="120", fecha="2026-09-20", referencia="QA-CORRECCION"))
            self.assertEqual(response.status_code, 302)
            rebuild.assert_called_once()
        pago.refresh_from_db()
        self.assertEqual((pago.monto, pago.fecha, pago.recibo.fecha, pago.recibo.referencia), (Decimal("120"), date(2026, 9, 20), pago.fecha, "QA-CORRECCION"))
        ids = list(PagoFactura.objects.filter(factura=pago.factura).values_list("pk", flat=True))
        self.assertEqual(AsientoContable.objects.filter(documento_tipo="pago_factura", documento_id__in=ids, evento="cobro").count(), 3)
        self.assertEqual(PagoFactura.objects.count(), before)
        self.assertEqual(self.stock_snapshot(), stock)

    def test_edit_can_split_existing_payment_into_two_using_original_first_id_and_receipt(self):
        pago = self.pagos[(self.empresa.slug, 1)]
        original_receipt = pago.recibo.pk
        before = PagoFactura.objects.count()
        data = self.datos(mixed=True, monto_efectivo="", monto_tarjeta="75", monto_transferencia="125")
        self.assertEqual(self.client.post(self.url(edit=True), data).status_code, 302)
        pago.refresh_from_db()
        self.assertEqual((pago.monto, pago.metodo, pago.recibo.pk), (Decimal("75"), "tarjeta", original_receipt))
        self.assertTrue(PagoFactura.objects.filter(factura=pago.factura, monto=Decimal("125"), metodo="transferencia", referencia="QA-transferencia").exists())
        self.assertEqual(PagoFactura.objects.count(), before+1)

    def test_registration_accounting_failure_rolls_back_payment_receipt_invoice_and_audit(self):
        before = self.snapshot()
        with patch.object(views, "registrar_asiento_pago_cliente", side_effect=RuntimeError("synthetic accounting failure")):
            self.assertEqual(self.client.post(self.url(), self.datos()).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_mixed_second_accounting_failure_and_edit_failure_are_atomic(self):
        original = views.registrar_asiento_pago_cliente
        calls = []
        def fail_second(pago):
            calls.append(pago.pk)
            if len(calls) == 2:
                raise RuntimeError("synthetic second accounting failure")
            return original(pago)
        before = self.snapshot()
        with patch.object(views, "registrar_asiento_pago_cliente", side_effect=fail_second):
            self.assertEqual(self.client.post(self.url(), self.datos(mixed=True)).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        with patch.object(views, "registrar_asiento_pago_cliente", side_effect=RuntimeError("synthetic rebuild failure")):
            result = self.client.post(self.url(edit=True), self.datos(monto="150"))
            self.assertEqual(result.status_code, 200)
            persisted = PagoFactura.objects.get(pk=self.pagos[(self.empresa.slug, 1)].pk)
            displayed = result.context["pago_edicion"]
            self.assertEqual((displayed.monto, displayed.fecha, displayed.referencia), (persisted.monto, persisted.fecha, persisted.referencia))
            self.assertEqual((result.context["form_data"]["monto"], result.context["form_data"]["fecha"], result.context["form_data"]["referencia"]), ("150", "2026-10-12", "QA-PAGO-NUEVO"))
        self.assertEqual(self.snapshot(), before)

    def test_global_audit_records_original_payment_actor_and_receipt_changes(self):
        started = timezone.now()
        self.assertEqual(self.client.post(self.url(), self.datos()).status_code, 302)
        pago = PagoFactura.objects.get(referencia="QA-PAGO-NUEVO")
        audit = RegistroAuditoria.objects.get(modelo="pagofactura", objeto_id=str(pago.pk), accion="crear")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertGreaterEqual(audit.fecha, started)
        self.assertEqual(audit.cambios["monto"]["nuevo"], "100")
        self.assertTrue(RegistroAuditoria.objects.filter(modelo="recibopago", objeto_id=str(pago.recibo.pk), usuario=self.usuario).exists())

    def test_corrupted_linked_receipt_of_any_invoice_payment_denies_before_receipt_sync_or_rebuild(self):
        receipt = self.recibos[(self.empresa.slug, 2)]
        ReciboPago.objects.filter(pk=receipt.pk).update(empresa=self.empresas["medical_spa"])
        before = self.snapshot()
        with patch.object(views, "_recalcular_y_recontabilizar_pagos_factura") as rebuild:
            for edit in (False, True):
                for method in ("get", "post"):
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo(edit=edit, method=method)
            rebuild.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_invoice_customer_payment_and_foreign_path_scopes_are_checked_before_lookup_or_save(self):
        foreign_factura = self.facturas["medical_spa"]
        foreign_pago = self.pagos[("medical_spa", 1)]
        before = self.snapshot()
        for edit, factura, pago in ((False, foreign_factura, None), (True, self.facturas[self.empresa.slug], foreign_pago)):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(factura=factura, pago=pago, edit=edit)
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(cliente=self.clientes["medical_spa"])
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(method="post")
        self.assertEqual(PagoFactura.objects.count(), len(before[1][0]))

    def test_sibling_financial_relations_and_issued_credit_note_scope_deny_before_register_edit(self):
        sibling = self.pagos[(self.empresa.slug, 2)]
        PagoFactura.objects.filter(pk=sibling.pk).update(cuenta_financiera=self.cuentas["medical_spa"])
        for edit in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(edit=edit, method="post")
        PagoFactura.objects.filter(pk=sibling.pk).update(cuenta_financiera=self.cuentas[self.empresa.slug])
        note = NotaCredito.objects.create(empresa=self.empresa, factura_origen=self.facturas[self.empresa.slug], cliente=self.clientes[self.empresa.slug], estado="borrador", subtotal=Decimal("10"), total=Decimal("10"), total_lempiras=Decimal("10"))
        NotaCredito.objects.filter(pk=note.pk).update(estado="emitida", empresa=self.empresas["medical_spa"])
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(method="post")

    def test_inactive_historical_account_and_multicompany_cashier_do_not_revoke_history(self):
        actor = Usuario.objects.create_user(username="payment-multi-company", empresa=self.empresas["medical_spa"])
        PagoFactura.objects.filter(pk=self.pagos[(self.empresa.slug, 0)].pk).update(cajero=actor)
        CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(activa=False)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url(edit=True)).status_code, 200)

    def test_closed_query_and_post_metadata_duplicates_and_malformed_ids_deny_without_writes(self):
        before = self.snapshot()
        for query in ("app=1&app=1", "app=1&modal=1", "app=1&unknown=1"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(url=self.url().split("?")[0]+"?"+query)
        for key in ("app", "monto", "fecha", "cuenta_financiera", "referencia"):
            data = QueryDict(mutable=True)
            data.update(self.datos())
            data.setlist(key, ["1", "1"])
            with self.assertRaises(PermissionDenied):
                self.directo(method="post", data=data)
        for field in ("empresa", "factura", "pago_id", "cajero", "accion", "recibo_id", "initial-monto"):
            data = self.datos()
            data[field] = "1"
            with self.assertRaises(PermissionDenied):
                self.directo(method="post", data=data)
        with self.assertRaises(PermissionDenied):
            self.directo(method="post", url=self.url(app="0"))
        with self.assertRaises(PermissionDenied):
            self.directo(method="post", data=self.datos(app="0"))
        for value in ("00"+str(self.cuentas[self.empresa.slug].pk), "NaN", "1e2", "9223372036854775808"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(method="post", data=self.datos(cuenta_financiera=value))
        self.assertEqual(self.snapshot(), before)

    def test_noncanonical_invoice_payment_ids_and_method_csrf_logout_are_blocked(self):
        before = self.snapshot()
        for suffix in ("0", "00"+str(self.facturas[self.empresa.slug].pk), "9223372036854775808"):
            url = self.url().replace(f"/{self.facturas[self.empresa.slug].pk}/pago/", f"/{suffix}/pago/")
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(url=url)
        for method in ("put", "delete", "patch"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(method=method)
        self.assertEqual(self.snapshot(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url()).status_code, 200)
        self.assertIn(protected.post(self.url(), self.datos()).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(protected.post(self.url(), self.datos(), HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_paid_register_and_annulled_invoice_render_safe_mobile_readonly_not_forbidden_invoice(self):
        factura = self.new_invoice(impuesto=Decimal("0"))
        PagoFactura.objects.create(factura=factura, monto=Decimal("100"), cuenta_financiera=self.cuentas[self.empresa.slug])
        response = self.client.get(self.url(factura=factura))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["mobile_payment_readonly"])
        self.assertNotContains(response, '<form id="mobilePaymentForm"')
        paid_payment = PagoFactura.objects.get(factura=factura)
        self.assertFalse(self.client.get(self.url(factura=factura, edit=True, pago=paid_payment)).context["mobile_payment_readonly"])
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url(factura=factura), self.datos()).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(estado="anulada")
        for edit in (False, True):
            response = self.client.get(self.url(edit=edit))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_payment_readonly"])
            self.assertNotContains(response, '<form id="mobilePaymentForm"')
            before = self.snapshot()
            self.assertEqual(self.client.post(self.url(edit=edit), self.datos()).status_code, 200)
            self.assertEqual(self.snapshot(), before)

    def test_success_return_with_real_invoice_read_permission_and_invoice_detail_action_flags(self):
        self.asignar_rol(puede_registrar_pagos_clientes=True, puede_ver_facturas=True)
        factura = self.facturas[self.empresa.slug]
        target = reverse("ver_factura", args=[self.empresa.slug, factura.pk])+"?app=1"
        detail = self.client.get(target)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, self.url())
        self.assertEqual(self.client.post(self.url(), self.datos()).url, target)
        self.asignar_rol(puede_ver_facturas=True)
        self.assertNotContains(self.client.get(target), self.url())

    def test_original_modal_post_success_is_unchanged_and_no_extra_app_parameters_are_needed(self):
        data = self.datos()
        data.pop("app")
        result = self.client.post(self.url(mobile=False, modal="1"), data)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "erp-pago-guardado")
        self.assertEqual(result["X-Frame-Options"], "SAMEORIGIN")

    def test_pure_capability_and_hub_do_not_advertise_an_unauthorized_invoice_picker(self):
        from crm.mobile_modules import construir_contexto_modulos_app
        from .mobile_payments import cobros_disponibles_app
        request = RequestFactory().get("/")
        request.user = self.usuario
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(cobros_disponibles_app(request, self.empresa))
        self.assertFalse(any(f'"{model._meta.db_table}"' in item["sql"] for item in queries for model in (Factura, PagoFactura, ReciboPago, CuentaFinanciera)))
        modules = {item["key"]: item for item in construir_contexto_modulos_app(request, self.empresa)["modulos_app"]}
        self.assertEqual((modules["cobros"]["estado"], modules["cobros"]["url"]), ("pendiente", ""))
        self.assertNotIn("recibos", modules)
        self.assertEqual(self.snapshot(), before)
        self.asignar_rol(puede_recibos=True)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertFalse(cobros_disponibles_app(request, self.empresa))
        self.assertNotIn("cobros", {item["key"]: item for item in construir_contexto_modulos_app(request, self.empresa)["modulos_app"]})

    def test_other_clinical_company_app_flag_retains_its_original_web_flow(self):
        from .mobile_payments import cobros_disponibles_app
        empresa = Empresa.objects.create(nombre="Otra clínica cobros QA", slug="otra_clinica_cobros", rtn="MPAY-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        cliente = Cliente.objects.create(empresa=empresa, nombre="Otro cliente QA", rtn="0801199900143")
        factura = Factura.objects.create(empresa=empresa, cliente=cliente, estado="borrador", subtotal=Decimal("100"), total=Decimal("100"), total_lempiras=Decimal("100"))
        request = RequestFactory().get("/")
        request.user = self.usuario
        self.assertFalse(cobros_disponibles_app(request, empresa))
        response = self.client.get(self.url(empresa, factura))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/registrar_pago.html")
        self.assertTemplateNotUsed(response, "facturacion/mobile_payment_form.html")

    def test_orphan_invoice_receipt_and_every_sibling_financial_key_denies_before_write(self):
        sibling = self.pagos[(self.empresa.slug, 2)]
        cases = [(Factura, self.facturas[self.empresa.slug], "cliente_id"), (ReciboPago, self.recibos[(self.empresa.slug, 2)], "factura_id"), (ReciboPago, self.recibos[(self.empresa.slug, 2)], "cliente_id")]
        cases += [(PagoFactura, sibling, field) for field in ("cuenta_financiera_id", "cuenta_financiera_impuesto_id", "proveedor_comision_id", "cajero_id")]
        cases.append((CuentaFinanciera, self.cuentas[self.empresa.slug], "cuenta_contable_id"))
        for model, record, field in cases:
            original = getattr(record, field)
            with connection.constraint_checks_disabled():
                try:
                    model.objects.filter(pk=record.pk).update(**{field: 2147483647})
                    before = self.snapshot()
                    for edit in (False, True):
                        for method in ("get", "post"):
                            with self.assertRaises((PermissionDenied, Http404)):
                                self.directo(edit=edit, method=method)
                    self.assertEqual(self.snapshot(), before)
                finally:
                    model.objects.filter(pk=record.pk).update(**{field: original})

    def test_third_party_contable_and_paid_commission_history_never_cross_company(self):
        foreign_account = self.cuentas["medical_spa"]
        for model, record in ((Cliente, self.clientes[self.empresa.slug]), (Proveedor, self.proveedores[self.empresa.slug])):
            original = record.cuenta_contable_id
            try:
                model.objects.filter(pk=record.pk).update(cuenta_contable_id=foreign_account.cuenta_contable_id)
                before = self.snapshot()
                data = self.datos(porcentaje_comision="5", proveedor_comision=str(self.proveedores[self.empresa.slug].pk))
                for edit in (False, True):
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo(edit=edit, method="post", data=data)
                self.assertEqual(self.snapshot(), before)
            finally:
                model.objects.filter(pk=record.pk).update(cuenta_contable_id=original)
        sibling = self.pagos[(self.empresa.slug, 2)]
        PagoFactura.objects.filter(pk=sibling.pk).update(proveedor_comision=self.proveedores[self.empresa.slug], monto_comision=Decimal("5"))
        sibling.refresh_from_db()
        paid = PagoComisionProveedor.objects.create(comision_origen=sibling, monto=Decimal("1"), cuenta_financiera=self.cuentas[self.empresa.slug], registrado_por=self.usuario)
        for field, value in (("cuenta_financiera_id", foreign_account.pk), ("registrado_por_id", 2147483647)):
            original = getattr(paid, field)
            with connection.constraint_checks_disabled():
                try:
                    PagoComisionProveedor.objects.filter(pk=paid.pk).update(**{field: value})
                    before = self.snapshot()
                    for edit in (False, True):
                        with self.assertRaises((PermissionDenied, Http404)):
                            self.directo(edit=edit, method="post")
                    self.assertEqual(self.snapshot(), before)
                finally:
                    PagoComisionProveedor.objects.filter(pk=paid.pk).update(**{field: original})

    def test_simple_and_mixed_envelopes_cannot_be_combined_or_upload_files(self):
        before = self.snapshot()
        with patch.object(views, "registrar_asiento_pago_cliente") as accounting:
            for data in (self.datos(monto_efectivo="1"), self.datos(mixed=True, monto="100"), self.datos(mixed=True, cuenta_financiera=str(self.cuentas[self.empresa.slug].pk))):
                with self.assertRaises(PermissionDenied):
                    self.directo(method="post", data=data)
            for edit in (False, True):
                data = self.datos(archivo=SimpleUploadedFile("unexpected.txt", b"unexpected payload"))
                with self.assertRaises(PermissionDenied):
                    self.directo(edit=edit, method="post", data=data)
            accounting.assert_not_called()
        self.assertEqual(self.snapshot(), before)
