"""Mobile purchase registration uses the original saver and synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import test_mobile_purchases as purchase_fixture
from . import views
from .models import (
    CAI, CompraInventario, CuentaAcumuladoCompra, Factura, InventarioProducto,
    LineaCompraInventario, MovimientoInventario, MovimientoLoteBodega, NotaCredito,
    PagoCompra, PagoFactura, Proveedor, ReciboPago,
)


class _PurchaseMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePurchaseCreateTests(TestCase):
    SLUGS = purchase_fixture.MobilePurchasesIntegrationTests.SLUGS
    snapshot = purchase_fixture.MobilePurchasesIntegrationTests.snapshot

    @classmethod
    def setUpTestData(cls):
        purchase_fixture.MobilePurchasesIntegrationTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in (
            "facturacion.views.enviar_documento_whatsapp",
            "facturacion.views.subir_documento_whatsapp",
            "django.core.mail.EmailMessage.send",
            "facturacion.views.HTML",
        ):
            patcher = patch(target)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mock.assert_not_called)
        self.set_permissions(puede_crear_compras=True)

    def set_permissions(self, granular=True, companies=None, **flags):
        role = RolSistema.objects.create(
            nombre="Registro compras móvil QA", codigo=f"mpc-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags,
        )
        for company in companies or self.empresas.values():
            UsuarioEmpresaPermiso.objects.update_or_create(
                usuario=self.usuario, empresa=company, defaults={"rol_sistema": role},
            )
        return role

    def url(self, empresa=None, mobile=True):
        company = empresa or self.empresa
        return reverse("crear_compra", args=[company.slug]) + ("?app=1" if mobile else "")

    def payload(self, empresa=None, **changes):
        company = empresa or self.empresa
        data = {
            "app": "1", "proveedor": str(self.suppliers[company.slug].pk),
            "proveedor_nombre": "", "referencia_documento": f"APP-{company.slug}",
            "fecha_documento": "2026-11-02", "condicion_pago": "credito",
            "metodo_pago": "efectivo", "cuenta_financiera_pago": "", "dias_credito": "15",
            "fecha_vencimiento": "", "observacion": "Compra desde la app QA",
            "estado": "borrador", "lineas_compra-TOTAL_FORMS": "1",
            "lineas_compra-INITIAL_FORMS": "0", "lineas_compra-MIN_NUM_FORMS": "0",
            "lineas_compra-MAX_NUM_FORMS": "1000",
            "lineas_compra-0-producto": str(self.products[company.slug].pk),
            "lineas_compra-0-cantidad": "2.50", "lineas_compra-0-costo_unitario": "100.0000",
            "lineas_compra-0-descuento_porcentaje": "10",
            "lineas_compra-0-impuesto": str(self.tax.pk),
            "lineas_compra-0-comentario": "Comentario conservado",
        }
        data.update(changes)
        return data

    def direct(self, method="GET", target=None, data=None, user=None):
        target = target or self.url()
        request = RequestFactory().post(target, data or {}) if method == "POST" else RequestFactory().generic(method, target)
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(target).path)
        return match.func(request, **match.kwargs)

    def assert_denied(self, callback):
        try:
            response = callback()
        except (PermissionDenied, Http404):
            return
        self.assertIn(response.status_code, (403, 404, 405))

    def invoice_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (
            CAI, Factura, NotaCredito, PagoFactura, ReciboPago, CuentaAcumuladoCompra,
        ))

    def test_create_form_four_companies_uses_native_dates_scoped_catalogs_and_no_table(self):
        before = self.snapshot()
        for company in self.empresas.values():
            response = self.client.get(self.url(company))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_purchase_form.html")
            self.assertTemplateNotUsed(response, "facturacion/crear_compra_premium.html")
            self.assertNotContains(response, "<table")
            self.assertIn("no-store", response["Cache-Control"])
            form, formset = response.context["form"], response.context["formset"]
            self.assertEqual(set(form.fields["proveedor"].queryset.values_list("empresa_id", flat=True)), {company.pk})
            self.assertEqual(set(form.fields["cuenta_financiera_pago"].queryset.values_list("empresa_id", flat=True)), {company.pk})
            for line in [*formset.forms, response.context["empty_line_form"]]:
                self.assertEqual(set(line.fields["producto"].queryset.values_list("empresa_id", flat=True)), {company.pk})
            self.assertEqual(form["estado"].value(), "borrador")
            self.assertNotIn("aplicada", dict(form.fields["estado"].choices))
            inputs = {attrs.get("name"): attrs for tag, attrs in _PurchaseMarkup(response.content.decode()).nodes if tag == "input"}
            self.assertEqual(inputs["fecha_documento"]["type"], "date")
            self.assertEqual(inputs["fecha_vencimiento"]["type"], "date")
        self.assertEqual(self.snapshot(), before)

    def test_get_and_head_do_not_initialize_accounts_catalogue_or_write(self):
        before = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalog:
            with CaptureQueriesContext(connection) as captured:
                for company in self.empresas.values():
                    for method in ("get", "head"):
                        result = getattr(self.client, method)(self.url(company))
                        self.assertEqual(result.status_code, 200)
                        if method == "head":
                            self.assertEqual(result.content, b"")
            accounts.assert_not_called()
            catalog.assert_not_called()
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for query in captured))
        self.assertEqual(self.snapshot(), before)

    def test_create_only_roles_legacy_and_granular_can_create_without_consult_permission(self):
        for granular in (True, False):
            self.set_permissions(granular=granular, puede_crear_compras=True)
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200)
            result = self.client.post(self.url(), self.payload(referencia_documento=f"CREATEONLY-{granular}"))
            self.assertEqual(result.status_code, 302)
            self.assertIn("app", result["Location"])
            self.assertNotIn("/compras/", result["Location"])
            self.assertTrue(CompraInventario.objects.filter(empresa=self.empresa, referencia_documento=f"CREATEONLY-{granular}").exists())

    def test_read_only_and_unrelated_permissions_cannot_access_or_post_directly(self):
        for flags in ({}, {"puede_compras": True}, {"puede_productos": True}, {"puede_aplicar_compras": True}, {"puede_crear_compras": True, "solo_lectura_inventario": True}):
            self.set_permissions(**flags)
            before = self.snapshot()
            for method in ("GET", "POST"):
                self.assert_denied(lambda: self.direct(method, data=self.payload()))
            self.assertEqual(self.snapshot(), before)

    def test_permissions_are_company_specific(self):
        self.set_permissions(companies=[self.empresas["medical_spa"]])
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assert_denied(lambda: self.direct(target=self.url(self.empresas["medical_spa"])))
        self.assertEqual(self.client.get(self.url(self.empresas["luque_aestetic"])).status_code, 200)

    def test_inactive_company_license_module_membership_and_anonymous_are_guarded(self):
        for values in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values)
            self.assert_denied(lambda: self.direct())
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=False)
        self.assert_denied(lambda: self.direct())
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=True)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.assert_denied(lambda: self.direct(target=self.url(self.empresas["medical_spa"])))
        result = self.direct(user=AnonymousUser())
        self.assertEqual(result.status_code, 302)

    def test_post_four_companies_draft_reuses_original_totals_no_stock_payment_or_invoice_change(self):
        protected = self.invoice_snapshot()
        stock = tuple(list(model.objects.order_by("pk").values()) for model in (MovimientoInventario, MovimientoLoteBodega, InventarioProducto, PagoCompra))
        for company in self.empresas.values():
            result = self.client.post(self.url(company), self.payload(company))
            self.assertEqual(result.status_code, 302)
            purchase = CompraInventario.objects.get(empresa=company, referencia_documento=f"APP-{company.slug}")
            self.assertEqual((purchase.empresa_id, purchase.estado, purchase.proveedor_nombre), (company.pk, "borrador", self.suppliers[company.slug].nombre))
            self.assertEqual(purchase.fecha_documento, date(2026, 11, 2))
            self.assertEqual(purchase.fecha_vencimiento, date(2026, 11, 17))
            self.assertEqual(purchase.total_documento, Decimal("258.75"))
            line = purchase.lineas.get()
            self.assertEqual((line.cantidad, line.descuento_monto, line.subtotal, line.impuesto_monto), (Decimal("2.50"), Decimal("25.00"), Decimal("225.00"), Decimal("33.75")))
            self.assertEqual(line.comentario, "Comentario conservado")
            audit = RegistroAuditoria.objects.get(
                modelo="comprainventario", objeto_id=str(purchase.pk), accion="crear",
            )
            self.assertEqual((audit.empresa_id, audit.usuario_id, audit.metodo_http), (company.pk, self.usuario.pk, "POST"))
        self.assertEqual(self.invoice_snapshot(), protected)
        self.assertEqual(tuple(list(model.objects.order_by("pk").values()) for model in (MovimientoInventario, MovimientoLoteBodega, InventarioProducto, PagoCompra)), stock)

    def test_consult_permission_returns_to_same_company_mobile_detail(self):
        self.set_permissions(puede_crear_compras=True, puede_compras=True)
        result = self.client.post(self.url(), self.payload())
        purchase = CompraInventario.objects.get(referencia_documento=f"APP-{self.empresa.slug}")
        self.assertEqual(result["Location"], reverse("ver_compra", args=[self.empresa.slug, purchase.pk])+"?app=1")

    def test_foreign_product_supplier_and_financial_account_never_save(self):
        other = self.empresas["medical_spa"]
        for key, value in (
            ("lineas_compra-0-producto", self.products[other.slug].pk),
            ("proveedor", self.suppliers[other.slug].pk),
            ("cuenta_financiera_pago", self.accounts[other.slug].pk),
        ):
            before = self.snapshot()
            response = self.client.post(self.url(), self.payload(**{key: str(value)}))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_purchase_form.html")
            self.assertEqual(self.snapshot(), before)

    def test_invalid_second_line_never_saves_partial_purchase_and_preserves_values(self):
        data = self.payload(**{
            "lineas_compra-TOTAL_FORMS": "2", "lineas_compra-1-producto": str(self.products[self.empresa.slug].pk),
            "lineas_compra-1-cantidad": "-1", "lineas_compra-1-costo_unitario": "50",
            "lineas_compra-1-descuento_porcentaje": "0", "lineas_compra-1-impuesto": "",
            "lineas_compra-1-comentario": "Mantener este dato",
        })
        before = self.snapshot()
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(response.context["form"]["referencia_documento"].value(), data["referencia_documento"])
        self.assertEqual(response.context["formset"].forms[0]["cantidad"].value(), "2.50")
        self.assertEqual(response.context["formset"].forms[1]["comentario"].value(), "Mantener este dato")
        self.assertTrue(response.context["formset"].forms[1].errors)

    def test_zero_negative_quantity_and_invalid_discount_are_rejected(self):
        for key, value in (("lineas_compra-0-cantidad", "0"), ("lineas_compra-0-costo_unitario", "-0.01"), ("lineas_compra-0-descuento_porcentaje", "101")):
            before = self.snapshot()
            response = self.client.post(self.url(), self.payload(**{key: value}))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.snapshot(), before)

    def test_formset_management_rejects_missing_huge_negative_existing_or_duplicate_fields(self):
        for change in (
            {"lineas_compra-TOTAL_FORMS": ""}, {"lineas_compra-TOTAL_FORMS": "101"},
            {"lineas_compra-TOTAL_FORMS": "-1"}, {"lineas_compra-TOTAL_FORMS": "nan"},
            {"lineas_compra-INITIAL_FORMS": "1"}, {"lineas_compra-INITIAL_FORMS": ""},
        ):
            before = self.snapshot()
            self.assert_denied(lambda: self.direct("POST", data=self.payload(**change)))
            self.assertEqual(self.snapshot(), before)

    def test_unknown_duplicate_app_queries_and_non_post_methods_are_rejected(self):
        path = reverse("crear_compra", args=[self.empresa.slug])
        for query in ("app=1&app=1", "app=0", "app=1&empresa=medical_spa", "app=1&next=https://example.org"):
            self.assert_denied(lambda: self.direct(target=path+"?"+query))
        for method in ("PUT", "PATCH", "DELETE"):
            self.assert_denied(lambda: self.direct(method))

    def test_upload_is_not_accepted_by_mobile_purchase_form(self):
        data = self.payload(archivo=SimpleUploadedFile("unused.txt", b"QA"))
        before = self.snapshot()
        self.assert_denied(lambda: self.direct("POST", data=data))
        self.assertEqual(self.snapshot(), before)

    def test_existing_line_purchase_ids_unknown_or_duplicate_body_fields_are_rejected(self):
        for change in (
            {"lineas_compra-0-id": str(self.lines[self.empresa.slug].pk)},
            {"lineas_compra-0-compra": str(self.purchase.pk)},
            {"empresa": str(self.empresas["medical_spa"].pk)},
            {"app": ["1", "1"]},
            {"lineas_compra-TOTAL_FORMS": ["1", "1"]},
        ):
            before = self.snapshot()
            data = self.payload()
            data.update(change)
            self.assert_denied(lambda: self.direct("POST", data=data))
            self.assertEqual(self.snapshot(), before)

    def test_empty_deleted_lines_or_invalid_document_never_save(self):
        for change in (
            {"lineas_compra-0-DELETE": "on"},
            {"fecha_documento": "fecha imposible"},
            {"proveedor": "", "proveedor_nombre": ""},
        ):
            before = self.snapshot()
            response = self.client.post(self.url(), self.payload(**change))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_purchase_form.html")
            self.assertEqual(self.snapshot(), before)

    def test_csrf_is_still_required_for_mobile_purchase_registration(self):
        from django.test import Client
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        response = protected.post(self.url(), self.payload())
        # The existing ERP CSRF failure handler redirects back to company login.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(self.snapshot(), before)
        form = protected.get(self.url())
        self.assertEqual(form.status_code, 200)
        token = protected.cookies["csrftoken"].value
        valid = self.payload(csrfmiddlewaretoken=token)
        accepted = protected.post(self.url(), valid)
        self.assertEqual(accepted.status_code, 302)
        self.assertNotEqual(accepted["Location"], response["Location"])
        self.assertTrue(CompraInventario.objects.filter(empresa=self.empresa, referencia_documento=f"APP-{self.empresa.slug}").exists())

    def test_own_supplier_with_foreign_account_relation_is_rejected_before_saving(self):
        supplier = self.suppliers[self.empresa.slug]
        foreign_account = self.accounts["medical_spa"].cuenta_contable_id
        Proveedor.objects.filter(pk=supplier.pk).update(cuenta_contable_id=foreign_account)
        before = self.snapshot()
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_applied_state_requires_apply_permission_before_any_saver(self):
        before = self.snapshot()
        with patch.object(views, "_aplicar_compra_documento") as apply:
            self.assert_denied(lambda: self.direct("POST", data=self.payload(estado="aplicada")))
            apply.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_applied_credit_purchase_calls_original_stock_service_and_keeps_invoice_untouched(self):
        self.set_permissions(puede_crear_compras=True, puede_aplicar_compras=True, puede_compras=True)
        protected = self.invoice_snapshot()
        with patch.object(views, "_aplicar_compra_documento", wraps=views._aplicar_compra_documento) as apply, patch.object(views, "_registrar_asiento_compra_aplicada_seguro", return_value=None) as accounting:
            for company in self.empresas.values():
                result = self.client.post(self.url(company), self.payload(company, estado="aplicada"))
                self.assertEqual(result.status_code, 302)
                purchase = CompraInventario.objects.get(empresa=company, referencia_documento=f"APP-{company.slug}")
                self.assertEqual(purchase.estado, "aplicada")
                movement = MovimientoInventario.objects.get(compra_documento=purchase)
                self.assertEqual((movement.empresa_id, movement.producto_id, movement.cantidad), (company.pk, self.products[company.slug].pk, Decimal("2.50")))
                self.assertFalse(PagoCompra.objects.filter(compra=purchase).exists())
            self.assertEqual(apply.call_count, 4)
            self.assertEqual(accounting.call_count, 4)
        self.assertEqual(self.invoice_snapshot(), protected)

    def test_stock_validation_failure_rolls_back_purchase_and_all_lines(self):
        from django.core.exceptions import ValidationError
        self.set_permissions(puede_crear_compras=True, puede_aplicar_compras=True)
        before = self.snapshot()
        with patch.object(views, "_aplicar_compra_documento", side_effect=ValidationError("Periodo contable cerrado QA")):
            result = self.client.post(self.url(), self.payload(estado="aplicada"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Periodo contable cerrado QA")
        self.assertEqual(self.snapshot(), before)

    def test_applied_cash_purchase_reuses_original_automatic_payment(self):
        self.set_permissions(puede_crear_compras=True, puede_aplicar_compras=True)
        protected = self.invoice_snapshot()
        with patch.object(views, "_registrar_asiento_compra_aplicada_seguro", return_value=None), patch.object(views, "registrar_asiento_pago_proveedor", return_value=None), patch.object(views, "_registrar_pago_contado_compra_si_aplica", wraps=views._registrar_pago_contado_compra_si_aplica) as auto_payment:
            response = self.client.post(self.url(), self.payload(
                estado="aplicada", condicion_pago="contado",
                cuenta_financiera_pago=str(self.accounts[self.empresa.slug].pk),
            ))
        self.assertEqual(response.status_code, 302)
        purchase = CompraInventario.objects.get(empresa=self.empresa, referencia_documento=f"APP-{self.empresa.slug}")
        self.assertEqual(purchase.estado, "aplicada")
        payment = PagoCompra.objects.get(compra=purchase)
        self.assertEqual((payment.monto, payment.cuenta_financiera_id), (Decimal("258.75"), self.accounts[self.empresa.slug].pk))
        self.assertEqual((payment.compra.empresa_id, payment.metodo), (self.empresa.pk, "efectivo"))
        self.assertEqual(purchase.fecha_vencimiento, purchase.fecha_documento)
        auto_payment.assert_called_once()
        self.assertEqual(self.invoice_snapshot(), protected)

    def test_create_only_user_has_available_company_scoped_purchase_access_in_mobile_hub(self):
        for company in self.empresas.values():
            response = self.client.get(reverse("agenda_mobile_modules_screen", args=[company.slug]))
            self.assertEqual(response.status_code, 200)
            module = next(item for item in response.context["modulos_app"] if item["key"] == "compras")
            self.assertEqual((module["estado"], module["url"]), ("disponible", self.url(company)))

    def test_original_web_keeps_its_own_template_and_helper(self):
        self.set_permissions(puede_crear_compras=True)
        with patch.object(views, "_cuentas_financieras_activas_para_pago", wraps=views._cuentas_financieras_activas_para_pago) as helper, patch.object(views, "asegurar_cuentas_financieras_base_honduras"):
            result = self.client.get(self.url(mobile=False))
        self.assertEqual(result.status_code, 200)
        self.assertTemplateUsed(result, "facturacion/crear_compra_premium.html")
        self.assertTemplateNotUsed(result, "facturacion/mobile_purchase_form.html")
        helper.assert_called_once()

    def test_list_register_button_requires_create_and_other_financial_actions_stay_hidden(self):
        url = reverse("compras_dashboard", args=[self.empresa.slug])+"?app=1"
        self.set_permissions(puede_compras=True)
        readonly = self.client.get(url)
        self.assertNotContains(readonly, self.url())
        self.set_permissions(puede_compras=True, puede_crear_compras=True)
        allowed = self.client.get(url)
        self.assertContains(allowed, self.url())
        for action in ("edit", "apply", "void", "delete", "pay"):
            self.assertFalse(allowed.context[f"can_{action}_purchase"])
