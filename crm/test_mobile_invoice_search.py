"""Read-only invoice search presentation over the original authorized APP list.

Fixtures, accounts and invoices are synthetic. The trained home retains its
existing initializers; these tests assert invoice/fiscal records and their audit
stay unchanged, not that the original home GET is globally free of writes.
"""
from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal
from html.parser import HTMLParser

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import EmpresaModulo, RegistroAuditoria, Usuario
from facturacion.models import CAI, Cliente, Factura, PagoFactura
from . import test_mobile_clinic_interface as _interface_tests


class _InvoicePanelMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nodes, self.text = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            if self.depth:
                self.depth += 1
            elif attrs.get("id") == "facturas-app":
                self.depth = 1
        if self.depth:
            self.nodes.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "section" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class MobileInvoiceSearchTests(TestCase):
    SLUGS = _interface_tests.MobileClinicInterfaceTests.SLUGS
    ROUTES = _interface_tests.MobileClinicInterfaceTests.ROUTES
    NOW = datetime(2026, 11, 23, 18, tzinfo=datetime_timezone.utc)
    url = _interface_tests.MobileClinicInterfaceTests.url
    set_role = _interface_tests.MobileClinicInterfaceTests.set_role

    @classmethod
    def setUpTestData(cls):
        _interface_tests.MobileClinicInterfaceTests.setUpTestData.__func__(cls)
        cls.invoices = {}
        for index, company in enumerate(cls.empresas.values(), 1):
            cls.invoices[company.slug] = []
            for ordinal, (name, issue_date) in enumerate((
                (f"José María González {company.slug}", date(2025, 1, 15)),
                (f"Ana <img src=x onerror=alert(1)> {company.slug}", date(2027, 2, 16)),
            ), 1):
                client = Cliente.objects.create(
                    empresa=company, nombre=name, rtn=f"QA-INVOICE-{index}-{ordinal}",
                )
                # Ordinary unnumbered draft: do not allocate or emulate a CAI.
                invoice = Factura.objects.create(
                    empresa=company, cliente=client, estado="borrador",
                    fecha_emision=issue_date, total=Decimal("123.45"),
                    total_lempiras=Decimal("123.45"),
                )
                cls.invoices[company.slug].append(invoice)

    def setUp(self):
        _interface_tests.MobileClinicInterfaceTests.setUp(self)

    def fiscal_snapshot(self):
        records = tuple(
            list(model.objects.order_by("pk").values())
            for model in (Factura, PagoFactura, CAI)
        )
        fiscal_audit = list(RegistroAuditoria.objects.filter(
            app_label="facturacion", modelo__in=("factura", "pagofactura", "cai"),
        ).order_by("pk").values())
        return records + (fiscal_audit,)

    def home(self, company):
        before = self.fiscal_snapshot()
        response = self.client.get(reverse("agenda_mobile", args=[company.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fiscal_snapshot(), before)
        return response

    def test_four_company_admin_search_preserves_all_dates_and_original_document_links(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        for company in self.empresas.values():
            with self.subTest(company=company.slug):
                page = self.home(company)
                self.assertTemplateUsed(page, "crm/mobile_invoice_search.html")
                self.assertEqual(
                    {row["id"] for row in page.context["facturas_app"]},
                    {invoice.pk for invoice in self.invoices[company.slug]},
                )
                markup = _InvoicePanelMarkup(page.content.decode())
                links = [attrs["href"] for tag, attrs in markup.nodes if tag == "a"]
                for invoice in self.invoices[company.slug]:
                    self.assertIn(reverse("descargar_factura_pdf", args=[company.slug, invoice.pk]), links)
                    self.assertIn(reverse("ver_factura", args=[company.slug, invoice.pk]) + "?app=1", links)
                    self.assertIn(invoice.cliente.nombre, "".join(markup.text))
                for other_company in self.empresas.values():
                    if other_company != company:
                        for invoice in self.invoices[other_company.slug]:
                            self.assertNotIn(invoice.cliente.nombre, "".join(markup.text))

    def test_four_company_granted_read_permission_renders_accessible_deferred_search(self):
        self.set_role(puede_ver_facturas=True)
        for company in self.empresas.values():
            with self.subTest(company=company.slug):
                page = self.home(company)
                markup = _InvoicePanelMarkup(page.content.decode())
                inputs = [attrs for tag, attrs in markup.nodes if attrs.get("id") == "mobileInvoiceSearch"]
                self.assertEqual(len(inputs), 1)
                self.assertEqual(inputs[0]["type"], "search")
                self.assertNotIn("name", inputs[0])
                self.assertTrue(any(tag == "label" and attrs.get("for") == "mobileInvoiceSearch" for tag, attrs in markup.nodes))
                scripts = [attrs for tag, attrs in markup.nodes if tag == "script"]
                self.assertEqual(len(scripts), 1)
                self.assertEqual(scripts[0]["src"], "/static/crm/mobile_invoice_search.js?v=20261123")
                self.assertIn("defer", scripts[0])
                self.assertFalse(any(tag == "form" for tag, attrs in markup.nodes))
                self.assertFalse(any(tag == "img" for tag, attrs in markup.nodes))

    def test_four_company_missing_read_permission_hides_search_list_and_invoice_payload(self):
        self.set_role(puede_ver_facturas=False)
        for company in self.empresas.values():
            with self.subTest(company=company.slug):
                page = self.home(company)
                self.assertEqual(page.context["facturas_app"], [])
                for token in ('id="facturas-app"', 'id="mobileInvoiceSearch"', "crm/mobile_invoice_search.js"):
                    self.assertNotContains(page, token)
                self.assertTemplateNotUsed(page, "crm/mobile_invoice_search.html")

    def test_disabled_billing_module_does_not_expose_search_even_to_company_admin(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        for company in self.empresas.values():
            EmpresaModulo.objects.filter(empresa=company, modulo=self.billing).update(activo=False)
            with self.subTest(company=company.slug):
                page = self.home(company)
                self.assertEqual(page.context["facturas_app"], [])
                self.assertNotContains(page, 'id="facturas-app"')
                self.assertTemplateNotUsed(page, "crm/mobile_invoice_search.html")

    def test_fifth_clinic_keeps_original_invoice_list_without_the_new_search(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        self.externa.tipo_solucion = "clinica"
        self.externa.save(update_fields=["tipo_solucion"])
        page = self.home(self.externa)
        self.assertContains(page, 'id="facturas-app"')
        self.assertFalse(page.context["busqueda_facturas_app"])
        self.assertTemplateNotUsed(page, "crm/mobile_invoice_search.html")
        self.assertNotContains(page, 'id="mobileInvoiceSearch"')
