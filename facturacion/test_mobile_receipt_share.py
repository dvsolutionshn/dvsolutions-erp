"""Sharing UI consumes only the existing authenticated receipt PDF.

These integration checks never invoke the browser share sheet or native PDF
engine. Payment/document fixtures are synthetic and all transports are blocked.
"""

from datetime import datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.http import Http404
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Usuario
from contabilidad.models import CuentaContable, CuentaFinanciera
from . import test_mobile_receipts as receipt_fixtures
from . import views
from .models import CAI, Cliente, ConfiguracionFacturacionEmpresa, Factura, PagoFactura, ReciboPago


class ReceiptShareFixture:
    """Plain fixture API for isolated renderer QA; no inherited test methods."""

    SLUGS = receipt_fixtures.MobileReceiptsIntegrationTests.SLUGS
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)

    @classmethod
    def seed(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            receipt_fixtures.MobileReceiptsIntegrationTests.setUpTestData.__func__(cls)


class _ReceiptMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def by_id(self, value):
        return [(tag, attrs) for tag, attrs in self.nodes if attrs.get("id") == value]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileReceiptShareIntegrationTests(TestCase):
    SLUGS = ReceiptShareFixture.SLUGS
    PDF = b"%PDF-1.4\nSynthetic original receipt bytes for sharing\n%%EOF"
    NOW = ReceiptShareFixture.NOW
    SHARE_SCRIPT = "/static/facturacion/mobile_receipt_share.js"
    SHARE_IDS = ("mobileReceiptShare", "prepareReceiptShare", "shareReceiptFile", "receiptShareStatus")

    # Reuse only fixture/utility methods, not its TestCase or any test methods.
    url = receipt_fixtures.MobileReceiptsIntegrationTests.url
    asignar_rol = receipt_fixtures.MobileReceiptsIntegrationTests.asignar_rol
    directo = receipt_fixtures.MobileReceiptsIntegrationTests.directo
    datos = receipt_fixtures.MobileReceiptsIntegrationTests.datos

    @classmethod
    def setUpTestData(cls):
        ReceiptShareFixture.seed.__func__(cls)

    def setUp(self):
        self.blocked = []
        # Install clocks/transports before login and URL resolution callbacks.
        for target in (
            "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
            "django.core.mail.EmailMessage.send", "django.core.mail.EmailMultiAlternatives.send",
            "facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp",
        ):
            patcher = patch(target, side_effect=AssertionError("No transport is permitted by receipt sharing tests."))
            mocked = patcher.start()
            self.blocked.append(mocked)
            self.addCleanup(patcher.stop)
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)
        pdf = patch.object(views, "HTML")
        self.html = pdf.start()
        self.html.return_value.write_pdf.return_value = self.PDF
        self.addCleanup(pdf.stop)
        self.client.force_login(self.usuario)
        # Resolve every canonical route before mutation baselines/SQL capture.
        for empresa in self.empresas.values():
            for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                self.url(view, empresa)
        self.addCleanup(self.assert_transports_unused)

    def assert_transports_unused(self):
        for mocked in self.blocked:
            mocked.assert_not_called()

    def all_models_snapshot(self):
        return {
            model._meta.label_lower: list(model._base_manager.order_by("pk").values())
            for model in sorted(apps.get_models(include_auto_created=True), key=lambda model: model._meta.label_lower)
        }

    def query_without_writes(self, method, url, **kwargs):
        before = self.all_models_snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as captured:
            response = getattr(self.client, method)(url, **kwargs)
        writes = [item["sql"] for item in captured if re.match(r"\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", item["sql"], re.I)]
        self.assertEqual(writes, [])
        self.assertEqual(self.all_models_snapshot(), before)
        return response

    def direct_denial_without_writes(self, view="ver_recibo", **kwargs):
        before = self.all_models_snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(view, **kwargs)
        writes = [item["sql"] for item in captured if re.match(r"\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", item["sql"], re.I)]
        self.assertEqual(writes, [])
        self.assertEqual(self.all_models_snapshot(), before)
        return captured.captured_queries

    def share_markup(self, response):
        self.assertEqual(response.status_code, 200)
        parser = _ReceiptMarkup(response.content.decode())
        panels = parser.by_id("mobileReceiptShare")
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0][0], "section")
        self.assertEqual(panels[0][1]["data-pdf-url"], response.context["app_receipt_pdf_url"])
        self.assertEqual(
            [attrs for tag, attrs in parser.nodes if tag == "script" and attrs.get("src") == self.SHARE_SCRIPT],
            [{"src": self.SHARE_SCRIPT, "defer": None}],
        )
        return parser, panels[0][1]["data-pdf-url"]

    def assert_no_share_ui(self, response):
        self.assertEqual(response.status_code, 200)
        parser = _ReceiptMarkup(response.content.decode())
        for value in self.SHARE_IDS:
            self.assertEqual(parser.by_id(value), [])
        self.assertFalse(any(tag == "script" and attrs.get("src") == self.SHARE_SCRIPT for tag, attrs in parser.nodes))

    def test_four_companies_detail_has_only_original_protected_receipt_pdf_source(self):
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                response = self.query_without_writes("get", self.url(empresa=empresa))
                self.assertTemplateUsed(response, "facturacion/mobile_receipt_detail.html")
                parser, pdf_url = self.share_markup(response)
                self.assertEqual(pdf_url, self.url("descargar_recibo_pdf", empresa))
                self.assertEqual(urlsplit(pdf_url).query, "app=1")
                self.assertEqual(urlsplit(pdf_url).netloc, "")
                self.assertEqual(resolve(urlsplit(pdf_url).path).func, views.descargar_recibo_pdf)
                self.assertEqual(sum(tag == "a" and attrs.get("href") == pdf_url for tag, attrs in parser.nodes), 2)
                self.assertFalse(any(tag == "form" and attrs.get("method", "get").lower() == "post" for tag, attrs in parser.nodes))
        self.html.assert_not_called()

    def test_both_share_buttons_are_disabled_native_buttons_and_status_is_accessible(self):
        parser, _ = self.share_markup(self.query_without_writes("get", self.url()))
        for value in ("prepareReceiptShare", "shareReceiptFile"):
            self.assertEqual(len(parser.by_id(value)), 1)
            tag, attrs = parser.by_id(value)[0]
            self.assertEqual(tag, "button")
            self.assertEqual(attrs.get("type"), "button")
            self.assertIn("disabled", attrs)
            self.assertNotIn("name", attrs)
            self.assertNotIn("formaction", attrs)
            self.assertEqual(attrs.get("aria-describedby"), "receiptShareStatus")
        _, status = parser.by_id("receiptShareStatus")[0]
        self.assertEqual((status.get("role"), status.get("aria-live"), status.get("aria-atomic")), ("status", "polite", "true"))
        self.assertTrue(any(tag == "noscript" for tag, _ in parser.nodes))

    def test_list_editor_and_all_original_web_views_never_include_share_controls_or_asset(self):
        for empresa in self.empresas.values():
            for view, mobile in (("recibos_dashboard", True), ("editar_recibo", True), ("recibos_dashboard", False), ("ver_recibo", False), ("editar_recibo", False)):
                with self.subTest(empresa=empresa.slug, view=view, mobile=mobile):
                    self.assert_no_share_ui(self.query_without_writes("get", self.url(view, empresa, mobile=mobile)))
        self.html.assert_not_called()

    def test_detail_does_not_generate_pdf_prefetch_or_share_recipient_metadata_in_html(self):
        response = self.query_without_writes("get", self.url())
        parser, _ = self.share_markup(response)
        panel = parser.by_id("mobileReceiptShare")[0][1]
        self.assertEqual({key for key in panel if key.startswith("data-")}, {"data-pdf-url"})
        self.assertFalse(any(tag in {"iframe", "object", "embed"} for tag, _ in parser.nodes))
        self.assertFalse(any(tag == "link" and attrs.get("rel") in {"preload", "prefetch", "prerender"} for tag, attrs in parser.nodes))
        self.assertFalse(any(attrs.get("name") in {"telefono", "correo", "destinatario", "token", "recipient"} for _, attrs in parser.nodes))
        self.assertContains(response, "no un enlace de acceso al sistema")
        self.assertContains(response, "ni confirma su entrega")
        self.html.assert_not_called()

    def test_template_does_not_offer_share_or_load_asset_when_original_pdf_url_is_absent(self):
        from . import mobile_receipts

        original_context = mobile_receipts._contexto_recibos_app

        def no_pdf_context(*args, **kwargs):
            values = original_context(*args, **kwargs)
            values["app_receipt_pdf_url"] = ""
            return values

        with patch.object(mobile_receipts, "_contexto_recibos_app", side_effect=no_pdf_context):
            self.assert_no_share_ui(self.query_without_writes("get", self.url()))
        self.html.assert_not_called()

    def test_receipt_permission_alone_in_both_role_types_keeps_share_without_invoice_or_payment_grant(self):
        for empresa in self.empresas.values():
            for granular in (True, False):
                with self.subTest(empresa=empresa.slug, granular=granular):
                    self.asignar_rol(empresa, granular=granular, puede_recibos=True)
                    response = self.query_without_writes("get", self.url(empresa=empresa))
                    self.share_markup(response)
                    self.assertFalse(response.context["can_view_invoice"])
                    self.assertFalse(response.context["app_home_disponible"])
                    self.assertNotContains(response, 'rel="manifest"')
                    self.assertNotContains(response, reverse("ver_factura", args=[empresa.slug, self.facturas[empresa.slug].pk])+"?app=1")
                    pdf = self.query_without_writes("get", response.context["app_receipt_pdf_url"])
                    self.assertEqual(pdf.content, self.PDF)

    def test_invoice_or_payment_permissions_do_not_allow_receipt_share_or_pdf_without_receipts(self):
        for granular in (True, False):
            for permissions in ({"puede_ver_facturas": True}, {"puede_registrar_pagos_clientes": True}):
                self.asignar_rol(granular=granular, **permissions)
                for view in ("ver_recibo", "descargar_recibo_pdf"):
                    queries = self.direct_denial_without_writes(view)
                    self.assertFalse(any(ReciboPago._meta.db_table in item["sql"] for item in queries if item["sql"].lstrip().upper().startswith("SELECT")))
        self.html.assert_not_called()

    def test_revoked_permission_after_opening_detail_rechecks_original_pdf_authorization(self):
        response = self.query_without_writes("get", self.url())
        _, pdf_url = self.share_markup(response)
        self.asignar_rol(puede_ver_facturas=True, puede_registrar_pagos_clientes=True)
        self.direct_denial_without_writes("descargar_recibo_pdf", url=pdf_url)
        self.html.assert_not_called()

    def test_membership_is_rechecked_for_detail_and_file_in_each_nonprimary_company(self):
        for empresa in self.empresas.values():
            if empresa.pk == self.usuario.empresa_id:
                continue
            self.usuario.empresas_acceso.remove(empresa)
            try:
                for view in ("ver_recibo", "descargar_recibo_pdf"):
                    self.direct_denial_without_writes(view, empresa=empresa)
            finally:
                self.usuario.empresas_acceso.add(empresa)
        self.html.assert_not_called()

    def test_original_active_license_and_facturation_module_gates_are_not_bypassed(self):
        for empresa in self.empresas.values():
            for invalid in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
                Empresa.objects.filter(pk=empresa.pk).update(**invalid)
                try:
                    for view in ("ver_recibo", "descargar_recibo_pdf"):
                        self.direct_denial_without_writes(view, empresa=empresa)
                finally:
                    Empresa.objects.filter(pk=empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
            EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=False)
            try:
                for view in ("ver_recibo", "descargar_recibo_pdf"):
                    self.direct_denial_without_writes(view, empresa=empresa)
            finally:
                EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=True)
        self.html.assert_not_called()

    def test_original_superuser_license_exception_remains_but_does_not_bypass_module_scope(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        response = self.query_without_writes("get", self.url())
        _, pdf_url = self.share_markup(response)
        self.assertEqual(self.query_without_writes("get", pdf_url).content, self.PDF)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.direct_denial_without_writes("descargar_recibo_pdf")

    def test_nonclinical_or_out_of_four_scope_preserves_original_web_without_share_feature(self):
        for update in ({"tipo_solucion": "erp"}, {"slug": "receipt-share-outside"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**update)
            self.empresa.refresh_from_db()
            self.assert_no_share_ui(self.query_without_writes("get", self.url()))
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica", slug="hospital_mia")
            self.empresa.refresh_from_db()
        self.html.assert_not_called()

    def test_other_company_receipt_and_shared_customer_copy_do_not_make_file_public(self):
        other = self.empresas["medical_spa"]
        foreign_receipt = self.recibos[(other.slug, 1)]
        for view in ("ver_recibo", "descargar_recibo_pdf"):
            self.direct_denial_without_writes(view, recibo=foreign_receipt)
        original = self.recibo.cliente_id
        ReciboPago.objects.filter(pk=self.recibo.pk).update(cliente_id=self.clientes[other.slug].pk)
        try:
            self.assertEqual(self.clientes[other.slug].perfil_compartido_id, self.clientes[self.empresa.slug].perfil_compartido_id)
            for view in ("ver_recibo", "descargar_recibo_pdf"):
                self.direct_denial_without_writes(view)
        finally:
            ReciboPago.objects.filter(pk=self.recibo.pk).update(cliente_id=original)
        self.html.assert_not_called()

    def test_imported_factura_cliente_or_pago_mismatch_is_denied_before_pdf_engine(self):
        other = self.empresas["medical_spa"]
        wrong_invoice = self.facturas[other.slug]
        # A synthetic imported payment without an automatically linked receipt:
        # reusing an existing payment would hit the receipt OneToOne constraint
        # before the intended company-isolation guard can be exercised.
        foreign_payment = PagoFactura(factura=wrong_invoice, monto=1)
        PagoFactura.objects.bulk_create([foreign_payment])
        own_other_payment = self.pagos[(self.empresa.slug, 0)]
        for changes in ({"factura_id": wrong_invoice.pk}, {"pago_id": foreign_payment.pk}):
            old = {field: getattr(self.recibo, field) for field in changes}
            ReciboPago.objects.filter(pk=self.recibo.pk).update(**changes)
            try:
                for view in ("ver_recibo", "descargar_recibo_pdf"):
                    self.direct_denial_without_writes(view)
            finally:
                ReciboPago.objects.filter(pk=self.recibo.pk).update(**old)
        # A locally owned customer still must be the invoice's exact customer.
        new_customer = Cliente.objects.create(empresa=self.empresa, nombre="Otro cliente local documental", rtn="RS-QA-31-LOCAL")
        Factura.objects.filter(pk=self.recibo.factura_id).update(cliente_id=new_customer.pk)
        try:
            for view in ("ver_recibo", "descargar_recibo_pdf"):
                self.direct_denial_without_writes(view)
        finally:
            Factura.objects.filter(pk=self.recibo.factura_id).update(cliente_id=self.recibo.cliente_id)
        self.assertEqual(own_other_payment.factura_id, self.recibo.factura_id)
        self.html.assert_not_called()

    def test_orphan_receipt_relations_and_payment_account_company_cannot_reach_file(self):
        for field in ("factura_id", "cliente_id", "pago_id"):
            old = getattr(self.recibo, field)
            with connection.constraint_checks_disabled():
                ReciboPago.objects.filter(pk=self.recibo.pk).update(**{field: 2147483647})
                try:
                    self.direct_denial_without_writes("descargar_recibo_pdf")
                finally:
                    ReciboPago.objects.filter(pk=self.recibo.pk).update(**{field: old})
        other_account = self.cuentas["medical_spa"]
        original = PagoFactura.objects.get(pk=self.recibo.pago_id).cuenta_financiera_id
        PagoFactura.objects.filter(pk=self.recibo.pago_id).update(cuenta_financiera_id=other_account.pk)
        try:
            for view in ("ver_recibo", "descargar_recibo_pdf"):
                self.direct_denial_without_writes(view)
        finally:
            PagoFactura.objects.filter(pk=self.recibo.pago_id).update(cuenta_financiera_id=original)
        self.html.assert_not_called()

    def test_historical_inactive_local_customers_accounts_and_multicompany_cashier_remain_valid(self):
        for empresa in self.empresas.values():
            Cliente.objects.filter(pk=self.clientes[empresa.slug].pk).update(activo=False)
            CuentaFinanciera.objects.filter(pk=self.cuentas[empresa.slug].pk).update(activa=False)
            response = self.query_without_writes("get", self.url(empresa=empresa))
            _, pdf_url = self.share_markup(response)
            self.assertEqual(self.query_without_writes("get", pdf_url).content, self.PDF)
            self.assertEqual(self.recibos[(empresa.slug, 1)].pago.cajero_id, self.usuario.pk)

    def test_pdf_keeps_original_template_bytes_filename_disposition_and_cache_in_all_four(self):
        original_render = views.render_to_string
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                with patch.object(views, "render_to_string", wraps=original_render) as renderer:
                    app = self.query_without_writes("get", self.url("descargar_recibo_pdf", empresa))
                    app_html = self.html.call_args.kwargs["string"]
                    self.assertEqual(renderer.call_args.args[0], "facturacion/recibo_pdf.html")
                    self.assertEqual(set(renderer.call_args.args[1]), {"empresa", "recibo", "logo_url"})
                    self.assertEqual(renderer.call_args.args[1]["recibo"].empresa_id, empresa.pk)
                    web = self.query_without_writes("get", self.url("descargar_recibo_pdf", empresa, mobile=False))
                    web_html = self.html.call_args.kwargs["string"]
                self.assertEqual(app.content, self.PDF)
                self.assertEqual(app.content, web.content)
                self.assertEqual(app_html, web_html)
                self.assertEqual(app["Content-Type"], "application/pdf")
                self.assertEqual(app["Content-Disposition"], web["Content-Disposition"])
                self.assertTrue(app["Content-Disposition"].startswith('attachment; filename="Recibo_'))
                self.assertIn("no-store", app["Cache-Control"])
                self.assertEqual((app["Pragma"], app["Expires"]), ("no-cache", "0"))

    def test_head_consults_same_protected_documents_without_file_body_or_writes(self):
        for empresa in self.empresas.values():
            for view in ("ver_recibo", "descargar_recibo_pdf"):
                response = self.query_without_writes("head", self.url(view, empresa))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertIn("no-store", response["Cache-Control"])
                if view == "descargar_recibo_pdf":
                    self.assertEqual(response["Content-Type"], "application/pdf")
                    self.assertIn("attachment;", response["Content-Disposition"])

    def test_missing_accounts_cai_and_configurations_are_not_initialized_for_detail_or_pdf(self):
        PagoFactura.objects.all().update(cuenta_financiera=None, cuenta_financiera_impuesto=None)
        CuentaFinanciera.objects.all().delete()
        CuentaContable.objects.all().delete()
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        ConfiguracionFacturacionEmpresa.objects.all().delete()
        self.assertFalse(CAI.objects.exists())
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalogue, patch.object(views, "asegurar_cuenta_contable_cliente") as client_account:
            for empresa in self.empresas.values():
                self.share_markup(self.query_without_writes("get", self.url(empresa=empresa)))
                self.assertEqual(self.query_without_writes("get", self.url("descargar_recibo_pdf", empresa)).content, self.PDF)
            accounts.assert_not_called()
            catalogue.assert_not_called()
            client_account.assert_not_called()
        self.assertFalse(CuentaFinanciera.objects.exists())
        self.assertFalse(CuentaContable.objects.exists())
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.exists())
        self.assertFalse(ConfiguracionFacturacionEmpresa.objects.exists())

    def test_documentary_values_and_currency_keep_original_pdf_not_payment_snapshot(self):
        ReciboPago.objects.filter(pk=self.recibo.pk).update(fecha="2026-09-17", referencia="Documento distinto del pago", concepto="Texto documental\nconservado")
        Factura.objects.filter(pk=self.recibo.factura_id).update(moneda="USD")
        response = self.query_without_writes("get", self.url())
        _, pdf_url = self.share_markup(response)
        self.assertContains(response, "USD")
        self.assertEqual(response.context["recibo"].monto, self.recibo.monto)
        self.assertNotEqual(response.context["recibo"].referencia, response.context["recibo"].pago.referencia)
        self.assertEqual(self.query_without_writes("get", pdf_url).content, self.PDF)
        rendered_html = self.html.call_args.kwargs["string"]
        self.assertIn("Documento distinto del pago", rendered_html)
        self.assertIn("Texto documental", rendered_html)
        self.assertIn("Monto del recibo: $", rendered_html)

    def test_filename_and_share_source_are_escaped_without_recipient_or_public_link(self):
        Cliente.objects.filter(pk=self.recibo.cliente_id).update(nombre='<script data-recipient="unsafe">QA & archivo</script>')
        response = self.query_without_writes("get", self.url())
        _, pdf_url = self.share_markup(response)
        self.assertNotContains(response, '<script data-recipient="unsafe">')
        self.assertContains(response, "&lt;script")
        pdf = self.query_without_writes("get", pdf_url)
        self.assertNotIn('<script', pdf["Content-Disposition"])
        self.assertNotIn('data-recipient="', pdf["Content-Disposition"])
        self.assertTrue(pdf["Content-Disposition"].endswith('.pdf"'))

    def test_original_pdf_rejects_noncanonical_ids_extra_share_metadata_and_readonly_methods(self):
        route = self.url("descargar_recibo_pdf")
        for value in ("0", "01", "9223372036854775808", "999999999999999999999"):
            with self.subTest(identifier=value):
                self.direct_denial_without_writes("descargar_recibo_pdf", url=route.replace(f"/recibos/{self.recibo.pk}/", f"/recibos/{value}/"))
        for query in ("app=1&app=1", "app=1&recipient=1", "app=1&public=1", "app=1&token=synthetic", "app=1&email=qa.invalid"):
            self.direct_denial_without_writes("descargar_recibo_pdf", url=route.split("?")[0]+"?"+query)
        for method in ("post", "put", "delete"):
            self.direct_denial_without_writes("descargar_recibo_pdf", method=method)
        self.html.assert_not_called()

    def test_logout_after_opening_detail_does_not_publish_pdf_or_keep_authorization(self):
        response = self.query_without_writes("get", self.url())
        _, pdf_url = self.share_markup(response)
        self.client.logout()
        result = self.query_without_writes("get", pdf_url)
        self.assertEqual(result.status_code, 302)
        self.assertNotEqual(result.get("Content-Type"), "application/pdf")
        self.html.assert_not_called()

    def test_no_receipt_sender_or_new_document_endpoint_is_added(self):
        from . import urls

        receipt_routes = {pattern.name: str(pattern.pattern) for pattern in urls.urlpatterns if str(pattern.pattern).startswith("recibos/")}
        self.assertEqual(receipt_routes, {
            "recibos_dashboard": "recibos/",
            "ver_recibo": "recibos/<int:recibo_id>/",
            "editar_recibo": "recibos/<int:recibo_id>/editar/",
            "descargar_recibo_pdf": "recibos/<int:recibo_id>/pdf/",
        })
