"""Mobile receipts keep the canonical documentary edit and PDF renderer."""
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable
from crm.models import CitaCliente, ConfiguracionCRM, OpcionServicioAgenda
from clinica.models import ConfiguracionClinica, Paciente, ProfesionalSalud, RecetaMedica, TratamientoPaciente
from . import views
from .forms import ReciboPagoForm
from .models import CAI, Cliente, ConfiguracionFacturacionEmpresa, Factura, LineaFactura, MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoFactura, Producto, Proveedor, ReciboPago


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileReceiptsIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = ("fecha", "referencia", "concepto")
    PDF = b"%PDF-1.4\nSynthetic receipt renderer bytes\n%%EOF"

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.clientes, cls.cuentas, cls.proveedores, cls.facturas, cls.pagos, cls.recibos = {}, {}, {}, {}, {}, {}, {}
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Recibos QA {slug}", slug=slug, rtn=f"MREC-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            contable = CuentaContable.objects.create(empresa=empresa, codigo="1101-QA", nombre=f"Cuenta contable {slug}", tipo="activo")
            cls.cuentas[slug] = CuentaFinanciera.objects.create(empresa=empresa, nombre=f"Banco QA {slug}", cuenta_contable=contable)
            cls.proveedores[slug] = Proveedor.objects.create(empresa=empresa, nombre=f"Proveedor QA {slug}")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Sólo recibos", codigo="mobile-receipts-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_recibos=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-receipts", password="test-only", first_name="Cajero", last_name="Sintético", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        shared = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente compartido recibos QA", rtn="0801199900141")
        for empresa in cls.empresas.values():
            cls.clientes[empresa.slug] = Cliente.objects.get(empresa=empresa, perfil_compartido_id=shared.perfil_compartido_id)
            factura = Factura.objects.create(empresa=empresa, cliente=cls.clientes[empresa.slug], estado="borrador", subtotal=Decimal("1000.00"), impuesto=Decimal("150.00"), total=Decimal("1150.00"), total_lempiras=Decimal("1150.00"), fecha_emision=date(2026, 10, 1))
            cls.facturas[empresa.slug] = factura
            for index, (fecha, monto, metodo, isr, isv) in enumerate(((date(2026, 10, 1), "100", "efectivo", "0", "0"), (date(2026, 10, 3), "200", "transferencia", "10", "5"), (date(2026, 10, 5), "50", "tarjeta", "0", "0"))):
                pago = PagoFactura.objects.create(factura=factura, fecha=fecha, monto=Decimal(monto), retencion_isr=Decimal(isr), retencion_isv=Decimal(isv), metodo=metodo, referencia=f"PAGO-{empresa.slug}-{index}", cuenta_financiera=cls.cuentas[empresa.slug], cuenta_financiera_impuesto=cls.cuentas[empresa.slug], cajero=cls.usuario)
                cls.pagos[(empresa.slug, index)] = pago
                cls.recibos[(empresa.slug, index)] = pago.recibo
            Factura.objects.filter(pk=factura.pk).update(numero_factura=f"001-001-01-{factura.pk:08d}")
        cls.recibo = cls.recibos[(cls.empresa.slug, 1)]

    def setUp(self):
        self.client.force_login(self.usuario)
        pdf_patcher = patch.object(views, "HTML")
        self.html = pdf_patcher.start()
        self.html.return_value.write_pdf.return_value = self.PDF
        self.addCleanup(pdf_patcher.stop)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            patcher = patch(target)
            transport = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(transport.assert_not_called)

    def asignar_rol(self, empresa=None, granular=True, **permissions):
        rol = RolSistema.objects.create(nombre="Recibos restringidos", codigo=f"mobile-receipts-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, view="ver_recibo", empresa=None, recibo=None, mobile=True, **parameters):
        empresa = empresa or self.empresa
        recibo = recibo or self.recibos.get((empresa.slug, 1), self.recibo)
        args = [empresa.slug] if view == "recibos_dashboard" else [empresa.slug, recibo.pk]
        route = reverse(view, args=args)
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        return route+("?"+urlencode(query) if query else "")

    def datos(self, **extra):
        data = {"app": "1", "fecha": "2026-09-17", "referencia": "REF-DOCUMENTAL-NUEVA", "concepto": "Concepto documental nuevo\nSegunda línea conservada"}
        data.update(extra)
        return data

    def directo(self, view="ver_recibo", empresa=None, recibo=None, method="get", data=None, url=None):
        url = url or self.url(view, empresa, recibo)
        body = self.datos() if data is None else data
        if isinstance(body, QueryDict):
            body = {key: body.getlist(key) for key in body}
        request = RequestFactory().post(url, body) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def financial_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (PagoFactura, Factura, AsientoContable, LineaAsientoContable, CAI, NotaCredito, LineaFactura, MovimientoInventario, MovimientoLoteBodega, CuentaContable, CuentaFinanciera, Cliente))

    def side_counts(self):
        return tuple(model.objects.count() for model in (Paciente, ProfesionalSalud, Producto, Proveedor, RecetaMedica, TratamientoPaciente, CitaCliente, OpcionServicioAgenda, ConfiguracionAvanzadaEmpresa, ConfiguracionFacturacionEmpresa, ConfiguracionContableEmpresa, ConfiguracionClinica, ConfiguracionCRM))

    def snapshot(self):
        return (list(ReciboPago.objects.order_by("pk").values()), self.financial_snapshot(), self.side_counts(), RegistroAuditoria.objects.count())

    def assert_no_document_queries(self, queries):
        selects = [item["sql"] for item in queries if item["sql"].lstrip().upper().startswith("SELECT")]
        for model in (ReciboPago, PagoFactura, Factura, Cliente, CuentaFinanciera):
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects), model._meta.label)

    def new_receipt(self, empresa=None, text="Recibo paginado QA"):
        empresa = empresa or self.empresa
        factura = Factura.objects.create(empresa=empresa, cliente=self.clientes[empresa.slug], estado="borrador", subtotal=Decimal("1000"), impuesto=Decimal("150"), total=Decimal("1150"), total_lempiras=Decimal("1150"))
        pago = PagoFactura.objects.create(factura=factura, monto=Decimal("25"), metodo="efectivo", cajero=self.usuario)
        ReciboPago.objects.filter(pk=pago.recibo.pk).update(concepto=text)
        return ReciboPago.objects.get(pk=pago.recibo.pk)

    def test_four_companies_opt_in_mobile_and_original_web_templates_are_separate(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            for view, app_template, web_template in (("recibos_dashboard", "facturacion/mobile_receipt_list.html", "facturacion/recibos_premium.html"), ("ver_recibo", "facturacion/mobile_receipt_detail.html", "facturacion/ver_recibo_premium.html"), ("editar_recibo", "facturacion/mobile_receipt_edit.html", "facturacion/editar_recibo_premium.html")):
                app = self.client.get(self.url(view, empresa))
                web = self.client.get(self.url(view, empresa, mobile=False))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, app_template)
                self.assertTemplateUsed(web, web_template)
                self.assertTemplateNotUsed(web, app_template)
                self.assertIn("no-store", app["Cache-Control"])
                self.assertNotContains(app, "<table")
        self.assertEqual(self.snapshot(), before)

    def test_passive_get_head_and_pdf_never_seed_or_modify_financial_or_clinical_data(self):
        # The fixtures' original invoice save creates this optional setup;
        # reading receipts must not recreate it when it is genuinely absent.
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalogue, patch.object(views, "asegurar_cuenta_contable_cliente") as client_account:
            for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                self.assertEqual(self.client.get(self.url(view)).status_code, 200)
                self.assertEqual(self.client.head(self.url(view)).status_code, 200)
            accounts.assert_not_called()
            catalogue.assert_not_called()
            client_account.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_receipts_permission_alone_authorizes_all_four_routes_without_invoice_or_payment_grants(self):
        for granular in (True, False):
            self.asignar_rol(granular=granular, puede_recibos=True)
            for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                response = self.client.get(self.url(view))
                self.assertEqual(response.status_code, 200)
            detail = self.client.get(self.url())
            self.assertFalse(detail.context["can_view_invoice"])
            self.assertNotContains(detail, reverse("ver_factura", args=[self.empresa.slug, self.recibo.factura_id])+"?app=1")
            result = self.client.post(self.url("editar_recibo"), self.datos())
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result.url, self.url())

    def test_other_financial_permissions_do_not_grant_receipt_access_even_without_middleware(self):
        for permissions in ({"puede_ver_facturas": True}, {"puede_registrar_pagos_clientes": True}, {"puede_facturas": True, "puede_inventario": True}):
            self.asignar_rol(**permissions)
            before = self.snapshot()
            for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo(view)
                self.assert_no_document_queries(queries)
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo("editar_recibo", method="post")
            self.assertEqual(self.snapshot(), before)

    def test_direct_company_membership_active_license_module_and_clinic_scope_guards(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(view)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for invalid in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**invalid)
            for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(view)
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(empresa=other)

    def test_nonclinical_company_opt_in_flag_does_not_replace_original_web_templates(self):
        other = Empresa.objects.create(nombre="Otra empresa recibos", slug="other-mobile-receipts", rtn="MREC-OTHER", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=other, modulo=self.modulo)
        self.usuario.empresas_acceso.add(other)
        app = self.client.get(self.url("recibos_dashboard", other))
        web = self.client.get(self.url("recibos_dashboard", other, mobile=False))
        self.assertEqual(app.status_code, web.status_code)
        self.assertTemplateUsed(app, "facturacion/recibos_premium.html")
        self.assertTemplateNotUsed(app, "facturacion/mobile_receipt_list.html")

    def test_list_company_scope_shared_customer_copies_and_foreign_document_ids(self):
        self.assertEqual(len({client.perfil_compartido_id for client in self.clientes.values()}), 1)
        for empresa in self.empresas.values():
            response = self.client.get(self.url("recibos_dashboard", empresa))
            rows = list(response.context["recibos"])
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row.empresa_id == empresa.pk and row.cliente_id == self.clientes[empresa.slug].pk for row in rows))
            for other in self.empresas.values():
                if other.pk != empresa.pk:
                    self.assertNotContains(response, self.recibos[(other.slug, 0)].numero_recibo)
        foreign = self.recibos[("medical_spa", 1)]
        before = self.snapshot()
        for view in ("ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(view, recibo=foreign)
        self.assertEqual(self.snapshot(), before)

    def test_original_search_fields_filter_the_full_company_receipt_set(self):
        ReciboPago.objects.filter(pk=self.recibo.pk).update(referencia="REFERENCIA-ÚNICA-QA", concepto="CONCEPTO-ÚNICO-QA")
        factura = Factura.objects.get(pk=self.recibo.factura_id)
        for query in (self.recibo.numero_recibo, "REFERENCIA-ÚNICA-QA", "CONCEPTO-ÚNICO-QA", factura.numero_factura, self.clientes[self.empresa.slug].nombre):
            response = self.client.get(self.url("recibos_dashboard", q=query))
            self.assertEqual(response.status_code, 200)
            self.assertIn(self.recibo.pk, [row.pk for row in response.context["recibos"]])
            self.assertTrue(all(row.empresa_id == self.empresa.pk for row in response.context["recibos"]))

    def test_server_pagination_search_and_stable_tie_order_cover_all_company_rows_once(self):
        records = [self.new_receipt(text=f"Paginación coincidente {index}") for index in range(23)]
        ReciboPago.objects.filter(pk__in=[row.pk for row in records]).update(fecha_creacion=timezone.now())
        first = self.client.get(self.url("recibos_dashboard", q="Paginación coincidente", page="1"))
        second = self.client.get(self.url("recibos_dashboard", q="Paginación coincidente", page="2"))
        ids1, ids2 = [row.pk for row in first.context["recibos"]], [row.pk for row in second.context["recibos"]]
        self.assertEqual((len(ids1), len(ids2)), (20, 3))
        self.assertEqual(ids1+ids2, [row.pk for row in reversed(records)])
        self.assertEqual(first.context["recibos"].paginator.count, 23)
        self.assertContains(first, "page=2")
        self.assertContains(first, "q=Paginaci")
        self.assertEqual([row.pk for row in self.client.get(self.url("recibos_dashboard", q="Paginación coincidente", page="1")).context["recibos"]], ids1)

    def test_list_total_and_pages_cover_all_filtered_company_rows_not_only_current_page(self):
        for _ in range(21):
            self.new_receipt(text="Resumen filtrado")
        response = self.client.get(self.url("recibos_dashboard", q="Resumen filtrado"))
        self.assertEqual(len(response.context["recibos"]), 20)
        self.assertEqual(response.context["mobile_receipt_total"], 21)
        second = self.client.get(self.url("recibos_dashboard", q="Resumen filtrado", page="2"))
        self.assertEqual(len(second.context["recibos"]), 1)
        self.assertEqual(sum((row.monto for row in list(response.context["recibos"])+list(second.context["recibos"])), Decimal("0")), Decimal("525.00"))

    def test_detail_preserves_other_payments_semantics_including_future_payment_and_retentions(self):
        app = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(app.context["total_pagos_previos"], Decimal("150.00"))
        self.assertEqual(app.context["total_pagos_previos"], web.context["total_pagos_previos"])
        self.assertEqual([row.pk for row in app.context["pagos_factura"]], [self.pagos[(self.empresa.slug, index)].pk for index in range(3)])
        factura = app.context["recibo"].factura
        self.assertEqual(factura.total_pagado, Decimal("365.00"))
        self.assertEqual(factura.saldo_pendiente, Decimal("785.00"))
        current = app.context["recibo"].pago
        self.assertEqual((current.monto, current.total_retenciones, current.total_aplicado), (Decimal("200.00"), Decimal("15.00"), Decimal("215.00")))
        self.assertNotContains(app, "<table")

    def test_pdf_reuses_original_template_renderer_filename_headers_and_bytes(self):
        before = self.snapshot()
        original_render = views.render_to_string
        with patch.object(views, "render_to_string", wraps=original_render) as rendered:
            app = self.client.get(self.url("descargar_recibo_pdf"))
            app_html = self.html.call_args.kwargs["string"]
            rendered.assert_called_with("facturacion/recibo_pdf.html", {"empresa": self.empresa, "recibo": rendered.call_args.args[1]["recibo"], "logo_url": rendered.call_args.args[1]["logo_url"]})
            web = self.client.get(self.url("descargar_recibo_pdf", mobile=False))
            web_html = self.html.call_args.kwargs["string"]
        self.assertEqual(app.content, self.PDF)
        self.assertEqual(app.content, web.content)
        self.assertEqual(app_html, web_html)
        self.assertEqual(app["Content-Type"], "application/pdf")
        self.assertEqual(app["Content-Disposition"], web["Content-Disposition"])
        self.assertIn("attachment;", app["Content-Disposition"])
        self.assertIn("no-store", app["Cache-Control"])
        self.html.return_value.write_pdf.assert_called()
        self.assertEqual(self.snapshot(), before)

    def test_form_exact_original_three_fields_choices_validators_and_native_iso_date(self):
        app = self.client.get(self.url("editar_recibo"))
        web = self.client.get(self.url("editar_recibo", mobile=False))
        form, original = app.context["form"], web.context["form"]
        self.assertIsInstance(form, ReciboPagoForm)
        self.assertEqual(tuple(form.fields), self.FIELDS)
        for name in self.FIELDS:
            self.assertContains(app, f'name="{name}"', count=1)
            self.assertIs(type(form.fields[name].widget), type(original.fields[name].widget))
            self.assertEqual(form.fields[name].required, original.fields[name].required)
            self.assertEqual(getattr(form.fields[name], "max_length", None), getattr(original.fields[name], "max_length", None))
        self.assertContains(app, 'name="app" value="1"', count=1)
        self.assertContains(app, 'name="csrfmiddlewaretoken"', count=1)
        self.assertContains(app, f'value="{self.recibo.fecha.isoformat()}"')
        for forbidden in ("monto", "metodo", "cliente", "factura", "pago", "retencion_isr", "retencion_isv"):
            self.assertNotContains(app, f'name="{forbidden}"')

    def test_native_hidden_initial_schema_is_original_and_accepted_as_optional_singletons(self):
        class HiddenInputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.active, self.values = False, {}

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "form" and values.get("id") == "mobileReceiptForm":
                    self.active = True
                if self.active and tag == "input" and values.get("type") == "hidden" and values.get("name"):
                    self.values[values["name"]] = values.get("value", "")

            def handle_endtag(self, tag):
                if tag == "form":
                    self.active = False
        response = self.client.get(self.url("editar_recibo"))
        parser = HiddenInputs()
        parser.feed(response.content.decode())
        expected = {"initial-"+name for name, field in response.context["form"].fields.items() if field.show_hidden_initial}
        self.assertEqual({key for key in parser.values if key.startswith("initial-")}, expected)
        data = self.datos()
        data.update(parser.values)
        self.assertEqual(self.client.post(self.url("editar_recibo"), data).status_code, 302)

    def test_valid_edit_all_four_changes_only_documentary_three_fields_and_audit(self):
        for empresa in self.empresas.values():
            recibo = self.recibos[(empresa.slug, 1)]
            old = ReciboPago.objects.get(pk=recibo.pk)
            financial, counts, total = self.financial_snapshot(), self.side_counts(), ReciboPago.objects.count()
            response = self.client.post(self.url("editar_recibo", empresa), self.datos())
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url(empresa=empresa))
            actual = ReciboPago.objects.get(pk=recibo.pk)
            self.assertEqual(actual.fecha.isoformat(), "2026-09-17")
            self.assertEqual(actual.referencia, self.datos()["referencia"])
            self.assertEqual(actual.concepto, self.datos()["concepto"])
            for field in ("empresa_id", "factura_id", "cliente_id", "pago_id", "numero_recibo", "monto", "metodo", "fecha_creacion"):
                self.assertEqual(getattr(actual, field), getattr(old, field))
            self.assertEqual(self.financial_snapshot(), financial)
            self.assertEqual(self.side_counts(), counts)
            self.assertEqual(ReciboPago.objects.count(), total)

    def test_global_audit_retains_actual_actor_date_and_original_previous_new_document_values(self):
        old = ReciboPago.objects.get(pk=self.recibo.pk)
        started = timezone.now()
        self.assertEqual(self.client.post(self.url("editar_recibo"), self.datos()).status_code, 302)
        audit = RegistroAuditoria.objects.get(modelo=old._meta.model_name, objeto_id=str(old.pk), accion="modificar")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertGreaterEqual(audit.fecha, started)
        self.assertEqual(audit.metodo_http, "POST")
        self.assertEqual(set(audit.cambios), set(self.FIELDS))
        self.assertEqual(audit.cambios["fecha"], {"anterior": old.fecha.isoformat(), "nuevo": "2026-09-17"})
        self.assertEqual(audit.cambios["referencia"]["anterior"], old.referencia)
        self.assertEqual(audit.cambios["concepto"]["nuevo"], self.datos()["concepto"])

    def test_document_date_reference_and_concept_may_legitimately_differ_from_payment(self):
        pago_before = dict(PagoFactura.objects.values().get(pk=self.recibo.pago_id))
        self.client.post(self.url("editar_recibo"), self.datos())
        for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
            self.assertEqual(self.client.get(self.url(view)).status_code, 200)
        self.assertEqual(dict(PagoFactura.objects.values().get(pk=self.recibo.pago_id)), pago_before)
        actual = ReciboPago.objects.get(pk=self.recibo.pk)
        self.assertNotEqual(actual.fecha, actual.pago.fecha)
        self.assertNotEqual(actual.referencia, actual.pago.referencia)

    def test_app_and_web_edit_share_original_form_save_and_web_redirect(self):
        app_receipt, web_receipt = self.recibos[(self.empresa.slug, 0)], self.recibos[(self.empresa.slug, 2)]
        app = self.client.post(self.url("editar_recibo", recibo=app_receipt), self.datos())
        data = self.datos()
        data.pop("app")
        web = self.client.post(self.url("editar_recibo", recibo=web_receipt, mobile=False), data)
        self.assertEqual(app.url, self.url(recibo=app_receipt))
        self.assertEqual(web.url, self.url(recibo=web_receipt, mobile=False))
        app_receipt.refresh_from_db()
        web_receipt.refresh_from_db()
        for field in self.FIELDS:
            self.assertEqual(getattr(app_receipt, field), getattr(web_receipt, field))

    def test_invalid_bound_date_and_reference_preserve_all_fields_and_retentions_without_writes(self):
        for overrides in ({"fecha": "2026-02-31"}, {"referencia": "R"*101}, {"fecha": ""}):
            data = self.datos(**overrides)
            before = self.snapshot()
            response = self.client.post(self.url("editar_recibo"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_receipt_edit.html")
            self.assertTrue(response.context["form"].is_bound)
            self.assertTrue(response.context["form"].errors)
            for name in self.FIELDS:
                self.assertEqual(response.context["form"][name].value(), data[name])
            self.assertEqual(self.snapshot(), before)

    def test_optional_blank_reference_concept_and_original_form_validation_remain_unchanged(self):
        data = self.datos(referencia="", concepto="")
        original = ReciboPagoForm(data, instance=ReciboPago.objects.get(pk=self.recibo.pk))
        self.assertTrue(original.is_valid())
        result = self.client.post(self.url("editar_recibo"), data)
        self.assertEqual(result.status_code, 302)
        actual = ReciboPago.objects.get(pk=self.recibo.pk)
        self.assertEqual(actual.referencia, original.cleaned_data["referencia"])
        self.assertEqual(actual.concepto, original.cleaned_data["concepto"])

    def test_concept_is_stored_as_original_text_but_escaped_in_mobile_views(self):
        text = '<script>alert("synthetic")</script>\nTexto & conservado'
        self.assertEqual(self.client.post(self.url("editar_recibo"), self.datos(concepto=text)).status_code, 302)
        for view in ("ver_recibo", "editar_recibo", "recibos_dashboard"):
            response = self.client.get(self.url(view))
            self.assertNotContains(response, '<script>alert("synthetic")</script>')
            if view != "recibos_dashboard":
                self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(ReciboPago.objects.get(pk=self.recibo.pk).concepto, text)

    def test_closed_get_queries_duplicate_app_and_bad_pages_are_rejected_before_document_queries(self):
        for view, query in (("recibos_dashboard", "app=1&q=a&q=b"), ("recibos_dashboard", "app=1&unknown=1"), ("ver_recibo", "app=1&q=a"), ("editar_recibo", "app=1&page=1"), ("descargar_recibo_pdf", "app=1&app=0")):
            url = self.url(view).split("?")[0]+"?"+query
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(view, url=url)
        for page in ("", "0", "-1", "01", "1.0", "1e2", "١", "9223372036854775808", "999999999999999999999"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo("recibos_dashboard", url=self.url("recibos_dashboard", page=page))

    def test_post_closed_envelope_preserves_real_duplicates_and_rejects_unknown_financial_metadata(self):
        before = self.snapshot()
        for name in ("app", "fecha", "referencia", "concepto", "csrfmiddlewaretoken"):
            data = QueryDict(mutable=True)
            data.update(self.datos())
            data.setlist(name, ["1", "1"])
            with self.assertRaises(PermissionDenied):
                self.directo("editar_recibo", method="post", data=data)
        for name in ("monto", "metodo", "empresa", "cliente", "factura", "pago", "retencion_isr", "retencion_isv", "numero_recibo", "accion", "initial-concepto", "unknown"):
            with self.assertRaises(PermissionDenied):
                self.directo("editar_recibo", method="post", data=self.datos(**{name: "1"}))
        for data, query in (({"fecha": "2026-10-12"}, "app=1"), (self.datos(app="0"), "app=1"), (self.datos(), "app=0"), (self.datos(), "app=1&app=1")):
            with self.assertRaises(PermissionDenied):
                self.directo("editar_recibo", method="post", data=data, url=self.url("editar_recibo").split("?")[0]+"?"+query)
        self.assertEqual(self.snapshot(), before)

    def test_noncanonical_missing_and_overflow_document_paths_deny_before_pdf_or_save(self):
        before = self.snapshot()
        for value in ("0", "00"+str(self.recibo.pk), "9223372036854775807", "9223372036854775808", "999999999999999999999999999"):
            for view in ("ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                url = self.url(view).replace(f"/recibos/{self.recibo.pk}/", f"/recibos/{value}/")
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(view, url=url)
        self.html.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_imported_receipt_invoice_customer_and_payment_mismatches_are_denied_before_hidden_list_or_save(self):
        foreign_factura = self.facturas["medical_spa"]
        forged_payment = PagoFactura(factura=foreign_factura, monto=Decimal("1"))
        PagoFactura.objects.bulk_create([forged_payment])
        for field, value in (("factura_id", foreign_factura.pk), ("cliente_id", self.clientes["medical_spa"].pk), ("pago_id", forged_payment.pk)):
            original = getattr(self.recibo, field)
            try:
                ReciboPago.objects.filter(pk=self.recibo.pk).update(**{field: value})
                before = self.snapshot()
                for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                    url = self.url(view, q="No coincide", page="100") if view == "recibos_dashboard" else self.url(view)
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo(view, url=url)
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo("editar_recibo", method="post")
                self.assertEqual(self.snapshot(), before)
            finally:
                ReciboPago.objects.filter(pk=self.recibo.pk).update(**{field: original})

    def test_imported_invoice_customer_is_own_but_incoherent_receipt_customer_never_leaks(self):
        factura = self.facturas[self.empresa.slug]
        other = Cliente.objects.create(empresa=self.empresa, nombre="Cliente diferente factura QA", rtn="0801199900142")
        Factura.objects.filter(pk=factura.pk).update(cliente=other)
        before = self.snapshot()
        for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(view)
        self.assertEqual(self.snapshot(), before)

    def test_orphaned_receipt_and_invoice_related_keys_are_denied_without_inner_join_hiding(self):
        for model, record, field in ((ReciboPago, self.recibo, "factura_id"), (ReciboPago, self.recibo, "cliente_id"), (ReciboPago, self.recibo, "pago_id"), (Factura, self.facturas[self.empresa.slug], "cliente_id")):
            original = getattr(record, field)
            with connection.constraint_checks_disabled():
                try:
                    model.objects.filter(pk=record.pk).update(**{field: 2147483647})
                    before = self.snapshot()
                    for view in ("recibos_dashboard", "ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
                        with self.assertRaises((PermissionDenied, Http404)):
                            self.directo(view)
                    self.assertEqual(self.snapshot(), before)
                finally:
                    model.objects.filter(pk=record.pk).update(**{field: original})

    def test_all_sibling_payment_financial_relations_are_checked_before_detail_history_or_balance(self):
        sibling = self.pagos[(self.empresa.slug, 2)]
        for field, value in (("cuenta_financiera_id", self.cuentas["medical_spa"].pk), ("cuenta_financiera_impuesto_id", self.cuentas["medical_spa"].pk), ("proveedor_comision_id", self.proveedores["medical_spa"].pk)):
            original = getattr(sibling, field)
            try:
                PagoFactura.objects.filter(pk=sibling.pk).update(**{field: value})
                before = self.snapshot()
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo()
                self.assertEqual(self.snapshot(), before)
            finally:
                PagoFactura.objects.filter(pk=sibling.pk).update(**{field: original})
        for field in ("cuenta_financiera_id", "cuenta_financiera_impuesto_id", "proveedor_comision_id", "cajero_id"):
            original = getattr(sibling, field)
            with connection.constraint_checks_disabled():
                try:
                    PagoFactura.objects.filter(pk=sibling.pk).update(**{field: 2147483647})
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo()
                finally:
                    PagoFactura.objects.filter(pk=sibling.pk).update(**{field: original})

    def test_related_financial_account_contable_company_and_orphan_are_checked_without_active_rule(self):
        account = self.cuentas[self.empresa.slug]
        original = account.cuenta_contable_id
        for value in (self.cuentas["medical_spa"].cuenta_contable_id, 2147483647):
            with connection.constraint_checks_disabled():
                try:
                    CuentaFinanciera.objects.filter(pk=account.pk).update(cuenta_contable_id=value)
                    with self.assertRaises((PermissionDenied, Http404)):
                        self.directo()
                finally:
                    CuentaFinanciera.objects.filter(pk=account.pk).update(cuenta_contable_id=original)
        CuentaFinanciera.objects.filter(pk=account.pk).update(activa=False)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_multicompany_cashier_and_shared_inactive_customer_remain_legitimate_historical_links(self):
        actor = Usuario.objects.create_user(username="receipt-other-primary-company", empresa=self.empresas["medical_spa"], first_name="Autor", last_name="Multicompañía")
        actor.empresas_acceso.add(self.empresa)
        PagoFactura.objects.filter(pk=self.recibo.pago_id).update(cajero=actor)
        Cliente.objects.filter(pk=self.recibo.cliente_id).update(activo=False)
        before = self.snapshot()
        for view in ("ver_recibo", "editar_recibo", "descargar_recibo_pdf"):
            self.assertEqual(self.client.get(self.url(view)).status_code, 200)
        self.assertContains(self.client.get(self.url()), actor.get_full_name())
        self.assertEqual(self.snapshot(), before)

    def test_issued_credit_notes_keep_original_balance_and_reject_cross_company_or_customer(self):
        factura = self.facturas[self.empresa.slug]
        note = NotaCredito.objects.create(empresa=self.empresa, factura_origen=factura, cliente=self.clientes[self.empresa.slug], estado="borrador", total=Decimal("100"), subtotal=Decimal("100"), total_lempiras=Decimal("100"))
        NotaCredito.objects.filter(pk=note.pk).update(estado="emitida")
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recibo"].factura.saldo_pendiente, Decimal("685.00"))
        for field, value in (("empresa_id", self.empresas["medical_spa"].pk), ("cliente_id", self.clientes["medical_spa"].pk)):
            original = getattr(note, field)
            try:
                NotaCredito.objects.filter(pk=note.pk).update(**{field: value})
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo()
            finally:
                NotaCredito.objects.filter(pk=note.pk).update(**{field: original})

    def test_methods_logout_and_csrf_enforcement_preserve_financial_records(self):
        before = self.snapshot()
        for view in ("recibos_dashboard", "ver_recibo", "descargar_recibo_pdf"):
            for method in ("post", "put", "delete"):
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(view, method=method)
        self.assertEqual(self.snapshot(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url("editar_recibo")).status_code, 200)
        self.assertIn(protected.post(self.url("editar_recibo"), self.datos()).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(protected.post(self.url("editar_recibo"), self.datos(), HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value).status_code, 302)
        self.client.logout()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url("editar_recibo"), self.datos()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_hub_and_receipt_only_navigation_use_authorized_module_screen_without_agenda_grant(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        response = self.client.get(hub)
        self.assertEqual(response.status_code, 200)
        card = next(item for item in response.context["modulos_app"] if item["key"] == "recibos")
        self.assertEqual(card["estado"], "disponible")
        self.assertEqual(card["url"], self.url("recibos_dashboard"))
        for view in ("recibos_dashboard", "ver_recibo", "editar_recibo"):
            page = self.client.get(self.url(view))
            self.assertFalse(page.context["app_home_disponible"])
            self.assertEqual(page.context["app_modules_url"], hub)
            self.assertNotContains(page, 'rel="manifest"')
        self.asignar_rol(puede_ver_facturas=True)
        page = self.client.get(hub)
        self.assertFalse(any(item["key"] == "recibos" for item in page.context["modulos_app"]))

    def test_invoice_action_requires_actual_invoice_permission_not_only_receipt_permission(self):
        self.assertFalse(self.client.get(self.url()).context["can_view_invoice"])
        self.asignar_rol(puede_recibos=True, puede_ver_facturas=True)
        response = self.client.get(self.url())
        self.assertTrue(response.context["can_view_invoice"])
        self.assertContains(response, reverse("ver_factura", args=[self.empresa.slug, self.recibo.factura_id])+"?app=1")

    def test_module_capability_helper_is_pure_and_never_requires_extra_document_permissions(self):
        from .mobile_receipts import recibos_disponibles_app
        request = RequestFactory().get(self.url("recibos_dashboard"))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(recibos_disponibles_app(request, self.empresa))
        self.assert_no_document_queries(queries)
        self.assertEqual(self.snapshot(), before)
        self.asignar_rol(puede_ver_facturas=True, puede_registrar_pagos_clientes=True)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertFalse(recibos_disponibles_app(request, self.empresa))
