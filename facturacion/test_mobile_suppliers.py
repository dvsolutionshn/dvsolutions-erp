"""Mobile suppliers keep the canonical profile form and purchase projections."""
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from clinica.models import ConfiguracionClinica, Paciente, ProfesionalSalud
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM
from . import views
from .forms import ProveedorForm
from .models import CAI, Cliente, ClienteContable, CompraInventario, ComprobanteEgresoCompra, ConfiguracionFacturacionEmpresa, CuentaAcumuladoCompra, Factura, LineaCompraInventario, MovimientoInventario, MovimientoLoteBodega, PagoCompra, PagoFactura, Producto, Proveedor, ReciboPago, TipoImpuesto


class SupplierControlNames(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inside = False
        self.names = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.inside = attrs.get("id") == "mobileSupplierForm"
        if self.inside and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.names.append(attrs["name"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileSuppliersIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = ("nombre", "rtn", "contacto", "telefono", "correo", "direccion", "ciudad", "condicion_pago", "dias_credito", "activo")
    ROUTES = ("proveedores_facturacion", "ver_proveedor_facturacion", "crear_proveedor_facturacion", "editar_proveedor_facturacion")
    HISTORY_MODELS = (CompraInventario, LineaCompraInventario, PagoCompra, ComprobanteEgresoCompra, Factura, PagoFactura, ReciboPago)

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.proveedores, cls.cuentas, cls.productos, cls.compras, cls.lineas, cls.pagos = {}, {}, {}, {}, {}, {}, {}
        cls.rol = RolSistema.objects.create(nombre="Proveedores QA", codigo="mobile-suppliers-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_proveedores=True, puede_crear_proveedores=True, puede_editar_proveedores=True)
        cls.impuesto = TipoImpuesto.objects.create(nombre="Impuesto global QA", porcentaje=Decimal("15"), activo=True)
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Clínica proveedores QA {slug}", slug=slug, rtn=f"MSUP-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            contable = CuentaContable.objects.create(empresa=empresa, codigo="1101-SUP", nombre=f"Cuenta proveedores {slug}", tipo="activo")
            cls.cuentas[slug] = CuentaFinanciera.objects.create(empresa=empresa, nombre=f"Banco proveedores {slug}", cuenta_contable=contable)
            provider = Proveedor.objects.create(empresa=empresa, nombre=f"Proveedor QA {slug}", rtn=f"SUP-RTN-{index}", contacto=f"Contacto QA {index}", telefono="9999-0000", correo="supplier@example.test", direccion="Dirección original\nSegunda línea", ciudad=f"Ciudad QA {index}", condicion_pago="credito", dias_credito=15, cuenta_contable=contable)
            cls.proveedores[slug] = provider
            product = Producto.objects.create(empresa=empresa, nombre=f"Producto proveedores {slug}", codigo=f"MSUP-{index}", precio=Decimal("20"))
            cls.productos[slug] = product
            compra = CompraInventario.objects.create(empresa=empresa, proveedor=provider, proveedor_nombre=provider.nombre, estado="aplicada", fecha_documento=date(2026, 10, 1), condicion_pago="credito", dias_credito=1, cuenta_financiera_pago=cls.cuentas[slug])
            cls.compras[slug] = compra
            cls.lineas[slug] = LineaCompraInventario.objects.create(compra=compra, producto=product, cantidad=Decimal("2"), costo_unitario=Decimal("20"), impuesto=cls.impuesto)
            cls.pagos[slug] = PagoCompra.objects.create(compra=compra, monto=Decimal("10"), cuenta_financiera=cls.cuentas[slug], fecha=date(2026, 10, 2))
        cls.empresa = cls.empresas["hospital_mia"]
        cls.proveedor = cls.proveedores[cls.empresa.slug]
        cls.usuario = Usuario.objects.create_user(username="mobile-suppliers", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target)
            mock = transport.start()
            self.addCleanup(transport.stop)
            self.addCleanup(mock.assert_not_called)

    def asignar_rol(self, granular=True, empresa=None, **permissions):
        role = RolSistema.objects.create(nombre="Proveedores restringidos", codigo=f"mobile-suppliers-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": role})
        return role

    def url(self, route="ver_proveedor_facturacion", empresa=None, proveedor=None, mobile=True, **query):
        empresa = empresa or self.empresa
        proveedor = proveedor or self.proveedores.get(empresa.slug, self.proveedor)
        args = [empresa.slug] if route in {"proveedores_facturacion", "crear_proveedor_facturacion"} else [empresa.slug, proveedor.pk]
        parameters = {"app": "1"} if mobile else {}
        parameters.update(query)
        return reverse(route, args=args)+("?"+urlencode(parameters) if parameters else "")

    def datos(self, **extra):
        body = {"app": "1", "nombre": "Proveedor móvil nuevo", "rtn": "0801199900171", "contacto": "Contacto nuevo", "telefono": "9999-7777", "correo": "new@example.test", "direccion": "Dirección nueva\nSegunda línea conservada", "ciudad": "Ciudad nueva", "condicion_pago": "credito", "dias_credito": "30", "activo": "on"}
        body.update(extra)
        return body

    def directo(self, route="ver_proveedor_facturacion", method="get", data=None, url=None, proveedor=None, empresa=None):
        url = url or self.url(route, empresa, proveedor)
        data = self.datos() if data is None else data
        if isinstance(data, QueryDict):
            data = {key: data.getlist(key) for key in data}
        request = RequestFactory().post(url, data) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def assert_rejected(self, callback):
        try:
            result = callback()
        except (PermissionDenied, Http404):
            return
        self.assertIn(result.status_code, (403, 404, 405))

    def financial_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (CompraInventario, LineaCompraInventario, PagoCompra, ComprobanteEgresoCompra, CuentaContable, CuentaFinanciera, AsientoContable, LineaAsientoContable, CAI, Producto, MovimientoInventario, MovimientoLoteBodega, Factura, PagoFactura, ReciboPago))

    def snapshot(self):
        counts = tuple(model.objects.count() for model in (ConfiguracionAvanzadaEmpresa, ConfiguracionFacturacionEmpresa, ConfiguracionContableEmpresa, ConfiguracionClinica, ConfiguracionCRM, Paciente, ProfesionalSalud, CitaCliente, RegistroAuditoria))
        return list(Proveedor.objects.order_by("pk").values()), self.financial_snapshot(), counts

    def assert_no_history_queries(self, captured):
        selects = [row["sql"] for row in captured if row["sql"].lstrip().upper().startswith("SELECT")]
        self.assertFalse(any(f'"{model._meta.db_table}"' in sql for model in self.HISTORY_MODELS for sql in selects))

    def new_purchase(self, amount=Decimal("10"), estado="aplicada", fecha=None):
        compra = CompraInventario.objects.create(empresa=self.empresa, proveedor=self.proveedor, proveedor_nombre=self.proveedor.nombre, estado=estado, condicion_pago="credito", dias_credito=1, fecha_documento=fecha or date(2026, 10, 1))
        LineaCompraInventario.objects.create(compra=compra, producto=self.productos[self.empresa.slug], cantidad=Decimal("1"), costo_unitario=amount)
        return compra

    def test_four_companies_mobile_and_original_web_templates_remain_separate(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            for route, app_template, web_template in ((self.ROUTES[0], "facturacion/mobile_supplier_list.html", "facturacion/proveedores_filtros_premium.html"), (self.ROUTES[1], "facturacion/mobile_supplier_detail.html", "facturacion/ver_proveedor_cxp_premium.html"), (self.ROUTES[2], "facturacion/mobile_supplier_form.html", "facturacion/crear_proveedor.html"), (self.ROUTES[3], "facturacion/mobile_supplier_form.html", "facturacion/crear_proveedor.html")):
                mobile = self.client.get(self.url(route, empresa))
                self.assertEqual(mobile.status_code, 200)
                self.assertTemplateUsed(mobile, app_template)
                self.assertIn("no-store", mobile["Cache-Control"])
                self.assertNotContains(mobile, "<table")
                web = self.client.get(self.url(route, empresa, mobile=False))
                self.assertTemplateUsed(web, web_template)
                self.assertTemplateNotUsed(web, app_template)
        self.assertEqual(self.snapshot(), before)

    def test_get_head_do_not_seed_accounts_catalogue_config_or_write_audit_and_business_models(self):
        ConfiguracionAvanzadaEmpresa.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as accounts, patch.object(views, "cargar_catalogo_base_honduras") as catalogue, patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa") as config:
            for route in self.ROUTES:
                for method in ("get", "head"):
                    response = getattr(self.client, method)(self.url(route))
                    self.assertEqual(response.status_code, 200)
                    if method == "head":
                        self.assertEqual(response.content, b"")
            accounts.assert_not_called()
            catalogue.assert_not_called()
            config.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_canonical_form_has_all_ten_fields_exactly_once_without_account_or_purchase_inputs(self):
        self.assertEqual(tuple(ProveedorForm.base_fields), self.FIELDS)
        for route in self.ROUTES[2:]:
            result = self.client.get(self.url(route))
            self.assertIsInstance(result.context["form"], ProveedorForm)
            names = Counter(SupplierControlNames(result.content.decode()).names)
            expected = set(self.FIELDS) | {"app", "csrfmiddlewaretoken"}
            expected |= {f"initial-{name}" for name, field in ProveedorForm.base_fields.items() if field.show_hidden_initial}
            self.assertEqual(set(names), expected)
            self.assertTrue(all(count == 1 for count in names.values()))

    def test_granular_read_create_and_edit_are_independent_actions_without_extra_permissions(self):
        from .mobile_suppliers import proveedores_disponibles_app
        cases = (("puede_proveedores", self.ROUTES[:2]), ("puede_crear_proveedores", (self.ROUTES[2],)), ("puede_editar_proveedores", (self.ROUTES[3],)))
        for permission, allowed in cases:
            self.asignar_rol(**{permission: True})
            before = self.snapshot()
            request = RequestFactory().get("/")
            request.user = Usuario.objects.get(pk=self.usuario.pk)
            with CaptureQueriesContext(connection) as queries:
                capabilities = proveedores_disponibles_app(request, self.empresa)
            self.assertEqual(capabilities, {"ver": permission == "puede_proveedores", "crear": permission == "puede_crear_proveedores", "editar": permission == "puede_editar_proveedores"})
            self.assert_no_history_queries(queries)
            self.assertFalse(any(f'"{Proveedor._meta.db_table}"' in row["sql"] for row in queries))
            for route in self.ROUTES:
                if route in allowed:
                    self.assertEqual(self.client.get(self.url(route)).status_code, 200)
                else:
                    self.assert_rejected(lambda: self.directo(route))
            self.assertEqual(self.snapshot(), before)

    def test_legacy_roles_use_same_specific_actions_without_proveedores_fallback_for_writes(self):
        for permission, allowed in (("puede_proveedores", self.ROUTES[:2]), ("puede_crear_proveedores", (self.ROUTES[2],)), ("puede_editar_proveedores", (self.ROUTES[3],))):
            self.asignar_rol(granular=False, **{permission: True})
            for route in self.ROUTES:
                if route in allowed:
                    self.assertEqual(self.client.get(self.url(route)).status_code, 200)
                else:
                    self.assert_rejected(lambda: self.directo(route))

    def test_other_financial_permissions_or_missing_role_cannot_bypass_backend_guards(self):
        for grant in ({}, {"puede_compras": True}, {"puede_registrar_pagos_proveedores": True}, {"puede_egresos": True}, {"puede_configuracion_facturacion": True}):
            self.asignar_rol(**grant)
            before = self.snapshot()
            for route in self.ROUTES:
                self.assert_rejected(lambda: self.directo(route))
            for route in self.ROUTES[2:]:
                self.assert_rejected(lambda: self.directo(route, method="post"))
            self.assertEqual(self.snapshot(), before)

    def test_billing_module_activity_license_membership_are_checked_even_without_middleware(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for route in self.ROUTES:
            self.assert_rejected(lambda: self.directo(route))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for values in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values)
            self.assert_rejected(lambda: self.directo(self.ROUTES[2]))
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        foreign = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(foreign)
        self.assert_rejected(lambda: self.directo(empresa=foreign))

    def test_other_clinic_opt_in_flag_does_not_replace_original_web_supplier_templates(self):
        empresa = Empresa.objects.create(nombre="Otra clínica proveedores", slug="otra_clinica_proveedores", rtn="MSUP-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        page = self.client.get(self.url(self.ROUTES[2], empresa))
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "facturacion/crear_proveedor.html")
        self.assertTemplateNotUsed(page, "facturacion/mobile_supplier_form.html")

    def test_reader_actions_are_hidden_and_cannot_post_to_write_or_read_routes(self):
        self.asignar_rol(puede_proveedores=True)
        before = self.snapshot()
        detail = self.client.get(self.url())
        self.assertFalse(detail.context["can_create_supplier"])
        self.assertFalse(detail.context["can_edit_supplier"])
        self.assertNotContains(detail, self.url(self.ROUTES[3]))
        for route in self.ROUTES:
            self.assert_rejected(lambda: self.directo(route, method="post"))
        self.assertEqual(self.snapshot(), before)

    def test_creator_only_success_returns_to_authorized_composer_without_read_grants(self):
        self.asignar_rol(puede_crear_proveedores=True)
        before = self.financial_snapshot()
        result = self.client.post(self.url(self.ROUTES[2]), self.datos())
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.url, self.url(self.ROUTES[2]))
        self.assertEqual(self.client.get(result.url).status_code, 200)
        self.assert_rejected(lambda: self.directo())
        self.assertEqual(self.financial_snapshot(), before)

    def test_editor_only_success_returns_to_own_edit_without_history_or_list_grants(self):
        self.asignar_rol(puede_editar_proveedores=True)
        before = self.financial_snapshot()
        result = self.client.post(self.url(self.ROUTES[3]), self.datos())
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.url, self.url(self.ROUTES[3]))
        self.assertEqual(self.client.get(result.url).status_code, 200)
        self.assert_rejected(lambda: self.directo(self.ROUTES[0]))
        self.assertEqual(self.financial_snapshot(), before)

    def test_writer_composers_and_invalid_posts_never_query_purchase_or_financial_history(self):
        for permission, route in (("puede_crear_proveedores", self.ROUTES[2]), ("puede_editar_proveedores", self.ROUTES[3])):
            self.asignar_rol(**{permission: True})
            for method in ("get", "post"):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.url(route)) if method == "get" else self.client.post(self.url(route), self.datos(correo="invalid-mail", nombre=""))
                self.assertEqual(response.status_code, 200)
                self.assert_no_history_queries(queries)

    def test_invalid_post_preserves_all_ten_fields_and_never_modifies_any_record(self):
        data = self.datos(correo="invalid-mail", dias_credito="-1")
        before = self.snapshot()
        for route in self.ROUTES[2:]:
            page = self.client.post(self.url(route), data)
            self.assertEqual(page.status_code, 200)
            self.assertTemplateUsed(page, "facturacion/mobile_supplier_form.html")
            self.assertTrue(page.context["form"].is_bound)
            self.assertTrue(page.context["form"].errors)
            for name in self.FIELDS:
                self.assertEqual(page.context["form"].data.get(name), data[name])
        self.assertEqual(self.snapshot(), before)

    def test_model_validation_error_on_create_and_edit_is_a_bound_app_error_without_writes(self):
        before = self.snapshot()
        for route in self.ROUTES[2:]:
            with patch.object(Proveedor, "save", side_effect=ValidationError({"nombre": ["Synthetic model validation error"]})):
                page = self.client.post(self.url(route), self.datos())
            self.assertEqual(page.status_code, 200)
            self.assertIn("Synthetic model validation error", page.context["form"].errors["nombre"])
            self.assertEqual(page.context["form"].data["direccion"], self.datos()["direccion"])
        self.assertEqual(self.snapshot(), before)

    def test_valid_create_all_four_saves_same_profile_fields_only_with_original_actor_audit(self):
        for empresa in self.empresas.values():
            before = self.financial_snapshot()
            data = self.datos(nombre=f"Nuevo proveedor {empresa.slug}")
            page = self.client.post(self.url(self.ROUTES[2], empresa), data)
            self.assertEqual(page.status_code, 302)
            self.assertEqual(page.url, self.url(self.ROUTES[0], empresa))
            saved = Proveedor.objects.get(empresa=empresa, nombre=data["nombre"])
            for name in self.FIELDS:
                expected = True if name == "activo" else 30 if name == "dias_credito" else data[name]
                self.assertEqual(getattr(saved, name), expected)
            self.assertTrue(RegistroAuditoria.objects.filter(modelo="proveedor", objeto_id=str(saved.pk), accion="crear", usuario=self.usuario, empresa=empresa).exists())
            self.assertEqual(self.financial_snapshot(), before)

    def test_valid_edit_preserves_hidden_financial_links_created_date_and_other_tenant_profiles(self):
        old = Proveedor.objects.get(pk=self.proveedor.pk)
        hidden = (old.empresa_id, old.cliente_contable_id, old.cuenta_contable_id, old.cuenta_habitual_id, old.cuenta_habitual_por_id, old.cuenta_habitual_en, old.fecha_creacion)
        foreign = list(Proveedor.objects.exclude(empresa=self.empresa).order_by("pk").values())
        before = self.financial_snapshot()
        self.assertEqual(self.client.post(self.url(self.ROUTES[3]), self.datos()).status_code, 302)
        saved = Proveedor.objects.get(pk=old.pk)
        self.assertEqual((saved.empresa_id, saved.cliente_contable_id, saved.cuenta_contable_id, saved.cuenta_habitual_id, saved.cuenta_habitual_por_id, saved.cuenta_habitual_en, saved.fecha_creacion), hidden)
        self.assertEqual(saved.direccion, self.datos()["direccion"])
        self.assertEqual(list(Proveedor.objects.exclude(empresa=self.empresa).order_by("pk").values()), foreign)
        self.assertEqual(self.financial_snapshot(), before)

    def test_original_defaults_and_contado_credito_normalization_are_not_replaced(self):
        data = self.datos(condicion_pago="contado", dias_credito="45")
        self.assertEqual(self.client.post(self.url(self.ROUTES[2]), data).status_code, 302)
        self.assertEqual(Proveedor.objects.get(nombre=data["nombre"]).dias_credito, 0)
        data = self.datos(nombre="Proveedor defaults QA")
        data.pop("condicion_pago")
        data.pop("dias_credito")
        self.assertEqual(self.client.post(self.url(self.ROUTES[2]), data).status_code, 302)
        saved = Proveedor.objects.get(nombre=data["nombre"])
        self.assertEqual((saved.condicion_pago, saved.dias_credito), ("contado", 0))
        self.assertEqual(self.client.post(self.url(self.ROUTES[3]), data).status_code, 302)
        saved = Proveedor.objects.get(pk=self.proveedor.pk)
        self.assertEqual((saved.condicion_pago, saved.dias_credito), ("credito", 15))

    def test_credit_days_empty_model_error_is_preserved_without_app_edit_server_error(self):
        before = self.snapshot()
        for route in self.ROUTES[2:]:
            page = self.client.post(self.url(route), self.datos(dias_credito=""))
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.context["form"].errors)
            self.assertEqual(page.context["form"].data["dias_credito"], "")
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_names_and_rtn_remain_allowed_by_original_model_without_new_uniqueness_rule(self):
        data = self.datos(nombre=self.proveedor.nombre, rtn=self.proveedor.rtn)
        self.assertEqual(self.client.post(self.url(self.ROUTES[2]), data).status_code, 302)
        self.assertEqual(Proveedor.objects.filter(empresa=self.empresa, nombre=data["nombre"], rtn=data["rtn"]).count(), 2)

    def test_edit_audit_has_original_old_new_values_user_company_timestamp_and_path(self):
        start = timezone.now()
        self.assertEqual(self.client.post(self.url(self.ROUTES[3]), self.datos()).status_code, 302)
        audit = RegistroAuditoria.objects.get(modelo="proveedor", objeto_id=str(self.proveedor.pk), accion="modificar")
        self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (self.usuario.pk, self.empresa.pk, "POST"))
        self.assertGreaterEqual(audit.fecha, start)
        self.assertEqual(audit.cambios["nombre"], {"anterior": self.proveedor.nombre, "nuevo": self.datos()["nombre"]})
        self.assertIn("proveedores", audit.ruta)

    def test_unflagged_web_create_edit_keep_original_save_defaults_and_redirects(self):
        data = self.datos(nombre="Proveedor web QA")
        data.pop("app")
        create = self.client.post(self.url(self.ROUTES[2], mobile=False), data)
        self.assertEqual(create.url, self.url(self.ROUTES[0], mobile=False))
        edit = self.client.post(self.url(self.ROUTES[3], mobile=False), data)
        self.assertEqual(edit.url, self.url(self.ROUTES[0], mobile=False))
        self.assertEqual(Proveedor.objects.get(pk=self.proveedor.pk).nombre, data["nombre"])

    def test_foreign_profile_and_cliente_contable_profiles_cannot_be_opened_or_edited(self):
        foreign = self.proveedores["medical_spa"]
        for route in (self.ROUTES[1], self.ROUTES[3]):
            self.assert_rejected(lambda: self.directo(route, proveedor=foreign))
        external = Empresa.objects.create(nombre="Administradora QA", slug="dubon_asociados", rtn="MSUP-DUBON")
        client = ClienteContable.objects.create(empresa=external, nombre="Cliente contable externo QA")
        Proveedor.objects.filter(pk=self.proveedor.pk).update(cliente_contable=client)
        for route in (self.ROUTES[1], self.ROUTES[3]):
            self.assert_rejected(lambda: self.directo(route))
        listing = self.client.get(self.url(self.ROUTES[0]))
        self.assertNotIn(self.proveedor.pk, [row.pk for row in listing.context["proveedores"]])

    def test_list_validates_hidden_financial_links_before_search_or_pagination(self):
        Proveedor.objects.filter(pk=self.proveedor.pk).update(cuenta_contable_id=self.cuentas["medical_spa"].cuenta_contable_id)
        before = self.snapshot()
        for query in ({"q": "No coincide"}, {"page": "100"}, {"estado": "inactivos"}):
            self.assert_rejected(lambda: self.directo(self.ROUTES[0], url=self.url(self.ROUTES[0], **query)))
        self.assertEqual(self.snapshot(), before)

    def test_hidden_supplier_accounts_and_nullable_actor_orphans_are_denied_before_edit_save(self):
        external = Empresa.objects.create(nombre="Administradora cuentas QA", slug="dubon_asociados", rtn="MSUP-DUBON-HAB")
        client = ClienteContable.objects.create(empresa=external, nombre="Cliente habitual ajeno QA")
        habitual = CuentaAcumuladoCompra.objects.create(cliente_contable=client, nombre="Cuenta habitual ajena", grupo="gasto")
        for field, value in (("cuenta_contable_id", self.cuentas["medical_spa"].cuenta_contable_id), ("cuenta_contable_id", 2147483647), ("cuenta_habitual_id", habitual.pk), ("cuenta_habitual_id", 2147483647), ("cuenta_habitual_por_id", 2147483647)):
            original = getattr(self.proveedor, field)
            with connection.constraint_checks_disabled():
                try:
                    Proveedor.objects.filter(pk=self.proveedor.pk).update(**{field: value})
                    before = self.snapshot()
                    for route in (self.ROUTES[0], self.ROUTES[1], self.ROUTES[3]):
                        self.assert_rejected(lambda: self.directo(route))
                    self.assert_rejected(lambda: self.directo(self.ROUTES[3], method="post"))
                    self.assertEqual(self.snapshot(), before)
                finally:
                    Proveedor.objects.filter(pk=self.proveedor.pk).update(**{field: original})

    def test_detail_rejects_imported_foreign_company_purchase_linked_to_current_supplier(self):
        CompraInventario.objects.filter(pk=self.compras[self.empresa.slug].pk).update(empresa=self.empresas["medical_spa"])
        before = self.snapshot()
        self.assert_rejected(lambda: self.directo())
        self.assertEqual(self.snapshot(), before)

    def test_purchase_lines_reject_foreign_or_orphan_products_and_orphan_global_tax(self):
        line = self.lineas[self.empresa.slug]
        for field, value in (("producto_id", self.productos["medical_spa"].pk), ("producto_id", 2147483647), ("impuesto_id", 2147483647)):
            original = getattr(line, field)
            with connection.constraint_checks_disabled():
                try:
                    LineaCompraInventario.objects.filter(pk=line.pk).update(**{field: value})
                    before = self.snapshot()
                    self.assert_rejected(lambda: self.directo())
                    self.assertEqual(self.snapshot(), before)
                finally:
                    LineaCompraInventario.objects.filter(pk=line.pk).update(**{field: original})
        tax = TipoImpuesto.objects.create(nombre="Otro impuesto GLOBAL", porcentaje=Decimal("18"))
        LineaCompraInventario.objects.filter(pk=line.pk).update(impuesto=tax)
        self.assertFalse(any(field.name == "empresa" for field in TipoImpuesto._meta.fields))
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_purchase_and_payment_accounts_check_own_contable_orphans_before_original_totals(self):
        for model, record, field in ((CompraInventario, self.compras[self.empresa.slug], "cuenta_financiera_pago_id"), (PagoCompra, self.pagos[self.empresa.slug], "cuenta_financiera_id")):
            original = getattr(record, field)
            for value in (self.cuentas["medical_spa"].pk, 2147483647):
                with connection.constraint_checks_disabled():
                    try:
                        model.objects.filter(pk=record.pk).update(**{field: value})
                        self.assert_rejected(lambda: self.directo())
                    finally:
                        model.objects.filter(pk=record.pk).update(**{field: original})
        original = self.cuentas[self.empresa.slug].cuenta_contable_id
        with connection.constraint_checks_disabled():
            try:
                CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(cuenta_contable_id=2147483647)
                self.assert_rejected(lambda: self.directo())
            finally:
                CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(cuenta_contable_id=original)

    def test_historical_inactive_accounts_and_multicompany_actor_are_legitimate_without_reactivation(self):
        actor = Usuario.objects.create_user(username="supplier-multi-company-actor", empresa=self.empresas["medical_spa"])
        Proveedor.objects.filter(pk=self.proveedor.pk).update(activo=False, cuenta_habitual_por=actor, cuenta_habitual_en=timezone.now())
        CuentaContable.objects.filter(pk=self.proveedor.cuenta_contable_id).update(activa=False)
        CuentaFinanciera.objects.filter(pk=self.cuentas[self.empresa.slug].pk).update(activa=False)
        before = self.snapshot()
        for route in (self.ROUTES[0], self.ROUTES[1], self.ROUTES[3]):
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        CompraInventario.objects.filter(pk=self.compras[self.empresa.slug].pk).update(cuenta_financiera_pago=None)
        PagoCompra.objects.filter(pk=self.pagos[self.empresa.slug].pk).update(cuenta_financiera=None)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_detail_preserves_all_seven_original_aggregates_for_all_purchases_not_only_twenty_cards(self):
        for index in range(23):
            estado = ("aplicada", "borrador", "anulada")[index % 3]
            compra = self.new_purchase(Decimal(index+2), estado=estado, fecha=date(2026, 9, 1))
            if estado == "aplicada":
                PagoCompra.objects.create(compra=compra, monto=Decimal("1"))
        before = self.snapshot()
        app = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(app.context["resumen"], web.context["resumen"])
        self.assertEqual(set(app.context["resumen"]), {"compras", "aplicadas", "borradores", "monto", "saldo_pendiente", "por_pagar", "vencidas"})
        self.assertEqual(app.context["resumen"]["compras"], 24)
        self.assertEqual(len(app.context["compras"]), 20)
        self.assertEqual([row.pk for row in app.context["compras"]], [row.pk for row in web.context["compras"]])
        self.assertEqual(self.snapshot(), before)

    def test_original_total_line_fallback_and_balance_properties_are_used_without_rewriting_rows(self):
        LineaCompraInventario.objects.filter(pk=self.lineas[self.empresa.slug].pk).update(subtotal=Decimal("0"), impuesto_monto=Decimal("0"), cantidad=Decimal("2"), costo_unitario=Decimal("22"))
        before = self.snapshot()
        app = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(app.context["resumen"], web.context["resumen"])
        self.assertEqual(app.context["resumen"]["monto"], Decimal("44"))
        self.assertEqual(app.context["resumen"]["saldo_pendiente"], Decimal("34"))
        self.assertEqual(self.snapshot(), before)

    def test_detail_links_only_authorized_mobile_purchase_and_never_payment_web(self):
        self.asignar_rol(puede_proveedores=True, puede_compras=True, puede_registrar_pagos_proveedores=True)
        page = self.client.get(self.url())
        purchase = self.compras[self.empresa.slug]
        detail_url = reverse("ver_compra", args=[self.empresa.slug, purchase.pk])+"?app=1"
        pdf_url = reverse("descargar_compra_pdf", args=[self.empresa.slug, purchase.pk])+"?app=1"
        self.assertTrue(page.context["can_view_purchase"])
        self.assertEqual(page.context["mobile_supplier_purchase_items"][0], {"compra": purchase, "detalle_url": detail_url, "pdf_url": pdf_url})
        self.assertContains(page, f'href="{detail_url}"')
        self.assertContains(page, f'href="{pdf_url}"')
        self.assertNotContains(page, f'href="{reverse("ver_compra", args=[self.empresa.slug, purchase.pk])}"')
        self.assertNotContains(page, reverse("registrar_pago_compra", args=[self.empresa.slug, self.compras[self.empresa.slug].pk]))
        self.assertNotContains(page, "<table")

    def test_search_filters_all_original_fields_before_pagination_with_original_state_and_summary(self):
        for query in (self.proveedor.nombre.lower(), self.proveedor.rtn.lower(), self.proveedor.contacto.lower(), self.proveedor.ciudad.lower()):
            result = self.client.get(self.url(self.ROUTES[0], q=" "+query+" "))
            self.assertIn(self.proveedor.pk, [row.pk for row in result.context["proveedores"]])
        for index in range(23):
            Proveedor.objects.create(empresa=self.empresa, nombre=f"Filtro proveedor {index:02d}", activo=True)
        for index in range(22):
            Proveedor.objects.create(empresa=self.empresa, nombre=f"No coincide {index:02d}", activo=False)
        result = self.client.get(self.url(self.ROUTES[0], q="Filtro proveedor", estado="activos", page="2"))
        self.assertEqual(result.context["proveedores"].paginator.count, 23)
        self.assertEqual(len(result.context["proveedores"]), 3)
        self.assertEqual(result.context["resumen"], {"total": 23, "activos": 23, "inactivos": 0, "con_rtn": 0})

    def test_pagination_is_twenty_stable_name_id_and_preserves_app_search_and_state(self):
        docs = [Proveedor.objects.create(empresa=self.empresa, nombre="Proveedor empate", activo=True) for _ in range(23)]
        first = self.client.get(self.url(self.ROUTES[0], q="Proveedor empate", estado="activos"))
        self.assertEqual([row.pk for row in first.context["proveedores"]], [row.pk for row in docs[:20]])
        self.assertEqual(first.context["proveedores"].paginator.per_page, 20)
        next_url = first.context["mobile_supplier_next_url"]
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["Proveedor empate"], "estado": ["activos"], "page": ["2"]})
        second = self.client.get(next_url)
        self.assertEqual([row.pk for row in second.context["proveedores"]], [row.pk for row in docs[20:]])

    def test_closed_mobile_metadata_duplicates_files_and_unexpected_financial_fields_deny_without_writes(self):
        before = self.snapshot()
        for route in self.ROUTES:
            for query in ("app=1&app=1", "app=1&empresa=1", "app=1&next=/", "app=1&accion=eliminar"):
                self.assert_rejected(lambda: self.directo(route, url=self.url(route).split("?")[0]+"?"+query))
        for route in self.ROUTES[2:]:
            for key in ("app", *self.FIELDS):
                data = QueryDict(mutable=True)
                data.update(self.datos())
                data.setlist(key, [data[key], data[key]])
                self.assert_rejected(lambda: self.directo(route, method="post", data=data))
            for key in ("empresa", "proveedor_id", "cuenta_contable", "cliente_contable", "cuenta_habitual", "creado_por", "next", "metodo_pago", "initial-nombre"):
                self.assert_rejected(lambda: self.directo(route, method="post", data=self.datos(**{key: "1"})))
            self.assert_rejected(lambda: self.directo(route, method="post", data=self.datos(archivo=SimpleUploadedFile("unexpected.txt", b"unexpected"))))
            self.assert_rejected(lambda: self.directo(route, method="post", url=self.url(route, app="0")))
        self.assertEqual(self.snapshot(), before)

    def test_ids_pages_and_list_filter_metadata_are_canonical_and_unknown_verbs_cannot_save(self):
        before = self.snapshot()
        for route in self.ROUTES[1::2]:
            original = self.url(route)
            for value in ("0", "00"+str(self.proveedor.pk), "9223372036854775808"):
                self.assert_rejected(lambda: self.directo(route, url=original.replace(f"/proveedores/{self.proveedor.pk}/", f"/proveedores/{value}/")))
        for value in ("", "0", "01", "-1", "1e2", "١", "9223372036854775808"):
            self.assert_rejected(lambda: self.directo(self.ROUTES[0], url=self.url(self.ROUTES[0], page=value)))
        for query in ("app=1&q=a&q=b", "app=1&estado=unknown", "app=1&page=1&page=1"):
            self.assert_rejected(lambda: self.directo(self.ROUTES[0], url=self.url(self.ROUTES[0]).split("?")[0]+"?"+query))
        for route in self.ROUTES:
            for method in ("put", "delete", "patch"):
                self.assert_rejected(lambda: self.directo(route, method=method))
        self.assertEqual(self.snapshot(), before)

    def test_writer_only_hub_and_metadata_are_safe_and_do_not_query_or_grant_private_data(self):
        client = Cliente.objects.create(empresa=self.empresa, nombre="Cliente privacidad proveedores QA", rtn="0801199900181")
        invoice = Factura.objects.create(empresa=self.empresa, cliente=client, estado="borrador", subtotal=Decimal("100"), total=Decimal("100"), total_lempiras=Decimal("100"))
        for granular in (True, False):
            for permission in ("puede_crear_proveedores", "puede_editar_proveedores"):
                self.asignar_rol(granular=granular, **{permission: True})
                for route in ("agenda_mobile_modules_screen", "agenda_mobile_modules"):
                    with CaptureQueriesContext(connection) as queries:
                        response = self.client.get(reverse(route, args=[self.empresa.slug]))
                    self.assertEqual(response.status_code, 200)
                    self.assert_no_history_queries(queries)
                    self.assertFalse(any(f'"{Proveedor._meta.db_table}"' in row["sql"] for row in queries))
                    self.assertNotIn(self.proveedor.nombre, response.content.decode())
                    self.assertNotIn(client.nombre, response.content.decode())
                    if route == "agenda_mobile_modules":
                        self.assertEqual(response.json()["inicio_url"], reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
                        cards = {row["key"]: row for row in response.json()["modulos"]}
                        if permission == "puede_editar_proveedores":
                            self.assertEqual((cards["proveedores"]["estado"], cards["proveedores"]["url"]), ("pendiente", ""))
                        else:
                            self.assertEqual(cards["proveedores"]["url"], self.url(self.ROUTES[2]))
                        for forbidden in ("compras", "egresos", "recibos", "pacientes", "agenda"):
                            self.assertNotIn(forbidden, cards)
                    else:
                        self.assertNotContains(response, 'rel="manifest"')
                before = self.snapshot()
                for route in ("agenda_mobile", "agenda_citas", "agenda_mobile_inventory"):
                    self.assertIn(self.client.get(reverse(route, args=[self.empresa.slug])).status_code, (302, 403, 404))
                self.assertIn(self.client.get(reverse("ver_factura", args=[self.empresa.slug, invoice.pk])+"?app=1").status_code, (302, 403))
                self.assertEqual(self.snapshot(), before)

    def test_csrf_and_logout_keep_original_protection_without_extra_permission_grants(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url(self.ROUTES[3])).status_code, 200)
        self.assertIn(protected.post(self.url(self.ROUTES[3]), self.datos()).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(protected.post(self.url(self.ROUTES[3]), self.datos(), HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(self.url(self.ROUTES[2])).status_code, 302)

    def test_profile_text_is_escaped_and_other_company_rows_never_leak_to_search(self):
        Proveedor.objects.filter(pk=self.proveedor.pk).update(nombre="<script>supplier_synthetic()</script>")
        page = self.client.get(self.url())
        self.assertContains(page, "&lt;script&gt;supplier_synthetic()&lt;/script&gt;")
        self.assertNotContains(page, "<script>supplier_synthetic()")
        listing = self.client.get(self.url(self.ROUTES[0], q=self.proveedores["medical_spa"].nombre))
        self.assertEqual(listing.context["proveedores"].paginator.count, 0)
