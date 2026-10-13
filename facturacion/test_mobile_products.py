"""Synthetic, independent coverage for mobile catalogue reads and their actions.

The existing catalogue filters and eleven summaries remain authoritative.
APP reads must never initialize or consult inventory, lots or financial data.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .models import CategoriaProductoFarmaceutico, InventarioProducto, PerfilFarmaceuticoProducto, Producto, TipoImpuesto


class _ProductMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileProductsIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    ROUTE = "productos_facturacion"
    ITEM_FIELDS = {"id", "nombre", "codigo", "tipo_label", "unidad_label", "precio", "activo", "descripcion", "categoria", "principio_activo", "controlado", "refrigerado", "impuesto_label"}
    FORBIDDEN_TABLES = ("facturacion_inventarioproducto", "facturacion_existencialotebodega", "facturacion_loteinventario", "facturacion_bodegainventario", "facturacion_bitacoraproductoeliminado", "facturacion_historialcostorealproducto", "facturacion_factura", "facturacion_pagofactura", "clinica_paciente")

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación catálogo QA"})
            cls.empresas, cls.productos, cls.perfiles, cls.categorias = {}, {}, {}, {}
            cls.impuesto = TipoImpuesto.objects.create(nombre="Impuesto global QA", porcentaje=Decimal("15"), activo=False)
            for index, slug in enumerate(cls.SLUGS, 1):
                empresa = Empresa.objects.create(nombre=f"Catálogo QA {slug}", slug=slug, rtn=f"PRODUCTS-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = empresa
                EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo, activo=True)
                active = CategoriaProductoFarmaceutico.objects.create(empresa=empresa, nombre=f"Categoría propia {slug}", activa=True)
                inactive = CategoriaProductoFarmaceutico.objects.create(empresa=empresa, nombre=f"Categoría histórica {slug}", activa=False)
                cls.categorias[(slug, "active")], cls.categorias[(slug, "inactive")] = active, inactive
                for number in range(3):
                    product = Producto.objects.create(empresa=empresa, nombre=f"Artículo catálogo {slug} {number}", codigo=f"MPC-{index}-{number}", tipo_item="servicio" if number == 2 else "producto", unidad_medida="servicio" if number == 2 else "unidad", precio=Decimal("10.25") + number, descripcion=f"Descripción íntegra sintética {slug} {number}", activo=number != 1, controla_inventario=number != 2, impuesto_predeterminado=cls.impuesto if number == 0 else None)
                    cls.productos[(slug, number)] = product
                    if number != 2:
                        cls.perfiles[(slug, number)] = PerfilFarmaceuticoProducto.objects.create(empresa=empresa, producto=product, categoria=active if number == 0 else inactive, principio_activo=f"Principio propio {slug} {number}", laboratorio=f"Laboratorio propio {slug} {number}", registro_sanitario=f"Registro propio {slug} {number}", producto_controlado=number == 0, requiere_refrigeracion=number == 1)
            cls.empresa = cls.empresas["hospital_mia"]
            cls.rol = RolSistema.objects.create(nombre="Sólo catálogo productos QA", codigo="products-qa-granular", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_productos=True)
            cls.legado = RolSistema.objects.create(nombre="Productos legado QA", codigo="products-qa-legacy", activo=True, puede_productos=True)
            cls.usuario = Usuario.objects.create_user(username="products-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Catálogo fuera de cuatro QA", slug="products_externa", rtn="PRODUCTS-EXT", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.modulo)
            cls.usuario.empresas_acceso.add(cls.externa)
            cls.producto_externo = Producto.objects.create(empresa=cls.externa, nombre="Producto ajeno exclusivo QA", codigo="MPC-EXT", precio=Decimal("99"))
            cls.categoria_externa = CategoriaProductoFarmaceutico.objects.create(empresa=cls.externa, nombre="Categoría ajena exclusiva QA")
            cls.eliminado = Producto.objects.create(empresa=cls.empresa, nombre="NO MOSTRAR PRODUCTO ELIMINADO", codigo="MPC-DEL", precio=Decimal("90"), eliminado=True)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url()  # Resolve URL metadata before installing any view blockers.
        self.blocked = {}
        for target in ("facturacion.views._anotar_existencias_por_bodega", "facturacion.views._obtener_inventario_producto", "facturacion.views._asegurar_bodegas_farmaceuticas", "facturacion.views._empresa_usa_perfil_farmaceutico", "facturacion.views._empresa_usa_inventario_por_lotes", "facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden catalogue side effect: {target}"))
            self.blocked[target] = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(self.blocked[target].assert_not_called)

    def url(self, empresa=None, mobile=True, **query):
        base = reverse(self.ROUTE, args=[(empresa or self.empresa).slug])
        parameters = {"app": "1", **query} if mobile else query
        return base + ("?" + urlencode(parameters, doseq=True) if parameters else "")

    def role(self, granular=True, **flags):
        flags = flags or {"puede_productos": True}
        role = RolSistema.objects.create(nombre="Productos rol aislado QA", codigo=f"products-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete()
        self.usuario.refresh_from_db()
        return role

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda item: item._meta.label_lower)}

    def assert_no_writes(self, captured):
        self.assertEqual([row["sql"] for row in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])

    def assert_no_stock_queries(self, captured):
        self.assertFalse(any(table in row["sql"].lower() for table in self.FORBIDDEN_TABLES for row in captured))

    def read(self, url=None, method="get", **kwargs):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.generic(method.upper(), url or self.url(), **kwargs) if isinstance(kwargs.get("data"), bytes) else getattr(self.client, method)(url or self.url(), **kwargs)
        self.assert_no_writes(captured)
        self.assert_no_stock_queries(captured)
        self.assertEqual(self.snapshot(), before)
        return response

    def request(self, url=None, method="get", data=None, body=None):
        factory = RequestFactory(); target = url or self.url()
        if body is not None:
            request = factory.generic(method.upper(), target, data=body, content_type="application/json")
        elif method in {"get", "head", "post"}:
            request = getattr(factory, method)(target, data=data or {})
        else:
            request = factory.generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        return request

    def direct(self, request=None, url=None, method="get", data=None, body=None):
        request = request or self.request(url, method, data, body)
        match = resolve(urlsplit(url or request.get_full_path()).path)
        return views.productos_facturacion.__wrapped__(request, **match.kwargs)

    def denied(self, url=None, method="get", data=None, body=None, request=None, early=True, exception=(PermissionDenied, Http404)):
        request = request or self.request(url, method, data, body)
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(exception):
                self.direct(request=request, url=url)
        self.assert_no_writes(captured); self.assert_no_stock_queries(captured)
        if early:
            self.assertFalse(any('"facturacion_producto"' in row["sql"] for row in captured))
        self.assertEqual(self.snapshot(), before)

    def items(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/mobile_product_catalog.html")
        return response.context["mobile_product_items"]

    def make_many(self, count=42):
        return [Producto.objects.create(empresa=self.empresa, nombre=f"Z Catálogo adicional {number // 2:03d}", codigo=f"MPC-MANY-{number:03d}", precio=Decimal("2.50"), activo=number % 3 != 0) for number in range(count)]

    def test_four_companies_both_role_types_get_head_exact_mobile_template(self):
        for granular in (True, False):
            self.role(granular)
            for company in self.empresas.values():
                response = self.read(self.url(company))
                self.assertEqual({item["id"] for item in self.items(response)}, {self.productos[(company.slug, number)].pk for number in range(3)})
                head = self.read(self.url(company), "head")
                self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")
                self.assertIn("no-store", head["Cache-Control"])

    def test_exact_product_permission_not_inventory_create_edit_or_billing(self):
        for granular in (True, False):
            for flag in ("puede_inventario", "puede_crear_productos", "puede_editar_productos", "puede_ver_facturas", "puede_ver_calendario", "puede_configuracion_facturacion"):
                self.role(granular, **{flag: True})
                self.denied()
                self.assertIn(self.client.get(self.url()).status_code, (302, 403))

    def test_company_local_override_none_or_other_permission_does_not_fall_back(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        self.denied()
        self.role(puede_inventario=True)
        self.denied()

    def test_local_role_can_authorize_one_company_without_another(self):
        self.role(puede_inventario=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=self.rol)
        self.denied()
        self.assertEqual(self.read(self.url(self.empresas["medical_spa"])).status_code, 200)

    def test_membership_active_profile_module_license_enforced_without_middleware(self):
        for change in ({"activa": False}, {"tipo_solucion": "erp"}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate() - timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**change)
            self.denied()
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, tipo_solucion="clinica", estado_licencia="activa", fecha_vencimiento_plan=None)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.denied(self.url(self.empresas["medical_spa"]))

    def test_superuser_license_exception_still_requires_module_and_active_company(self):
        supervisor = Usuario.objects.create_superuser(username="products-super", password="test-only")
        request = self.request(); request.user = supervisor
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.assertEqual(self.direct(request=request).status_code, 200)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied(request=request)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.denied(request=request)

    def test_company_administrator_preserves_original_bypass_not_other_grants(self):
        self.role(puede_inventario=True)
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        self.assertEqual(self.read().status_code, 200)

    def test_logged_out_requests_and_capability_are_denied_without_seed(self):
        from .mobile_products import productos_disponibles_app
        request = self.request(); request.user = AnonymousUser()
        before = self.snapshot()
        self.assertFalse(productos_disponibles_app(request, self.empresa))
        self.assertEqual(Client().get(self.url()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_capability_is_pure_without_optin_inventory_or_catalogue_queries(self):
        from .mobile_products import productos_disponibles_app
        request = self.request(self.url(mobile=False)); before = self.snapshot()
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(productos_disponibles_app(request, self.empresa))
        self.assert_no_writes(captured); self.assert_no_stock_queries(captured)
        for table in ("facturacion_producto", "facturacion_perfilfarmaceuticoproducto", "facturacion_categoriaproductofarmaceutico", "facturacion_tipoimpuesto"):
            self.assertFalse(any(table in row["sql"].lower() for row in captured))
        self.assertEqual(self.snapshot(), before)

    def test_marker_invalid_values_duplicates_fail_closed_before_catalogue_queries(self):
        for values in ("", "0", "true", "01", ["1", "1"], ["0", "1"]):
            self.denied(self.url(app=values))
        for method in ("post", "put", "patch", "delete", "options"):
            self.denied(method=method)

    def test_closed_query_rejects_unknown_and_duplicate_scalars(self):
        for key in ("empresa", "accion", "stock", "modal", "next", "producto", "estado"):
            self.denied(self.url() + "&" + urlencode({key: "1"}))
        for key, value in (("q", "Artículo"), ("categoria", str(self.categorias[(self.empresa.slug, "active")].pk)), ("tipo", "todos"), ("page", "1")):
            self.denied(self.url(**{key: [value, value]}))

    def test_get_head_raw_bodies_and_files_reject_before_catalogue_queries(self):
        for method in ("get", "head"):
            self.denied(method=method, body=b'{"app":"1"}')
            response = self.read(method=method, data=b"{}", content_type="application/json")
            self.assertEqual(response.status_code, 403)
        request = self.request()
        request.POST  # Populate Django's lazy POST before replacing FILES.
        request._files = MultiValueDict({"foto": [SimpleUploadedFile("qa.txt", b"synthetic")]})
        self.denied(request=request)

    def test_post_marker_in_body_without_query_cannot_reach_desktop(self):
        self.denied(self.url(mobile=False), method="post", data={"app": "1", "nombre": "No create"})
        self.assertEqual(self.read(method="post", data={"app": "1"}).status_code, 403)

    def test_page_ascii_positive_int64_canonical_and_large_clamps(self):
        for value in ("", "0", "-1", "01", "+1", "1.0", "1e1", "١", "１", "9223372036854775808", " " * 2 + "1"):
            self.denied(self.url(page=value))
        response = self.read(self.url(page="9223372036854775807"))
        self.assertEqual(response.context["mobile_product_page"], 1)

    def test_category_canonical_own_scope_empty_and_wrapped_whitespace(self):
        for value in ("0", "-1", "01", "+1", "1.0", "١", "9223372036854775808"):
            self.denied(self.url(categoria=value))
        for category in (self.categoria_externa.pk, 9223372036854775807):
            self.denied(self.url(categoria=category), early=False, exception=Http404)
        response = self.read(self.url(categoria=" " + str(self.categorias[(self.empresa.slug, "active")].pk) + " "))
        self.assertEqual([item["id"] for item in self.items(response)], [self.productos[(self.empresa.slug, 0)].pk])
        self.assertEqual(len(self.items(self.read(self.url(categoria="")))), 3)

    def test_type_normalizes_original_invalid_to_todos_and_no_new_enum_rule(self):
        for value in ("", "invalid", " PRODUCTO ", "SeRvIcIo"):
            response = self.read(self.url(tipo=value))
            expected = value.strip().lower() if value.strip().lower() in {"producto", "servicio"} else "todos"
            self.assertEqual(response.context["tipo_item"], expected)
            self.assertEqual(len(self.items(response)), 2 if expected == "producto" else 1 if expected == "servicio" else 3)

    def test_inactive_products_visible_eliminated_and_foreign_ignored(self):
        response = self.read(); ids = {item["id"] for item in self.items(response)}
        self.assertIn(self.productos[(self.empresa.slug, 1)].pk, ids)
        self.assertNotIn(self.eliminado.pk, ids); self.assertNotIn(self.producto_externo.pk, ids)
        self.assertNotContains(response, self.eliminado.nombre); self.assertNotContains(response, self.producto_externo.nombre)

    def test_inactive_category_selectable_and_native_extra_option_not_duplicate(self):
        category = self.categorias[(self.empresa.slug, "inactive")]
        response = self.read(self.url(categoria=category.pk))
        self.assertEqual([item["id"] for item in self.items(response)], [self.productos[(self.empresa.slug, 1)].pk])
        self.assertEqual(response.context["categoria_seleccionada"].pk, category.pk)
        options = [attrs for tag, attrs in _ProductMarkup(response.content.decode()).nodes if tag == "option" and attrs.get("value") == str(category.pk)]
        self.assertEqual(len(options), 1); self.assertIn("selected", options[0])

    def test_original_global_tax_inactive_and_missing_profile_nullable_category_are_valid(self):
        PerfilFarmaceuticoProducto.objects.filter(pk=self.perfiles[(self.empresa.slug, 1)].pk).update(categoria=None)
        items = self.items(self.read())
        first = next(item for item in items if item["id"] == self.productos[(self.empresa.slug, 0)].pk)
        self.assertIn(self.impuesto.nombre, first["impuesto_label"])
        no_profile = next(item for item in items if item["id"] == self.productos[(self.empresa.slug, 2)].pk)
        self.assertEqual(no_profile["categoria"], ""); self.assertEqual(no_profile["principio_activo"], "")
        self.assertFalse(no_profile["controlado"]); self.assertFalse(no_profile["refrigerado"])

    def test_all_original_summary_keys_exact_before_type_filter_and_pagination(self):
        response = self.read(self.url(tipo="servicio"))
        self.assertEqual(response.context["resumen"], {"total": 1, "total_catalogo": 3, "productos": 2, "servicios": 1, "activos": 1, "inactivos": 0, "valor_catalogo": Decimal("12.25"), "con_foto": 0, "con_inventario": 0, "controlados": 0, "refrigerados": 0})
        self.make_many()
        first, second = self.read(), self.read(self.url(page="2"))
        self.assertEqual(first.context["resumen"], second.context["resumen"])
        self.assertEqual(first.context["resumen"]["total"], 45)
        self.assertEqual(first.context["resumen"]["valor_catalogo"], Decimal("138.75"))

    def test_original_summary_photo_count_without_photo_url_or_stock_query(self):
        product = self.productos[(self.empresa.slug, 0)]
        Producto.objects.filter(pk=product.pk).update(foto="productos/fotos/PRIVATE-MEDIA-QA.jpg", costo_promedio=Decimal("999.9999"), costo_real_inventario=Decimal("888.8888"))
        response = self.read()
        self.assertEqual(response.context["resumen"]["con_foto"], 1)
        for secret in ("PRIVATE-MEDIA-QA", "999.9999", "888.8888", "/media/"):
            self.assertNotContains(response, secret)

    def test_q_matches_all_six_original_fields_before_pagination(self):
        product, profile = self.productos[(self.empresa.slug, 1)], self.perfiles[(self.empresa.slug, 1)]
        self.make_many()
        for model, obj, field in ((Producto, product, "nombre"), (Producto, product, "codigo"), (Producto, product, "descripcion"), (PerfilFarmaceuticoProducto, profile, "principio_activo"), (PerfilFarmaceuticoProducto, profile, "laboratorio"), (PerfilFarmaceuticoProducto, profile, "registro_sanitario")):
            original = model.objects.filter(pk=obj.pk).values_list(field, flat=True).get()
            model.objects.filter(pk=obj.pk).update(**{field: "ONLY-SEARCH-SIX-QA"})
            response = self.read(self.url(q=" ONLY-SEARCH-SIX-QA "))
            self.assertEqual([item["id"] for item in self.items(response)], [product.pk])
            self.assertEqual(response.context["resumen"]["total_catalogo"], 1)
            model.objects.filter(pk=obj.pk).update(**{field: original})

    def test_long_query_has_no_new_maxlength_and_empty_result_keeps_echo(self):
        query = "No hay coincidencias " * 90
        response = self.read(self.url(q=query))
        self.assertEqual(self.items(response), [])
        self.assertEqual(response.context["mobile_product_query"], query.strip())
        self.assertEqual(response.context["mobile_product_total"], 0)
        for tag, attrs in _ProductMarkup(response.content.decode()).nodes:
            if attrs.get("name") == "q": self.assertNotIn("maxlength", attrs)

    def test_stable_twenty_pagination_and_distinct_ids_when_names_tie(self):
        self.make_many()
        expected = list(Producto.objects.filter(empresa=self.empresa, eliminado=False).order_by("nombre", "id").values_list("pk", flat=True))
        first, second, third = (self.read(self.url(page=str(page))) for page in (1, 2, 3))
        self.assertEqual([item["id"] for item in self.items(first)], expected[:20])
        self.assertEqual([item["id"] for item in self.items(second)], expected[20:40])
        self.assertEqual([item["id"] for item in self.items(third)], expected[40:])
        self.assertEqual(first.context["mobile_product_total"], 45)
        self.assertEqual(first.context["mobile_product_num_pages"], 3)

    def test_query_category_type_persist_on_pager_and_type_tabs(self):
        category = self.categorias[(self.empresa.slug, "active")]
        for product in self.make_many():
            PerfilFarmaceuticoProducto.objects.create(empresa=self.empresa, producto=product, categoria=category)
        response = self.read(self.url(q="Catálogo", categoria=category.pk, tipo="producto"))
        parsed = parse_qs(urlsplit(response.context["mobile_product_next_url"]).query)
        self.assertEqual(parsed, {"app": ["1"], "q": ["Catálogo"], "categoria": [str(category.pk)], "tipo": ["producto"], "page": ["2"]})
        for item in response.context["mobile_product_types"]:
            query = parse_qs(urlsplit(item["url"]).query)
            self.assertEqual(query["app"], ["1"]); self.assertEqual(query["q"], ["Catálogo"]); self.assertEqual(query["categoria"], [str(category.pk)])
            self.assertEqual(query["tipo"], [item["codigo"]])

    def test_empty_catalogue_keeps_zero_original_summary_and_no_seed(self):
        Producto.objects.filter(empresa=self.empresa).delete()
        response = self.read()
        self.assertEqual(self.items(response), [])
        self.assertEqual(set(response.context["resumen"]), {"total", "total_catalogo", "productos", "servicios", "activos", "inactivos", "valor_catalogo", "con_foto", "con_inventario", "controlados", "refrigerados"})
        self.assertTrue(all(value == 0 for value in response.context["resumen"].values()))

    def test_profile_foreign_company_hidden_by_filters_denied_before_display(self):
        profile = self.perfiles[(self.empresa.slug, 1)]
        PerfilFarmaceuticoProducto.objects.filter(pk=profile.pk).update(empresa=self.externa)
        self.denied(self.url(q="NO-COINCIDENCIA", tipo="servicio", page="2"), early=False)
        response = self.read(self.url(q="NO-COINCIDENCIA"))
        self.assertEqual(response.status_code, 403); self.assertNotContains(response, profile.principio_activo, status_code=403)

    def test_profile_foreign_category_hidden_by_filters_denied(self):
        PerfilFarmaceuticoProducto.objects.filter(pk=self.perfiles[(self.empresa.slug, 1)].pk).update(categoria=self.categoria_externa)
        self.denied(self.url(q="NO-COINCIDENCIA", page="2"), early=False)

    def test_orphan_category_and_global_tax_cannot_disappear_through_inner_joins(self):
        profile, product = self.perfiles[(self.empresa.slug, 1)], self.productos[(self.empresa.slug, 0)]
        for model, obj, field, original in ((PerfilFarmaceuticoProducto, profile, "categoria_id", profile.categoria_id), (Producto, product, "impuesto_predeterminado_id", product.impuesto_predeterminado_id)):
            try:
                model.objects.filter(pk=obj.pk).update(**{field: 9223372036854775807})
                self.denied(self.url(q="NO-COINCIDENCIA", tipo="servicio", page="2"), early=False)
            finally:
                model.objects.filter(pk=obj.pk).update(**{field: original})

    def test_corrupt_foreign_or_eliminated_product_relations_do_not_poison_scope(self):
        PerfilFarmaceuticoProducto.objects.create(empresa=self.externa, producto=self.eliminado, categoria=self.categoria_externa)
        PerfilFarmaceuticoProducto.objects.create(empresa=self.empresa, producto=self.producto_externo, categoria=self.categorias[(self.empresa.slug, "active")])
        self.assertEqual(len(self.items(self.read())), 3)

    def test_unused_inventory_wrong_company_is_never_queried_or_presented(self):
        product = self.productos[(self.empresa.slug, 0)]
        InventarioProducto.objects.create(empresa=self.externa, producto=product, existencias=Decimal("98765.43"))
        response = self.read()
        self.assertNotContains(response, "98765")
        self.assertEqual(len(self.items(response)), 3)

    def test_projection_exact_allowlist_no_models_photo_cost_stock_or_profile_object(self):
        response = self.read()
        for item in self.items(response):
            self.assertIsInstance(item, dict); self.assertEqual(set(item), self.ITEM_FIELDS)
            self.assertFalse(any(hasattr(value, "_meta") for value in item.values()))
        self.assertEqual(list(response.context["productos"]), self.items(response))
        self.assertEqual(list(response.context["page_obj"]), self.items(response))
        for key in ("productos_sugeridos", "ultimas_bajas", "form", "inventario", "bodegas", "lotes"):
            self.assertNotIn(key, response.context)

    def test_native_get_filters_and_only_original_authorized_baja_posts(self):
        response = self.read(); nodes = _ProductMarkup(response.content.decode()).nodes
        forms = [attrs for tag, attrs in nodes if tag == "form"]
        self.assertEqual(len(forms), 1 + len(self.items(response)))
        self.assertEqual(forms[0].get("method", "get").lower(), "get")
        self.assertEqual(forms[0]["id"], "mobileProductCatalogSearchForm")
        self.assertEqual(forms[0]["action"], response.context["app_products_search_url"])
        self.assertTrue(response.context["can_delete_product"])
        self.assertFalse(response.context["can_create_product"])
        self.assertFalse(response.context["can_edit_product"])
        fields_by_form = []
        for tag, attrs in nodes:
            if tag == "form":
                fields_by_form.append([])
            elif fields_by_form and tag in {"input", "select", "textarea"} and attrs.get("name"):
                fields_by_form[-1].append(attrs["name"])
        self.assertEqual(sorted(fields_by_form[0]), ["app", "categoria", "q", "tipo"])
        for item, attrs, names in zip(self.items(response), forms[1:], fields_by_form[1:]):
            self.assertEqual(attrs.get("method", "get").lower(), "post")
            self.assertEqual(attrs["action"], reverse("eliminar_producto_facturacion", args=[self.empresa.slug, item["id"]]) + "?app=1")
            self.assertEqual(attrs["data-operation-kind"], "baja")
            self.assertEqual(attrs["data-operation-confirmation"], "ELIMINAR")
            self.assertEqual(sorted(names), ["app", "csrfmiddlewaretoken", "motivo"])
        self.assertNotContains(response, "<table"); self.assertNotContains(response, 'rel="manifest"')
        forbidden = [reverse(name, args=[self.empresa.slug]) for name in ("crear_producto_facturacion", "categorias_farmaceuticas", "codigos_barras_productos", "inventario_facturacion", "productos_facturacion")]
        for tag, attrs in nodes:
            if tag == "a" and attrs.get("href"):
                href = attrs["href"]
                if href == response.context["app_modules_url"]:
                    continue  # Authorized standalone route lives under /crm/citas/.
                self.assertNotIn(href, forbidden)
                self.assertFalse(any(value in href for value in ("editar", "eliminar", "facturas", "recibos", "citas", "pacientes", "/media/")))

    def test_xss_and_long_description_are_escaped_not_silently_truncated(self):
        product = self.productos[(self.empresa.slug, 0)]
        name = '<img src=x onerror="PRODUCT-XSS"> & "nombre"'
        description = '<script>DESCRIPTION-XSS</script> & ' + "Detalle íntegro. " * 100
        Producto.objects.filter(pk=product.pk).update(nombre=name, descripcion=description)
        response = self.read(self.url(q="PRODUCT-XSS"))
        self.assertNotContains(response, name); self.assertNotContains(response, "<script>DESCRIPTION-XSS</script>")
        self.assertContains(response, "&lt;img"); self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(self.items(response)[0]["descripcion"], description)
        self.assertContains(response, "Detalle íntegro. " * 50)

    def test_product_only_functions_roundtrip_has_no_patient_calendar_or_stock_grants(self):
        for granular in (True, False):
            self.role(granular)
            response = self.read()
            modules_url = response.context["app_modules_url"]
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            before = self.snapshot(); hub = self.client.get(modules_url)
            self.assertEqual(hub.status_code, 200)
            self.assertEqual(self.snapshot(), before)
            card = next(item for item in hub.context["modulos_app"] if item["key"] == "catalogo_productos")
            self.assertEqual(card["estado"], "disponible"); self.assertEqual(card["url"], self.url())
            self.assertFalse(self.usuario.tiene_permiso_erp("puede_inventario", self.empresa))
            self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa))
            self.assertEqual(self.read(card["url"]).status_code, 200)

    def test_catalogue_card_absent_for_roles_without_original_product_read(self):
        self.role(puede_inventario=True)
        hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertEqual(hub.status_code, 200)
        self.assertNotIn("catalogo_productos", [item["key"] for item in hub.context["modulos_app"]])
        self.denied()

    def test_four_original_price_final_flags_do_not_recalculate_prices(self):
        for company in self.empresas.values():
            response = self.read(self.url(company))
            self.assertIs(response.context["mobile_product_price_includes_tax"], True)
            self.assertContains(response, "Precio final, impuesto incluido")
            self.assertEqual({item["id"]: item["precio"] for item in self.items(response)}, {self.productos[(company.slug, number)].pk: self.productos[(company.slug, number)].precio for number in range(3)})

    def test_foreign_selected_category_denies_before_product_scope_queries(self):
        self.denied(self.url(categoria=self.categoria_externa.pk), exception=Http404)

    def test_original_web_template_summary_and_annotation_unchanged_without_app(self):
        for company in self.empresas.values():
            for query in ({}, {"tipo": "servicio"}, {"categoria": self.categorias[(company.slug, "inactive")].pk}, {"q": "Artículo"}):
                before = self.snapshot()
                mobile = self.read(self.url(company, **query))
                with patch.object(views, "_anotar_existencias_por_bodega", wraps=self.original_annotation) as annotation:
                    web = self.client.get(self.url(company, mobile=False, **query))
                self.assertEqual(web.status_code, 200); self.assertTemplateUsed(web, "facturacion/productos_premium.html")
                annotation.assert_called_once()
                self.assertEqual(web.context["resumen"], mobile.context["resumen"])
                self.assertEqual([product.pk for product in web.context["productos"]], [item["id"] for item in self.items(mobile)])
                self.assertEqual(self.snapshot(), before)

    def test_outside_four_company_app_marker_preserves_original_desktop(self):
        before = self.snapshot()
        with patch.object(views, "_anotar_existencias_por_bodega", wraps=self.original_annotation) as annotation:
            response = self.client.get(self.url(self.externa))
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "facturacion/productos_premium.html")
        self.assertNotIn("mobile_product_items", response.context)
        annotation.assert_called_once(); self.assertEqual(self.snapshot(), before)

    def test_original_web_post_catalogue_behavior_unchanged_no_app(self):
        before = self.snapshot()
        with patch.object(views, "_anotar_existencias_por_bodega", wraps=self.original_annotation):
            response = self.client.post(self.url(mobile=False), {"nombre": "No new product", "accion": "crear"})
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "facturacion/productos_premium.html")
        self.assertEqual(self.snapshot(), before)

    def test_csrf_and_post_are_not_a_write_or_catalogue_bypass(self):
        from django.contrib.sessions.models import Session
        from core import views as core_views
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario)
        before = self.snapshot()
        session_keys = set(Session.objects.values_list("session_key", flat=True))
        session_data = dict(strict.session)
        response = strict.post(self.url(), {"app": "1", "nombre": "No write"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        after = self.snapshot()
        # The unchanged global CSRF handler writes only its documented expiry
        # notice to the existing session; no APP/business saver was reached.
        self.assertEqual({key: value for key, value in after.items() if key != "sessions.session"}, {key: value for key, value in before.items() if key != "sessions.session"})
        self.assertEqual(set(Session.objects.values_list("session_key", flat=True)), session_keys)
        self.assertEqual(dict(strict.session), {**session_data, core_views.SESSION_EXPIRED_MESSAGE_KEY: core_views.SESSION_EXPIRED_MESSAGE})
        before = after
        strict.cookies["csrftoken"] = "a" * 32
        reset_queries()
        with CaptureQueriesContext(connection) as captured:
            response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": "a" * 32})
        self.assert_no_writes(captured); self.assert_no_stock_queries(captured)
        self.assertEqual(response.status_code, 403); self.assertEqual(self.snapshot(), before)

    # Preserve the original callable before setUp replaces the view binding.
    original_annotation = staticmethod(views._anotar_existencias_por_bodega)
