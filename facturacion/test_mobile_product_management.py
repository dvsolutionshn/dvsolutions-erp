"""Independent synthetic integration checks for original product CRUD in APP.

The original dynamic ProductoForm and its transactions remain authoritative.
Prepared reads are immutable; explicitly missing configuration may initialize.
"""
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.db.models import Sum
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .forms import EliminarProductoForm, ProductoForm
from .models import BitacoraProductoEliminado, BodegaInventario, CategoriaProductoFarmaceutico, ExistenciaLoteBodega, InventarioProducto, LoteInventario, MovimientoInventario, MovimientoLoteBodega, PerfilFarmaceuticoProducto, Producto, TipoImpuesto


class _ProductManagementMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.form_id = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form": self.form_id = values.get("id")
        values["_form"] = self.form_id
        self.nodes.append((tag, values))

    def handle_endtag(self, tag):
        if tag == "form": self.form_id = None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class MobileProductManagementIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    ROUTES = {"create": "crear_producto_facturacion", "edit": "editar_producto_facturacion", "delete": "eliminar_producto_facturacion"}
    BUSINESS_ALLOWED = {"facturacion.producto", "facturacion.perfilfarmaceuticoproducto", "facturacion.historialcostorealproducto", "facturacion.inventarioproducto", "facturacion.loteinventario", "facturacion.existencialotebodega", "facturacion.movimientolotebodega", "facturacion.movimientoinventario", "facturacion.bitacoraproductoeliminado", "core.registroauditoria"}

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.billing, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Productos móviles QA"})
            cls.clinical, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica productos QA"})
            cls.tax = TipoImpuesto.objects.create(nombre="Impuesto producto global QA", porcentaje=Decimal("15"), activo=True)
            cls.empresas, cls.productos, cls.categorias, cls.bodegas, cls.configs = {}, {}, {}, {}, {}
            for index, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Gestión productos QA {slug}", slug=slug, rtn=f"MPM-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company
                for module in (cls.billing, cls.clinical): EmpresaModulo.objects.create(empresa=company, modulo=module, activo=True)
                cls.configs[slug] = ConfiguracionAvanzadaEmpresa.objects.create(empresa=company, usa_bodegas_internas=True, usa_inventario_farmaceutico=True)
                names = (("Bodega Principal", "principal"), ("Bodega Clinica", "provisional")) if slug == "luque_aestetic" else (("Bodega General", "principal"), ("Bodega Hospital", "provisional"), ("Vitrina", "vitrina")) if slug in {"hospital_mia", "medical_spa"} else (("Bodega principal", "principal"), ("Bodega provisional", "provisional"), ("Vitrina", "vitrina"))
                cls.bodegas[slug] = [BodegaInventario.objects.create(empresa=company, nombre=name, tipo=kind) for name, kind in names]
                cls.categorias[slug] = CategoriaProductoFarmaceutico.objects.create(empresa=company, nombre=f"Categoría gestión {slug}")
                product = Producto.objects.create(empresa=company, nombre=f"Producto histórico {slug}", codigo=f"MPM-{index}-OLD", precio=Decimal("115.00"), costo_real_inventario=Decimal("12.3456"), controla_inventario=True, impuesto_predeterminado=cls.tax, descripcion="Descripción anterior", fecha_referencia="2027-08-31", fecha_alerta="2027-07-01", nota_fecha="Nota histórica")
                cls.productos[slug] = product
                PerfilFarmaceuticoProducto.objects.create(empresa=company, producto=product, categoria=cls.categorias[slug], principio_activo="Principio anterior", presentacion="Caja anterior", concentracion="10 mg", laboratorio="Laboratorio QA", registro_sanitario="REG-QA", requiere_receta=True, producto_controlado=True, alerta_vencimiento_dias=45)
                lot = LoteInventario.objects.create(empresa=company, producto=product, numero_lote=f"MPM-OLD-{index}", fecha_vencimiento="2027-08-31")
                for number, warehouse in enumerate(cls.bodegas[slug], 1): ExistenciaLoteBodega.objects.create(empresa=company, lote=lot, bodega=warehouse, cantidad=Decimal(number))
                InventarioProducto.objects.create(empresa=company, producto=product, existencias=sum(Decimal(number) for number in range(1, len(cls.bodegas[slug])+1)))
            cls.empresa = cls.empresas["hospital_mia"]; cls.producto = cls.productos[cls.empresa.slug]
            cls.granular = RolSistema.objects.create(nombre="Gestión productos granular QA", codigo="mpm-granular", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_productos=True, puede_crear_productos=True, puede_editar_productos=True)
            cls.legacy = RolSistema.objects.create(nombre="Gestión productos legado QA", codigo="mpm-legacy", activo=True, puede_productos=True, puede_crear_productos=True, puede_editar_productos=True)
            cls.usuario = Usuario.objects.create_user(username="mpm-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.granular)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Gestión producto externa QA", slug="mpm_externa", rtn="MPM-EXT", tipo_solucion="erp", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.billing)
            cls.usuario.empresas_acceso.add(cls.externa)
            cls.producto_externo = Producto.objects.create(empresa=cls.externa, nombre="Producto ajeno MPM", codigo="MPM-EXT-P", precio=Decimal("20"))
            cls.categoria_externa = CategoriaProductoFarmaceutico.objects.create(empresa=cls.externa, nombre="Categoría ajena MPM")
            cls.bodega_externa = BodegaInventario.objects.create(empresa=cls.externa, nombre="Bodega ajena MPM")

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW); clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url()  # Resolve metadata before transport blockers, never invoke a view.
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden product transport: {target}")); mock = blocker.start()
            self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def url(self, action="create", company=None, product=None, mobile=True, **query):
        company = company or self.empresa; product = product or self.productos.get(company.slug) or self.producto_externo
        args = [company.slug] + ([] if action == "create" else [product.pk])
        base = reverse(self.ROUTES[action], args=args); params = {"app": "1", **query} if mobile else query
        return base + ("?"+urlencode(params, doseq=True) if params else "")

    def data(self, company=None, product=None, **updates):
        company = company or self.empresa
        form = ProductoForm(instance=product, empresa=company) if product else ProductoForm(empresa=company)
        values = {name: "" for name in form.fields}
        values.update(app="1", nombre=f"Producto nuevo sintético {company.slug}", codigo=f"MPM-NEW-{company.pk}", tipo_item="producto", unidad_medida="caja", precio="115.00", controla_inventario="on", activo="on", impuesto_predeterminado=str(self.tax.pk), descripcion="Texto sintético <b>literal</b>", fecha_referencia="2027-08-31", fecha_alerta="2027-07-01", nota_fecha="Nota registrada")
        optional = {"costo_real_inventario": "12.3456", "categoria_farmaceutica": str(self.categorias[company.slug].pk), "principio_activo": "Principio QA", "presentacion": "Caja QA", "concentracion": "10 mg", "laboratorio": "Laboratorio QA", "registro_sanitario": "REG-QA", "requiere_receta": "on", "requiere_refrigeracion": "on", "producto_controlado": "on", "alerta_vencimiento_dias": "45", "lote_inicial": "MPM-INITIAL", "vencimiento_lote_inicial": "2027-08-31"}
        values.update({key: value for key, value in optional.items() if key in form.fields})
        for warehouse, name in form.bodega_stock_fields: values[name] = str(form.fields[name].initial or Decimal("0"))
        values.update(updates); return values

    def role(self, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Gestión productos rol aislado", codigo=f"mpm-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete(); self.usuario.refresh_from_db(); return role

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda item: item._meta.label_lower)}

    def changes(self, before):
        after = self.snapshot(); return {label for label in before if before[label] != after[label]}

    def no_writes(self, captured):
        self.assertEqual([row["sql"] for row in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])

    def request(self, action="create", company=None, product=None, method="get", data=None, target=None, body=None):
        factory = RequestFactory(); target = target or self.url(action, company, product)
        request = factory.generic(method.upper(), target, data=body, content_type="application/json") if body is not None else getattr(factory, method)(target, data=data or {}) if method in {"get", "head", "post"} else factory.generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk); return request

    def direct(self, request, action="create", company=None, product_id=None):
        company = company or self.empresa; callback = {"create": views.crear_producto, "edit": views.editar_producto, "delete": views.eliminar_producto}[action].__wrapped__
        return callback(request, company.slug) if action == "create" else callback(request, company.slug, product_id or self.producto.pk)

    def denied(self, action="create", method="get", data=None, target=None, request=None, product_id=None, exception=(PermissionDenied, Http404), early=True):
        request = request or self.request(action, method=method, data=data, target=target); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(exception): self.direct(request, action, product_id=product_id)
        self.no_writes(captured); self.assertEqual(self.snapshot(), before)
        if early: self.assertFalse(any('"facturacion_producto"' in row["sql"].lower() for row in captured))

    def immutable(self, action="create", company=None, method="get", data=None):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured: response = getattr(self.client, method)(self.url(action, company), data=data or {})
        self.no_writes(captured); self.assertEqual(self.snapshot(), before); return response

    def assert_app_form(self, response, bound=False):
        self.assertEqual(response.status_code, 200); self.assertIsInstance(response.context["form"], ProductoForm)
        self.assertEqual(response.context["form"].is_bound, bound); self.assertIn("no-store", response["Cache-Control"])

    def test_four_companies_both_roles_create_and_edit_reads(self):
        for role in (self.granular, self.legacy):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                for action in ("create", "edit"):
                    self.assert_app_form(self.immutable(action, company)); head = self.immutable(action, company, "head")
                    self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")

    def test_create_permission_independent_from_product_read_and_edit(self):
        for granular in (True, False):
            self.role(granular, puede_crear_productos=True); self.assert_app_form(self.immutable())
            self.denied("edit"); self.denied("delete", "post", {"app": "1", "motivo": "Motivo sintético válido"})

    def test_edit_permission_independent_from_create_and_read(self):
        for granular in (True, False):
            self.role(granular, puede_editar_productos=True); self.assert_app_form(self.immutable("edit")); self.denied()

    def test_delete_requires_original_products_not_create_or_edit(self):
        for flag in ("puede_crear_productos", "puede_editar_productos", "puede_inventario", "puede_ver_calendario"):
            self.role(**{flag: True}); self.denied("delete", "post", {"app": "1", "motivo": "Motivo válido sintético"})

    def test_unrelated_clinical_financial_flags_do_not_grant_product_forms(self):
        self.role(puede_configuracion_clinica=True, puede_crear_facturas=True, puede_inventario=True)
        self.denied(); self.denied("edit")

    def test_local_none_denies_without_global_fallback(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        self.denied(); self.denied("edit")

    def test_local_override_is_company_specific(self):
        self.role(puede_productos=True); UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=self.granular)
        self.assert_app_form(self.immutable()); request = self.request(company=self.empresas["medical_spa"])
        before = self.snapshot()
        with self.assertRaises(PermissionDenied): self.direct(request, company=self.empresas["medical_spa"])
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_and_inactive_real_http_cannot_seed(self):
        request = self.request(); request.user = AnonymousUser(); self.denied(request=request)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_active=False); self.assertEqual(self.immutable().status_code, 302)

    def test_membership_active_profile_module_and_license_checked(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(empresa=self.externa); self.usuario.empresas_acceso.clear(); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(empresa=self.empresa)
        for values in ({"activa": False}, {"tipo_solucion": "erp"}, {"estado_licencia": "suspendida"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values); self.denied(); Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.billing).update(activo=False); self.denied()

    def test_original_admin_bypass_and_super_license_exception_not_membership_widening(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=None, es_administrador_empresa=True); self.assert_app_form(self.immutable())
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida"); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True); self.assert_app_form(self.immutable())

    def test_get_query_app_unique_closed_and_mixed_markers_denied(self):
        for suffix in ("&app=1", "&app=0", "&next=/", "&modal=1", "&quick_mode=1", "&empresa=1", "&page=1"):
            self.denied(target=self.url()+suffix)
        self.denied(target=self.url(app="0"))

    def test_post_requires_query_and_body_app_single_matching(self):
        for target, data in ((self.url(mobile=False), self.data()), (self.url(app="0"), self.data()), (self.url(), self.data(app="0")), (self.url(), self.data(app=["1", "1"]))): self.denied(method="post", target=target, data=data)

    def test_unknown_post_keys_and_retired_destinations_rejected_before_form(self):
        for key in ("empresa", "producto_id", "eliminado", "usuario", "costo_promedio", "next", "quick_mode", "accion", "stock_bodega_999999999"):
            self.denied(method="post", data={**self.data(), key: "1"})

    def test_scalar_duplicate_values_include_boolean_metadata_and_dynamic_stock(self):
        stock = f"stock_bodega_{self.bodegas[self.empresa.slug][0].pk}"
        for key in ("nombre", "codigo", "precio", "activo", "controla_inventario", "categoria_farmaceutica", "csrfmiddlewaretoken", stock): self.denied(method="post", data={**self.data(), key: ["1", "1"]})

    def test_unsupported_methods_and_get_head_raw_bodies_denied(self):
        for method in ("put", "patch", "delete", "options", "trace"): self.denied(method=method)
        for method in ("get", "head"): self.denied(request=self.request(method=method, body=b"{}"))

    def test_noncanonical_ids_before_product_lookup(self):
        for value in ("0", "00"+str(self.producto.pk), "-1", "１２", "9223372036854775808"):
            target = self.url("edit").replace(f"/{self.producto.pk}/editar/", f"/{value}/editar/")
            self.denied("edit", target=target, product_id=value)
            for field in ("categoria_farmaceutica", "impuesto_predeterminado"):
                self.denied(method="post", data=self.data(**{field: value}))

    def test_foreign_missing_or_eliminated_selected_product_404(self):
        for value in (self.producto_externo.pk, 999999999):
            target = self.url("edit").replace(f"/{self.producto.pk}/editar/", f"/{value}/editar/")
            self.denied("edit", target=target, product_id=value, early=False, exception=Http404)
        Producto.objects.filter(pk=self.producto.pk).update(eliminado=True); self.denied("edit", early=False, exception=Http404)

    def test_full_dynamic_form_names_stock_options_and_widgets_match_original(self):
        for company in self.empresas.values():
            response = self.immutable("edit", company); original = ProductoForm(instance=self.productos[company.slug], empresa=company)
            actual = response.context["form"]
            self.assertEqual(tuple(actual.fields), tuple(original.fields))
            for name in actual.fields:
                self.assertEqual(type(actual.fields[name]), type(original.fields[name])); self.assertEqual(type(actual.fields[name].widget), type(original.fields[name].widget))
            names = {attrs["name"] for tag, attrs in _ProductManagementMarkup(response.content.decode()).nodes if tag in {"input", "select", "textarea"} and attrs.get("name") and attrs.get("_form") == "mobileProductForm"}
            self.assertEqual(names, set(actual.fields)|{"app", "csrfmiddlewaretoken"})

    def test_cost_real_only_two_original_companies_and_final_price_label_four(self):
        for company in self.empresas.values():
            form = self.immutable(company=company).context["form"]
            self.assertEqual("costo_real_inventario" in form.fields, company.slug in {"hospital_mia", "medical_spa"})
            self.assertEqual(form.fields["precio"].label, "Precio final (impuesto incluido)")

    def test_profile_and_distribution_fields_disappear_only_original_configuration(self):
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(usa_bodegas_internas=False, usa_inventario_farmaceutico=False)
        form = self.immutable().context["form"]
        self.assertFalse(form.bodega_stock_fields); self.assertNotIn("lote_inicial", form.fields); self.assertNotIn("categoria_farmaceutica", form.fields)

    def test_bound_invalid_form_preserves_all_dynamic_values_without_writes(self):
        body = self.data(product=self.producto, nombre="", precio="NaN", descripcion="<script>synthetic</script>")
        response = self.immutable("edit", method="post", data=body); self.assert_app_form(response, True)
        for name, value in body.items(): self.assertEqual(response.context["form"].data[name], value)
        self.assertNotContains(response, "<script>synthetic</script>")

    def test_original_choice_and_decimal_validation_not_replaced(self):
        for values in ({"tipo_item": "servicio", "controla_inventario": "on"}, {"precio": "Infinity"}, {"costo_real_inventario": "-1"}, {"alerta_vencimiento_dias": "0"}):
            response = self.immutable(method="post", data=self.data(**values)); self.assert_app_form(response, True); self.assertTrue(response.context["form"].errors)

    def test_foreign_inactive_category_is_original_modelchoice_error(self):
        CategoriaProductoFarmaceutico.objects.filter(pk=self.categorias[self.empresa.slug].pk).update(activa=False)
        for value in (self.categorias[self.empresa.slug].pk, self.categoria_externa.pk):
            response = self.immutable(method="post", data=self.data(categoria_farmaceutica=str(value))); self.assert_app_form(response, True); self.assertIn("categoria_farmaceutica", response.context["form"].errors)

    def test_tax_global_choices_original_active_queryset_and_nullable_preserved(self):
        inactive = TipoImpuesto.objects.create(nombre="Impuesto inactivo MPM", porcentaje=Decimal("10"), activo=False)
        response = self.immutable(method="post", data=self.data(impuesto_predeterminado=str(inactive.pk))); self.assertIn("impuesto_predeterminado", response.context["form"].errors)
        response = self.client.post(self.url(), self.data(impuesto_predeterminado="")); self.assertEqual(response.status_code, 302)
        self.assertIsNone(Producto.objects.get(empresa=self.empresa, codigo=f"MPM-NEW-{self.empresa.pk}").impuesto_predeterminado_id)

    def test_valid_create_four_companies_both_roles_original_save_once_and_effects(self):
        original = ProductoForm.save
        for role_index, role in enumerate((self.granular, self.legacy)):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                body = self.data(company, codigo=f"MPM-MATRIX-{role_index}-{company.pk}")
                before = self.snapshot()
                with patch.object(ProductoForm, "save", autospec=True, side_effect=original) as save: response = self.client.post(self.url(company=company), body)
                self.assertEqual(response.status_code, 302); save.assert_called_once(); self.assertEqual(save.call_args.kwargs, {"commit": False})
                product = Producto.objects.get(empresa=company, codigo=body["codigo"]); self.assertEqual(product.precio, Decimal("115")); self.assertEqual(product.unidad_medida, "caja")
                self.assertTrue(self.changes(before) <= self.BUSINESS_ALLOWED)

    def test_valid_edit_uses_original_form_save_and_keeps_other_tenants(self):
        original_other = list(Producto.objects.exclude(empresa=self.empresa).order_by("id").values()); original = ProductoForm.save
        body = self.data(product=self.producto, codigo=self.producto.codigo, nombre="Producto editado sintético")
        with patch.object(ProductoForm, "save", autospec=True, side_effect=original) as save: response = self.client.post(self.url("edit"), body)
        self.assertEqual(response.status_code, 302); save.assert_called_once(); self.assertEqual(save.call_args.kwargs, {})
        self.assertEqual(list(Producto.objects.exclude(empresa=self.empresa).order_by("id").values()), original_other)

    def test_edit_complete_stock_submission_does_not_zero_existing_distribution(self):
        before = list(ExistenciaLoteBodega.objects.filter(lote__producto=self.producto).order_by("id").values("id", "cantidad"))
        response = self.client.post(self.url("edit"), self.data(product=self.producto, codigo=self.producto.codigo))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(ExistenciaLoteBodega.objects.filter(lote__producto=self.producto).order_by("id").values("id", "cantidad")), before)

    def test_omitting_existing_dynamic_stock_is_rejected_before_original_adjuster(self):
        for action in ("create", "edit"):
            body = self.data(product=self.producto if action == "edit" else None); body.pop(f"stock_bodega_{self.bodegas[self.empresa.slug][0].pk}")
            with patch.object(views, "_aplicar_distribucion_bodegas_producto", side_effect=AssertionError("Missing stock must not clear")): self.denied(action, "post", body, early=False)

    def test_distribution_uses_original_adjuster_and_general_sum(self):
        warehouse = self.bodegas[self.empresa.slug][0]; body = self.data(product=self.producto, codigo=self.producto.codigo, **{f"stock_bodega_{warehouse.pk}": "5.00"})
        with patch.object(views, "_aplicar_distribucion_bodegas_producto", wraps=views._aplicar_distribucion_bodegas_producto) as adjust: response = self.client.post(self.url("edit"), body)
        self.assertEqual(response.status_code, 302); adjust.assert_called_once(); self.producto.inventario.refresh_from_db()
        self.assertEqual(self.producto.inventario.existencias, Decimal("10"))
        self.assertEqual(ExistenciaLoteBodega.objects.filter(lote__producto=self.producto, bodega=warehouse).aggregate(total=Sum("cantidad"))["total"], Decimal("5"))

    def test_product_transaction_rolls_back_on_distribution_validation_error(self):
        before = self.snapshot()
        with patch.object(views, "_aplicar_distribucion_bodegas_producto", side_effect=ValidationError("Synthetic distribution failure")): response = self.client.post(self.url(), self.data())
        self.assert_app_form(response, True); self.assertEqual(self.snapshot(), before); self.assertTrue(response.context["form"].non_field_errors())

    def test_original_duplicate_code_company_validation_retained(self):
        response = self.immutable(method="post", data=self.data(codigo=self.producto.codigo)); self.assert_app_form(response, True); self.assertTrue(response.context["form"].errors)

    def test_imported_profile_company_and_category_denied_before_render_or_save(self):
        profile = PerfilFarmaceuticoProducto.objects.get(producto=self.producto)
        body = self.data(product=self.producto)
        for values in ({"empresa_id": self.externa.pk}, {"categoria_id": self.categoria_externa.pk}, {"categoria_id": 999999999}):
            PerfilFarmaceuticoProducto.objects.filter(pk=profile.pk).update(**values)
            try:
                self.denied("edit", early=False); self.denied("edit", "post", body, early=False)
            finally: PerfilFarmaceuticoProducto.objects.filter(pk=profile.pk).update(empresa=self.empresa, categoria=self.categorias[self.empresa.slug])
        Producto.objects.filter(pk=self.producto.pk).update(impuesto_predeterminado_id=999999999)
        try: self.denied("edit", early=False); self.denied("edit", "post", body, early=False)
        finally: Producto.objects.filter(pk=self.producto.pk).update(impuesto_predeterminado=self.tax)

    def test_imported_stock_relations_denied_before_join_or_adjustment(self):
        stock = ExistenciaLoteBodega.objects.filter(lote__producto=self.producto).first()
        for values in ({"empresa_id": self.externa.pk}, {"bodega_id": self.bodega_externa.pk}):
            old = {key: getattr(stock, key) for key in values}; ExistenciaLoteBodega.objects.filter(pk=stock.pk).update(**values)
            self.denied("edit", early=False); ExistenciaLoteBodega.objects.filter(pk=stock.pk).update(**old)

    def test_delete_method_get_head_is_original_405_and_no_write(self):
        for method in ("get", "head"):
            response = self.immutable("delete", method=method); self.assertEqual(response.status_code, 405)

    def test_invalid_delete_motive_original_form_and_no_product_changes(self):
        body = {"app": "1", "motivo": "corto"}; before = self.snapshot(); response = self.client.post(self.url("delete"), body)
        self.assertIn(response.status_code, (200, 302)); self.assertFalse(EliminarProductoForm(body).is_valid())
        self.assertEqual(self.changes(before)-{"sessions.session"}, set())

    def test_invalid_delete_bound_is_separate_from_unbound_original_editor(self):
        before = self.snapshot(); response = self.client.post(self.url("delete"), {"app": "1", "motivo": "corto"})
        self.assertEqual(response.status_code, 200); self.assertFalse(response.context["form"].is_bound)
        self.assertTrue(response.context["delete_form"].is_bound); self.assertTrue(response.context["delete_form"].errors)
        self.assertTrue(response.context["mobile_product_delete_bound"]); self.assertEqual(self.snapshot(), before)

    def test_write_only_prg_and_navigation_do_not_offer_unauthorized_catalogue(self):
        for action, permission in (("create", "puede_crear_productos"), ("edit", "puede_editar_productos")):
            self.role(**{permission: True}); body = self.data(product=self.producto if action == "edit" else None)
            if action == "edit": body["codigo"] = self.producto.codigo
            response = self.client.post(self.url(action), body); self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], self.url(action)); page = self.client.get(response["Location"])
            self.assertFalse(page.context["can_view_products"]); self.assertEqual(page.context["app_products_url"], "")

    def test_product_capability_pure_does_not_query_catalogues_or_grant_inventory(self):
        from .mobile_product_management import gestion_productos_disponible_app
        request = self.request(); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured: capabilities = gestion_productos_disponible_app(request, self.empresa)
        self.assertEqual(capabilities, {"ver": True, "crear": True, "editar": True, "eliminar": True})
        self.no_writes(captured); self.assertEqual(self.snapshot(), before); self.assertFalse(any('"facturacion_' in row["sql"].lower() for row in captured))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_inventario", self.empresa))

    def test_delete_closed_metadata_and_scalars_never_retarget(self):
        for body in ({"app": "1", "motivo": "Motivo válido sintético", "producto_id": str(self.producto_externo.pk)}, {"app": "1", "motivo": ["Motivo válido", "Motivo válido"]}): self.denied("delete", "post", body)

    def test_valid_baja_is_soft_preserves_stock_history_and_original_snapshot(self):
        self.role(puede_productos=True); before_stock = list(ExistenciaLoteBodega.objects.order_by("id").values()); before = self.snapshot()
        response = self.client.post(self.url("delete"), {"app": "1", "motivo": "  Baja sintética por cambio de catálogo  "}); self.assertEqual(response.status_code, 302)
        self.producto.refresh_from_db(); self.assertTrue(self.producto.eliminado); self.assertFalse(self.producto.activo); self.assertEqual(self.producto.eliminado_por_id, self.usuario.pk)
        record = BitacoraProductoEliminado.objects.get(producto=self.producto); self.assertEqual(record.usuario_id, self.usuario.pk); self.assertEqual(record.stock_al_momento, Decimal("6")); self.assertEqual(record.motivo, "Baja sintética por cambio de catálogo")
        self.assertEqual(list(ExistenciaLoteBodega.objects.order_by("id").values()), before_stock); self.assertEqual(self.changes(before), {"facturacion.producto", "facturacion.bitacoraproductoeliminado", "core.registroauditoria"})

    def test_global_audit_actor_and_company_original_for_create_edit_and_delete(self):
        response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
        product = Producto.objects.get(empresa=self.empresa, codigo=f"MPM-NEW-{self.empresa.pk}")
        audit = RegistroAuditoria.objects.filter(app_label="facturacion", modelo="producto", objeto_id=str(product.pk), accion="crear").latest("id")
        self.assertEqual(audit.usuario_id, self.usuario.pk); self.assertEqual(audit.empresa_id, self.empresa.pk)

    def test_missing_advanced_configuration_get_preserves_original_seed_explicitly(self):
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).delete(); before = self.snapshot(); response = self.client.get(self.url())
        self.assert_app_form(response); self.assertTrue(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())
        self.assertEqual(self.changes(before), {"core.configuracionavanzadaempresa", "core.registroauditoria"})

    def test_web_original_templates_modal_and_outside_scope_remain_original(self):
        for target, template in ((self.url(mobile=False), "facturacion/crear_producto.html"), (self.url(mobile=False, modal="1"), "facturacion/crear_producto_rapido_modal.html"), (self.url(company=self.externa), "facturacion/crear_producto.html")):
            response = self.client.get(target); self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, template)

    def test_no_public_camera_storage_or_unauthorized_financial_links_in_new_editor(self):
        response = self.immutable(); content = response.content.decode(); self.assertNotIn("localStorage", content); self.assertNotIn("getUserMedia", content)
        for _tag, attrs in _ProductManagementMarkup(content).nodes:
            if attrs.get("href"):
                self.assertNotIn("/contabilidad/", attrs["href"]); self.assertNotIn("/facturacion/crear/", attrs["href"])

    def test_only_single_original_photo_file_allowed_and_invalid_image_bound(self):
        for field in ("archivo", "logo", "empresa"):
            self.denied(method="post", data={**self.data(), field: SimpleUploadedFile("synthetic.txt", b"synthetic")})
        self.denied(method="post", data={**self.data(), "foto": [SimpleUploadedFile("a.gif", b"synthetic"), SimpleUploadedFile("b.gif", b"synthetic")]})
        response = self.immutable(method="post", data={**self.data(), "foto": SimpleUploadedFile("invalid.gif", b"not-an-image", content_type="image/gif")})
        self.assert_app_form(response, True); self.assertIn("foto", response.context["form"].errors)

    def test_original_photo_save_clear_widget_and_scalar_cardinality_in_memory(self):
        image = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        response = self.client.post(self.url(), {**self.data(), "foto": SimpleUploadedFile("synthetic.gif", image, content_type="image/gif")}); self.assertEqual(response.status_code, 302)
        product = Producto.objects.get(empresa=self.empresa, codigo=f"MPM-NEW-{self.empresa.pk}"); self.assertTrue(product.foto)
        target = self.url("edit", product=product); response = self.client.get(target)
        names = [attrs.get("name") for _tag, attrs in _ProductManagementMarkup(response.content.decode()).nodes]
        self.assertIn("foto-clear", names)
        self.denied("edit", "post", {**self.data(product=product), "foto-clear": ["on", "on"]}, target=target, product_id=product.pk, early=False)
        response = self.client.post(target, {**self.data(product=product, codigo=product.codigo), "foto-clear": "on"}); self.assertEqual(response.status_code, 302)
        product.refresh_from_db(); self.assertFalse(product.foto)

    def test_valid_csrf_and_failed_csrf_preserve_original_handler(self):
        from core.views import SESSION_EXPIRED_MESSAGE_KEY
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario); before = self.snapshot()
        response = strict.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("empresa_login", kwargs={"slug": self.empresa.slug})); self.assertEqual(strict.session[SESSION_EXPIRED_MESSAGE_KEY], "Vuelve a iniciar sesion para continuar.")
        self.assertEqual(self.changes(before), {"sessions.session"})
        strict.cookies["csrftoken"] = "a"*32; before = self.snapshot()
        response = strict.post(self.url(), {**self.data(), "csrfmiddlewaretoken": "a"*32}); self.assertEqual(response.status_code, 302); self.assertTrue(self.changes(before) <= self.BUSINESS_ALLOWED)
