"""Cartera móvil: mismo reporte, permisos independientes y consulta sin efectos."""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from clinica.models import ConfiguracionClinica, GastoAdicional, Paciente, ProfesionalSalud, RecetaMedica, TratamientoPaciente
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable
from core.models import ConfiguracionAvanzadaEmpresa, ConfiguracionPowerBIEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM, OpcionServicioAgenda
from . import mobile_cxc, views
from .models import CAI, Cliente, ConfiguracionFacturacionEmpresa, Factura, LineaFactura, MovimientoInventario, MovimientoLoteBodega, NotaCredito, PagoComisionProveedor, PagoFactura, Producto, Proveedor, ReciboPago, TipoImpuesto
from .services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileCxcIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    SNAPSHOT_MODELS = (Factura, Cliente, Producto, LineaFactura, PagoFactura, ReciboPago, NotaCredito, PagoComisionProveedor, CuentaContable, CuentaFinanciera, AsientoContable, LineaAsientoContable, CAI, MovimientoInventario, MovimientoLoteBodega, GastoAdicional, Paciente, ProfesionalSalud, RecetaMedica, TratamientoPaciente, CitaCliente, OpcionServicioAgenda, ConfiguracionAvanzadaEmpresa, ConfiguracionFacturacionEmpresa, ConfiguracionPowerBIEmpresa, ConfiguracionContableEmpresa, ConfiguracionClinica, ConfiguracionCRM, RegistroAuditoria)

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.clientes, cls.cuentas, cls.productos, cls.facturas, cls.pagos, cls.proveedores = {}, {}, {}, {}, {}, {}, {}
        cls.impuesto = TipoImpuesto.objects.create(nombre="ISV global QA CxC", porcentaje=Decimal("15"))
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Clínica cartera QA {slug}", slug=slug, rtn=f"CXC-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            contable = CuentaContable.objects.create(empresa=empresa, codigo="1101-CXC", nombre=f"Cuenta cartera {slug}", tipo="activo")
            cls.cuentas[slug] = CuentaFinanciera.objects.create(empresa=empresa, nombre=f"Banco cartera {slug}", cuenta_contable=contable)
            cls.productos[slug] = Producto.objects.create(empresa=empresa, nombre=f"Producto cartera {slug}", precio=Decimal("100"), controla_inventario=False)
            cls.proveedores[slug] = Proveedor.objects.create(empresa=empresa, nombre=f"Proveedor cartera {slug}")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Sólo CxC QA", codigo="mobile-cxc-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_cxc=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-cxc", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        shared = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente compartido cartera QA", rtn="0801199900211")
        for empresa in cls.empresas.values():
            cls.clientes[empresa.slug] = Cliente.objects.get(empresa=empresa, perfil_compartido_id=shared.perfil_compartido_id)
            factura = Factura.objects.create(empresa=empresa, cliente=cls.clientes[empresa.slug], vendedor=cls.usuario, estado="borrador", subtotal=Decimal("100"), impuesto=Decimal("15"), total=Decimal("115"), total_lempiras=Decimal("115"), fecha_emision=date(2026, 9, 1), fecha_vencimiento=date(2026, 9, 30))
            cls.facturas[empresa.slug] = factura
            LineaFactura.objects.create(factura=factura, producto=cls.productos[empresa.slug], cantidad=Decimal("1"), precio_unitario=Decimal("100"), impuesto=cls.impuesto)
            pago = PagoFactura.objects.create(factura=factura, fecha=date(2026, 9, 3), monto=Decimal("10"), retencion_isr=Decimal("4"), retencion_isv=Decimal("1"), cuenta_financiera=cls.cuentas[empresa.slug], cajero=cls.usuario, referencia=f"QA-CXC-{empresa.slug}")
            cls.pagos[empresa.slug] = pago
            Factura.objects.filter(pk=factura.pk).update(estado="emitida", numero_factura=f"001-001-01-{factura.pk:08d}")

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target)
            mocked = transport.start()
            self.addCleanup(transport.stop)
            self.addCleanup(mocked.assert_not_called)

    def url(self, empresa=None, mobile=True, **query):
        parameters = {"app": "1"} if mobile else {}
        parameters.update(query)
        return reverse("reporte_cxc", args=[(empresa or self.empresa).slug])+("?"+urlencode(parameters) if parameters else "")

    def role(self, granular=True, empresa=None, **permissions):
        role = RolSistema.objects.create(nombre="CxC restringido", codigo=f"mobile-cxc-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": role})
        return role

    def directo(self, empresa=None, method="get", data=None, url=None, user=None):
        url = url or self.url(empresa)
        if isinstance(data, QueryDict):
            data = {key: data.getlist(key) for key in data}
        request = RequestFactory().post(url, data or {}) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in self.SNAPSHOT_MODELS)

    def new_client(self, nombre="Cliente adicional QA", empresa=None):
        empresa = empresa or self.empresa
        with suspender_sincronizacion_clientes_compartidos():
            return Cliente.objects.create(empresa=empresa, nombre=nombre, rtn=f"0801{Cliente.objects.count()+100:010d}")

    def new_invoice(self, cliente=None, empresa=None, amount=Decimal("115"), **fields):
        empresa = empresa or self.empresa
        factura = Factura.objects.create(empresa=empresa, cliente=cliente or self.clientes[empresa.slug], vendedor=self.usuario, estado="borrador", subtotal=amount, total=amount, total_lempiras=amount, fecha_emision=date(2026, 9, 1))
        Factura.objects.filter(pk=factura.pk).update(estado="emitida", numero_factura=f"001-001-01-{factura.pk:08d}", **fields)
        factura.refresh_from_db()
        return factura

    def new_note(self, factura=None, amount=Decimal("20"), **fields):
        factura = factura or self.facturas[self.empresa.slug]
        note = NotaCredito.objects.create(empresa=factura.empresa, factura_origen=factura, cliente=factura.cliente, estado="borrador", total=amount, subtotal=amount, total_lempiras=amount)
        NotaCredito.objects.filter(pk=note.pk).update(estado="emitida", **fields)
        return note

    def link_gasto(self, factura=None, empresa=None):
        empresa = empresa or self.empresa
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente sintético origen GA", identidad=f"0801{Paciente.objects.count()+200:010d}")
        return GastoAdicional.objects.create(empresa=empresa, paciente=paciente, factura=factura or self.facturas[self.empresa.slug], creado_por=self.usuario)

    def assert_denied_before_report(self, url=None, method="get", data=None):
        before = self.snapshot()
        with patch.object(views, "_construir_reporte_cxc", wraps=views._construir_reporte_cxc) as report:
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(url=url, method=method, data=data)
            report.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_four_company_optin_and_web_templates_for_both_role_types(self):
        for granular in (True, False):
            for empresa in self.empresas.values():
                self.role(granular=granular, empresa=empresa, puede_cxc=True)
                before = self.snapshot()
                app = self.client.get(self.url(empresa))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, "facturacion/mobile_cxc_list.html")
                self.assertNotContains(app, "<table")
                detail = self.client.get(self.url(empresa, cliente=self.clientes[empresa.slug].pk))
                self.assertEqual(detail.status_code, 200)
                self.assertTemplateUsed(detail, "facturacion/mobile_cxc_detail.html")
                self.assertTemplateUsed(self.client.get(self.url(empresa, mobile=False)), "facturacion/reporte_cxc_premium.html")
                self.assertEqual(self.snapshot(), before)

    def test_cxc_only_does_not_grant_invoice_payment_patient_or_receipt_reads(self):
        response = self.client.get(self.url(cliente=self.clientes[self.empresa.slug].pk))
        self.assertEqual(response.status_code, 200)
        for view, identifier in (("ver_factura", self.facturas[self.empresa.slug].pk), ("registrar_pago", self.facturas[self.empresa.slug].pk), ("ver_recibo", self.pagos[self.empresa.slug].recibo.pk)):
            target = reverse(view, args=[self.empresa.slug, identifier])+"?app=1"
            self.assertNotContains(response, target)
            self.assertIn(self.client.get(target).status_code, (302, 403))
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')

    def test_unrelated_role_permissions_deny_before_financial_queries(self):
        for granular in (True, False):
            for flags in ({"puede_reportes": True}, {"puede_cxp": True}, {"puede_exportar_reportes": True}, {"puede_ver_facturas": True}, {"puede_registrar_pagos_clientes": True}):
                self.role(granular=granular, **flags)
                self.assert_denied_before_report()
                request = RequestFactory().get(self.url()); request.user = Usuario.objects.get(pk=self.usuario.pk)
                with CaptureQueriesContext(connection) as captured:
                    with self.assertRaises(PermissionDenied):
                        mobile_cxc.autorizar_cxc_app(request, self.empresa)
                self.assertFalse(any('"facturacion_factura"' in row["sql"] for row in captured))

    def test_capability_helper_is_pure_and_has_no_unrelated_permission_dependency(self):
        request = RequestFactory().get(self.url()); request.user = self.usuario
        before = self.snapshot()
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(mobile_cxc.cxc_disponible_app(request, self.empresa))
        for model in (Factura, PagoFactura, NotaCredito, Cliente, Producto, ReciboPago):
            self.assertFalse(any(f'"{model._meta.db_table}"' in row["sql"] for row in captured))
        self.assertEqual(self.snapshot(), before)

    def test_combined_roles_keep_canonical_patient_invoice_and_safe_home_navigation(self):
        for codigo in ("clinica_medica", "agenda_citas"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            EmpresaModulo.objects.get_or_create(empresa=self.empresa, modulo=modulo)
        cases = (
            (True, {"puede_ver_pacientes": True}, False),
            (True, {"puede_ver_pacientes": True, "puede_ver_facturas": True, "puede_ver_calendario": True}, True),
            (False, {"puede_pacientes": True, "puede_ver_facturas": True, "puede_citas": True}, True),
        )
        for granular, grants, home in cases:
            self.role(granular=granular, puede_cxc=True, **grants)
            before = self.snapshot()
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["app_home_disponible"], home)
            self.assertEqual(response.context["app_patients_url"], reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1")
            self.assertEqual(response.context["app_invoices_url"], reverse("agenda_mobile", args=[self.empresa.slug])+"#facturas-app")
            self.assertNotContains(response, 'href=""')
            self.assertEqual(self.snapshot(), before)

    def test_membership_billing_module_active_and_license_guards_are_direct(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.assert_denied_before_report()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for changes in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**changes)
            self.assert_denied_before_report()
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        self.assert_denied_before_report(url=self.url(other))

    def test_superuser_preserves_original_license_exception_not_company_module(self):
        admin = Usuario.objects.create_superuser(username="cxc-super", password="test-only")
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.assertEqual(self.directo(user=admin).status_code, 200)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(PermissionDenied):
            self.directo(user=admin)

    def test_other_company_and_nonclinical_optin_leave_original_web_unchanged(self):
        empresa = Empresa.objects.create(nombre="Otra empresa QA", slug="otra_cxc", rtn="OTRA-CXC", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        response = self.client.get(self.url(empresa))
        self.assertTemplateUsed(response, "facturacion/reporte_cxc_premium.html")
        self.assertFalse(mobile_cxc.cxc_disponible_app(response.wsgi_request, empresa))
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="general")
        self.assertTemplateUsed(self.client.get(self.url()), "facturacion/reporte_cxc_premium.html")

    def test_mobile_post_and_unsafe_verbs_are_denied_before_helper_with_no_writes(self):
        for method in ("post", "put", "patch", "delete", "options"):
            self.assert_denied_before_report(method=method, data={"app": "1", "cliente": self.clientes[self.empresa.slug].pk})
        self.assert_denied_before_report(url=self.url(mobile=False), method="post", data={"app": "1"})

    def test_duplicate_unknown_and_mixed_metadata_are_rejected_not_silently_selected(self):
        for query in ("app=1&app=1", "app=0&app=1", "app=1&q=a&q=b", "app=1&cliente=1&cliente=2", "app=1&page=1&page=1", "app=1&accion=guardar", "app=1&empresa=2", "app=1&factura=1", "app=1&metodo=efectivo"):
            self.assert_denied_before_report(url=self.url(mobile=False)+"?"+query)

    def test_noncanonical_and_oversized_client_and_page_ids_fail_before_helper(self):
        for field in ("cliente", "page"):
            for value in ("-1", "0", "01", "1.0", "1e2", "1 ", "١", "9223372036854775808", "9"*80, "none"):
                self.assert_denied_before_report(url=self.url(**{field: value}))
        self.assert_denied_before_report(url=self.url(page=""))

    def test_foreign_missing_client_ids_are_not_data_or_web_fallback(self):
        for client_id in (self.clientes["medical_spa"].pk, 9223372036854775807):
            self.assert_denied_before_report(url=self.url(cliente=client_id))

    def test_inactive_local_shared_customer_is_still_queryable_without_sync(self):
        local = self.clientes[self.empresa.slug]
        Cliente.objects.filter(pk=local.pk).update(activo=False)
        before = self.snapshot()
        response = self.client.get(self.url(cliente=local.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cliente_seleccionado"].pk, local.pk)
        self.assertEqual(response.context["cliente_seleccionado"].perfil_compartido_id, self.clientes["medical_spa"].perfil_compartido_id)
        self.assertEqual(self.snapshot(), before)

    def test_original_helper_is_called_once_with_same_parameters_and_full_context(self):
        url = self.url(q="compartido", cliente=self.clientes[self.empresa.slug].pk)
        expected = views._construir_reporte_cxc(self.empresa, QueryDict(urlsplit(url).query))
        with patch.object(views, "_construir_reporte_cxc", wraps=views._construir_reporte_cxc) as helper:
            response = self.client.get(url)
        helper.assert_called_once()
        self.assertEqual(dict(helper.call_args.args[1]), dict(QueryDict(urlsplit(url).query)))
        for key in ("data", "resumen", "cliente_resumen", "facturas_pendientes_cliente", "facturas_con_saldo", "q", "cliente_id"):
            self.assertEqual(response.context[key], expected[key])

    def test_retention_credit_note_and_usd_balances_remain_original_model_values(self):
        factura = self.facturas[self.empresa.slug]
        self.new_note(factura, Decimal("20"))
        Factura.objects.filter(pk=factura.pk).update(moneda="USD", tipo_cambio=Decimal("25.25"))
        before = self.snapshot()
        response = self.client.get(self.url(cliente=factura.cliente_id))
        document = response.context["mobile_cxc_documents"][0]["factura"]
        self.assertEqual((document.total_pagado, document.total_documento_ajustado, document.saldo_pendiente), (Decimal("15"), Decimal("95"), Decimal("80")))
        self.assertEqual((document.reporte_total_hnl, document.reporte_saldo_hnl, response.context["resumen"]["total_cartera"]), (Decimal("2398.75"), Decimal("2020.00"), Decimal("2020.00")))
        self.assertEqual(self.snapshot(), before)

    def test_usd_historical_zero_exchange_uses_original_report_fallback_not_new_rule(self):
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(moneda="USD", tipo_cambio=Decimal("0"))
        response = self.client.get(self.url(cliente=self.clientes[self.empresa.slug].pk))
        factura = response.context["mobile_cxc_documents"][0]["factura"]
        self.assertEqual((factura.reporte_tasa_cambio, factura.reporte_saldo_hnl), (Decimal("1.0000"), Decimal("100.00")))

    def test_only_original_emitted_positive_balance_scope_is_shown(self):
        draft = self.new_invoice(); Factura.objects.filter(pk=draft.pk).update(estado="borrador")
        void = self.new_invoice(); Factura.objects.filter(pk=void.pk).update(estado="anulada")
        paid = self.new_invoice()
        CAI.objects.create(empresa=self.empresa, numero_cai="CAI-CXC-PAGO-QA", establecimiento="001", punto_emision="001", tipo_documento="01", rango_inicial=1, rango_final=100000, correlativo_actual=0, fecha_activacion=date(2020, 1, 1), fecha_limite=date(2030, 12, 31))
        PagoFactura.objects.create(factura=paid, monto=paid.total, cuenta_financiera=self.cuentas[self.empresa.slug])
        response = self.client.get(self.url())
        self.assertEqual([f.pk for f in response.context["facturas_con_saldo"]], [self.facturas[self.empresa.slug].pk])
        self.assertEqual(response.context["resumen"]["facturas_pendientes"], 1)

    def test_original_aging_buckets_include_future_and_missing_due_date(self):
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(estado="anulada")
        today = date(2026, 10, 12)
        for days in (-5, 30, 31, 60, 61, 90, 91):
            self.new_invoice(amount=Decimal("10"), fecha_emision=today-timedelta(days=days), fecha_vencimiento=None)
        with patch.object(views, "date") as clock:
            clock.today.return_value = today
            response = self.client.get(self.url())
        item = response.context["data"][0]
        self.assertEqual([item[key] for key in ("0_30", "31_60", "61_90", "90_mas", "total")], [Decimal("20"), Decimal("20"), Decimal("20"), Decimal("10"), Decimal("70")])

    def test_customer_pagination_preserves_full_summary_and_all_filtered_rows(self):
        for index in range(25):
            self.new_invoice(cliente=self.new_client(f"Paciente cartera paginado {index:02d}"), amount=Decimal("10"))
        before = self.snapshot()
        response = self.client.get(self.url(page="2"))
        self.assertEqual((response.context["mobile_cxc_total"], response.context["mobile_cxc_page"], len(response.context["mobile_cxc_items"])), (26, 2, 6))
        self.assertEqual(response.context["resumen"], {"clientes_con_saldo": 26, "total_cartera": Decimal("350"), "facturas_pendientes": 26})
        self.assertEqual(len(response.context["data"]), 26)
        self.assertEqual(self.snapshot(), before)

    def test_selected_document_pagination_keeps_original_customer_summary(self):
        for _ in range(25):
            self.new_invoice(amount=Decimal("10"))
        response = self.client.get(self.url(cliente=self.clientes[self.empresa.slug].pk, page="2"))
        self.assertEqual((response.context["mobile_cxc_total"], len(response.context["mobile_cxc_documents"])), (26, 6))
        self.assertEqual(response.context["cliente_resumen"]["facturas"], 26)
        self.assertEqual(response.context["cliente_resumen"]["saldo_total"], Decimal("350"))
        self.assertEqual(len(response.context["facturas_pendientes_cliente"]), 26)
        previous = parse_qs(urlsplit(response.context["mobile_cxc_previous_url"]).query)
        self.assertEqual(previous, {"app": ["1"], "cliente": [str(self.clientes[self.empresa.slug].pk)], "page": ["1"]})

    def test_search_and_client_links_preserve_encoded_query_without_xss(self):
        q = '<script>alert("QA")</script> & cartera'
        client = self.new_client(q)
        self.new_invoice(cliente=client)
        response = self.client.get(self.url(q=q))
        self.assertEqual(response.context["mobile_cxc_total"], 1)
        self.assertEqual(response.context["mobile_cxc_query"], q)
        self.assertNotContains(response, q)
        detail = response.context["mobile_cxc_items"][0]["detail_url"]
        self.assertEqual(parse_qs(urlsplit(detail).query), {"app": ["1"], "q": [q], "cliente": [str(client.pk)]})
        selected = self.client.get(detail)
        self.assertEqual(parse_qs(urlsplit(selected.context["app_cxc_back_url"]).query), {"app": ["1"], "q": [q]})

    def test_own_customer_without_pending_invoices_has_readonly_empty_detail(self):
        client = self.new_client("Cliente sin deuda QA")
        response = self.client.get(self.url(cliente=client.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/mobile_cxc_detail.html")
        self.assertEqual(response.context["mobile_cxc_documents"], [])
        self.assertIsNone(response.context["cliente_resumen"])

    def test_empty_cartera_does_not_seed_any_configuration_or_catalog(self):
        Factura.objects.filter(empresa=self.empresa).update(estado="anulada")
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        ConfiguracionFacturacionEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("seed")), patch.object(ConfiguracionFacturacionEmpresa.objects, "get_or_create", side_effect=AssertionError("seed")):
            for method in ("get", "head"):
                response = getattr(self.client, method)(self.url())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["mobile_cxc_items"], [])
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_invoice_customer_is_denied_before_search_page_or_paid_exclusion(self):
        foreign = self.clientes["medical_spa"]
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(cliente=foreign, total=Decimal("0"))
        self.assert_denied_before_report(url=self.url(q="no coincide", page="2"))

    def test_imported_orphan_invoice_customer_is_denied_without_inner_join_hiding(self):
        factura = self.facturas[self.empresa.slug]
        original = factura.cliente_id
        try:
            with connection.constraint_checks_disabled():
                Factura.objects.filter(pk=factura.pk).update(cliente_id=999999999)
            self.assert_denied_before_report(url=self.url(q="none"))
        finally:
            Factura.objects.filter(pk=factura.pk).update(cliente_id=original)

    def test_historical_inactive_multi_company_actors_are_valid_but_orphans_denied(self):
        actor = Usuario.objects.create_user(username="cxc-historical-actor", password="test-only", empresa=self.empresas["medical_spa"], is_active=False)
        factura = self.facturas[self.empresa.slug]
        Factura.objects.filter(pk=factura.pk).update(vendedor=actor)
        PagoFactura.objects.filter(pk=self.pagos[self.empresa.slug].pk).update(cajero=actor)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        try:
            with connection.constraint_checks_disabled():
                Factura.objects.filter(pk=factura.pk).update(vendedor_id=999999999)
            self.assert_denied_before_report()
        finally:
            Factura.objects.filter(pk=factura.pk).update(vendedor=actor)

    def test_historical_payment_account_and_coa_foreign_or_orphan_are_rejected(self):
        pago = self.pagos[self.empresa.slug]
        own = self.cuentas[self.empresa.slug]
        CuentaFinanciera.objects.filter(pk=own.pk).update(activa=False)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        PagoFactura.objects.filter(pk=pago.pk).update(cuenta_financiera=self.cuentas["medical_spa"])
        self.assert_denied_before_report(url=self.url(q="hidden"))
        PagoFactura.objects.filter(pk=pago.pk).update(cuenta_financiera=own)
        CuentaFinanciera.objects.filter(pk=own.pk).update(cuenta_contable=self.cuentas["medical_spa"].cuenta_contable)
        self.assert_denied_before_report()
        CuentaFinanciera.objects.filter(pk=own.pk).update(cuenta_contable_id=own.cuenta_contable_id)
        for model, pk, field, original in ((PagoFactura, pago.pk, "cuenta_financiera_id", own.pk), (CuentaFinanciera, own.pk, "cuenta_contable_id", own.cuenta_contable_id)):
            try:
                with connection.constraint_checks_disabled():
                    model.objects.filter(pk=pk).update(**{field: 999999999})
                self.assert_denied_before_report()
            finally:
                model.objects.filter(pk=pk).update(**{field: original})

    def test_payment_provider_and_cashier_existence_are_checked_before_balances(self):
        pago = self.pagos[self.empresa.slug]
        PagoFactura.objects.filter(pk=pago.pk).update(proveedor_comision=self.proveedores["medical_spa"])
        self.assert_denied_before_report()
        PagoFactura.objects.filter(pk=pago.pk).update(proveedor_comision=None)
        try:
            with connection.constraint_checks_disabled():
                PagoFactura.objects.filter(pk=pago.pk).update(cajero_id=999999999)
            self.assert_denied_before_report()
        finally:
            PagoFactura.objects.filter(pk=pago.pk).update(cajero=None)

    def test_only_issued_credit_notes_require_current_company_and_matching_client(self):
        note = self.new_note(empresa_id=self.empresas["medical_spa"].pk)
        self.assert_denied_before_report()
        NotaCredito.objects.filter(pk=note.pk).update(empresa=self.empresa, cliente=self.clientes["medical_spa"])
        self.assert_denied_before_report(url=self.url(q="hidden"))
        NotaCredito.objects.filter(pk=note.pk).update(estado="borrador")
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_foreign_invoice_product_without_original_gasto_is_not_authorized(self):
        line = LineaFactura.objects.get(factura=self.facturas[self.empresa.slug])
        LineaFactura.objects.filter(pk=line.pk).update(producto=self.productos["medical_spa"])
        self.assert_denied_before_report()

    def test_original_shared_gasto_noninventory_products_are_valid_in_all_clinics(self):
        for empresa in self.empresas.values():
            factura = self.facturas[empresa.slug]
            self.link_gasto(factura=factura, empresa=empresa)
            for source in ("hospital_mia", "medical_spa"):
                product = self.productos[source]
                Producto.objects.filter(pk=product.pk).update(activo=False, eliminado=True)
                LineaFactura.objects.filter(factura=factura).update(producto=product)
                before = self.snapshot()
                self.assertEqual(self.client.get(self.url(empresa)).status_code, 200)
                self.assertEqual(self.snapshot(), before)

    def test_shared_gasto_foreign_inventory_product_remains_disallowed_for_emitted_invoice(self):
        self.link_gasto()
        product = self.productos["medical_spa"]
        Producto.objects.filter(pk=product.pk).update(controla_inventario=True)
        LineaFactura.objects.filter(factura=self.facturas[self.empresa.slug]).update(producto=product)
        self.assert_denied_before_report()

    def test_shared_exception_does_not_allow_wrong_gasto_company_or_other_catalog(self):
        gasto = self.link_gasto()
        LineaFactura.objects.filter(factura=self.facturas[self.empresa.slug]).update(producto=self.productos["medical_spa"])
        GastoAdicional.objects.filter(pk=gasto.pk).update(empresa=self.empresas["medical_spa"])
        self.assert_denied_before_report()
        GastoAdicional.objects.filter(pk=gasto.pk).update(empresa=self.empresa)
        LineaFactura.objects.filter(factura=self.facturas[self.empresa.slug]).update(producto=self.productos["luque_aestetic"])
        self.assert_denied_before_report()

    def test_manual_null_product_and_global_inactive_tax_keep_original_financial_data(self):
        LineaFactura.objects.filter(factura=self.facturas[self.empresa.slug]).update(producto=None, descripcion_manual="Servicio histórico manual")
        TipoImpuesto.objects.filter(pk=self.impuesto.pk).update(activo=False)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["facturas_con_saldo"][0].reporte_base_15_hnl, Decimal("100"))

    def test_orphan_product_or_global_tax_import_is_denied_before_report(self):
        line = LineaFactura.objects.get(factura=self.facturas[self.empresa.slug])
        for field, original in (("producto_id", line.producto_id), ("impuesto_id", line.impuesto_id)):
            try:
                with connection.constraint_checks_disabled():
                    LineaFactura.objects.filter(pk=line.pk).update(**{field: 999999999})
                self.assert_denied_before_report(url=self.url(q="hidden", page="2"))
            finally:
                LineaFactura.objects.filter(pk=line.pk).update(**{field: original})
        self.link_gasto()
        product = self.productos[self.empresa.slug]
        try:
            with connection.constraint_checks_disabled():
                Producto.objects.filter(pk=product.pk).update(empresa_id=999999999)
            self.assert_denied_before_report()
        finally:
            Producto.objects.filter(pk=product.pk).update(empresa=self.empresa)

    def test_other_company_or_nonemitted_corruption_does_not_block_own_cartera(self):
        Factura.objects.filter(pk=self.facturas["medical_spa"].pk).update(cliente=self.clientes[self.empresa.slug])
        own_draft = self.new_invoice(); Factura.objects.filter(pk=own_draft.pk).update(estado="borrador", cliente=self.clientes["medical_spa"])
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_unrelated_receipt_and_commission_documents_are_not_read_or_required(self):
        # Those documents are validated by their own readers/savers, not used in
        # Factura.saldo_pendiente or the original CxC report.
        ReciboPago.objects.filter(pago=self.pagos[self.empresa.slug]).update(empresa=self.empresas["medical_spa"])
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(self.url()).status_code, 200)
        for model in (ReciboPago, PagoComisionProveedor, ConfiguracionPowerBIEmpresa):
            selects = [row["sql"] for row in captured if row["sql"].lstrip().upper().startswith("SELECT")]
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects))

    def test_get_and_head_never_recalculate_save_seed_or_change_stale_status(self):
        Factura.objects.filter(pk=self.facturas[self.empresa.slug].pk).update(estado_pago="pagado")
        before = self.snapshot()
        with patch.object(Factura, "calcular_totales", side_effect=AssertionError("recalc")), patch.object(Factura, "actualizar_estado_pago", side_effect=AssertionError("write")), patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("seed")):
            for method in ("get", "head"):
                response = getattr(self.client, method)(self.url(cliente=self.clientes[self.empresa.slug].pk))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(response.context["mobile_cxc_documents"][0]["factura"].estado_pago, "pagado")
                if method == "head":
                    self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_hub_and_metadata_hide_financial_cartera_without_removing_backend_permission(self):
        for granular in (True, False):
            self.role(granular=granular, puede_cxc=True)
            response = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("cxc", {item["key"] for item in response.context["modulos_app"]})
            data = self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug])).json()
            self.assertNotIn("cxc", {item["key"] for item in data["modulos"]})
            self.assertTrue(self.usuario.tiene_permiso_erp("puede_cxc", self.empresa))
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertFalse(any(item["key"] in {"facturacion", "agenda", "pacientes"} for item in data["modulos"]))

    def test_unflagged_original_web_post_keeps_its_existing_readonly_behavior(self):
        before = self.snapshot()
        response = self.client.post(self.url(mobile=False), {"cliente": self.clientes[self.empresa.slug].pk, "accion": "no-operacion"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/reporte_cxc_premium.html")
        self.assertEqual(self.snapshot(), before)

    def test_csrf_token_cannot_turn_readonly_report_into_a_write_endpoint(self):
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario)
        before = self.snapshot()
        token = "a"*32; strict.cookies["csrftoken"] = token
        response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": token, "accion": "guardar", "monto": "100"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)
