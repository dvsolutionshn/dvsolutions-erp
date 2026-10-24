"""Original manual lot entry/transfer, tested only with synthetic TestCase data.

No invented LoteForm, alternate FEFO service, actor or transaction semantics.
Explicit initializer tests separate allowed GET effects from prepared reads.
"""
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import urlencode
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .models import BodegaInventario, ExistenciaLoteBodega, InventarioProducto, LoteInventario, MovimientoInventario, MovimientoLoteBodega, Producto, Proveedor


class _StockOperationMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileStockOperationsIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    ROUTES = {"lot": "crear_lote_inventario", "transfer": "traslado_inventario_farmaceutico", "entry": "entrada_inventario"}
    FIELDS = {"lot": ("producto", "bodega", "numero_lote", "cantidad", "fecha_vencimiento", "fecha_vencimiento_rapida", "proveedor", "observacion"), "transfer": ("existencia", "bodega_destino", "cantidad", "observacion")}
    ALLOWED_LOT = {"facturacion.loteinventario", "facturacion.existencialotebodega", "facturacion.inventarioproducto", "facturacion.movimientolotebodega", "facturacion.movimientoinventario", "core.registroauditoria"}
    ALLOWED_TRANSFER = {"facturacion.existencialotebodega", "facturacion.movimientolotebodega", "core.registroauditoria"}

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.module, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Stock operaciones QA"})
            cls.empresas, cls.productos, cls.bodegas, cls.lotes, cls.existencias, cls.proveedores = {}, {}, {}, {}, {}, {}
            for number, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Stock operaciones QA {slug}", slug=slug, rtn=f"MSO-{number}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company; EmpresaModulo.objects.create(empresa=company, modulo=cls.module)
                ConfiguracionAvanzadaEmpresa.objects.create(empresa=company, usa_bodegas_internas=True, usa_inventario_farmaceutico=True)
                names = (("Bodega Principal", "principal"), ("Bodega Clinica", "provisional")) if slug == "luque_aestetic" else (("Bodega General", "principal"), ("Bodega Hospital", "provisional"), ("Vitrina", "vitrina")) if slug in {"hospital_mia", "medical_spa"} else (("Bodega principal", "principal"), ("Bodega provisional", "provisional"), ("Vitrina", "vitrina"))
                cls.bodegas[slug] = [BodegaInventario.objects.create(empresa=company, nombre=name, tipo=kind) for name, kind in names]
                product = Producto.objects.create(empresa=company, nombre=f"Producto stock {slug}", codigo=f"MSO-P-{number}", precio=Decimal("115"), controla_inventario=True)
                cls.productos[slug] = product
                provider = Proveedor.objects.create(empresa=company, nombre=f"Proveedor local stock {slug}", rtn=f"MSO-PROV-{number}")
                cls.proveedores[slug] = provider
                lot = LoteInventario.objects.create(empresa=company, producto=product, numero_lote=f"MSO-OLD-{number}", fecha_vencimiento="2027-08-31", proveedor=provider)
                cls.lotes[slug] = lot
                cls.existencias[slug] = ExistenciaLoteBodega.objects.create(empresa=company, bodega=cls.bodegas[slug][0], lote=lot, cantidad=Decimal("20"))
                InventarioProducto.objects.create(empresa=company, producto=product, existencias=Decimal("20"), stock_minimo=Decimal("3"))
            cls.empresa = cls.empresas["hospital_mia"]; cls.producto = cls.productos[cls.empresa.slug]; cls.lote = cls.lotes[cls.empresa.slug]; cls.existencia = cls.existencias[cls.empresa.slug]
            cls.granular = RolSistema.objects.create(nombre="Stock granular QA", codigo="mso-granular", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_inventario=True)
            cls.legacy = RolSistema.objects.create(nombre="Stock legado QA", codigo="mso-legacy", activo=True, puede_inventario=True)
            cls.usuario = Usuario.objects.create_user(username="mso-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.granular)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Stock externo QA", slug="mso_externa", rtn="MSO-EXT", tipo_solucion="erp", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.module); cls.usuario.empresas_acceso.add(cls.externa)
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=cls.externa, usa_bodegas_internas=True)
            cls.producto_externo = Producto.objects.create(empresa=cls.externa, nombre="Producto ajeno MSO", codigo="MSO-EXT-P", precio=Decimal("20"))
            cls.bodega_externa = BodegaInventario.objects.create(empresa=cls.externa, nombre="Bodega ajena MSO")
            cls.proveedor_externo = Proveedor.objects.create(empresa=cls.externa, nombre="Proveedor ajeno MSO", rtn="MSO-EXT-PROV")
            cls.lote_externo = LoteInventario.objects.create(empresa=cls.externa, producto=cls.producto_externo, numero_lote="MSO-EXT-L", proveedor=cls.proveedor_externo)
            cls.existencia_externa = ExistenciaLoteBodega.objects.create(empresa=cls.externa, bodega=cls.bodega_externa, lote=cls.lote_externo, cantidad=Decimal("9"))

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW); clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario); self.url()  # Metadata resolution, not a GET.
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden stock transport: {target}")); mock = blocker.start()
            self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def url(self, operation="lot", company=None, mobile=True, **query):
        company = company or self.empresa; base = reverse(self.ROUTES[operation], args=[company.slug]); params = {"app": "1", **query} if mobile else query
        return base + ("?"+urlencode(params, doseq=True) if params else "")

    def data(self, operation="lot", company=None, **updates):
        company = company or self.empresa; slug = company.slug
        if operation == "lot":
            values = {"app": "1", "producto": str(self.productos[slug].pk), "bodega": str(self.bodegas[slug][0].pk), "numero_lote": "MSO-NEW", "cantidad": "2.00", "fecha_vencimiento": "2027-08-31", "fecha_vencimiento_rapida": "", "proveedor": str(self.proveedores[slug].pk), "observacion": "  Entrada sintética <b>literal</b>  "}
        else:
            values = {"app": "1", "existencia": str(self.existencias[slug].pk), "bodega_destino": str(self.bodegas[slug][1].pk), "cantidad": "2.00", "observacion": "  Traslado sintético  "}
        values.update(updates); return values

    def role(self, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Stock rol aislado QA", codigo=f"mso-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete(); self.usuario.refresh_from_db(); return role

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda item: item._meta.label_lower)}

    def changes(self, before):
        after = self.snapshot(); return {label for label in before if before[label] != after[label]}

    def no_writes(self, captured):
        self.assertEqual([row["sql"] for row in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])

    def request(self, operation="lot", method="get", data=None, target=None, body=None):
        factory = RequestFactory(); target = target or self.url(operation)
        request = factory.generic(method.upper(), target, data=body, content_type="application/json") if body is not None else getattr(factory, method)(target, data=data or {}) if method in {"get", "head", "post"} else factory.generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk); return request

    def direct(self, request, operation="lot", company=None):
        callback = {"lot": views.crear_lote_inventario, "transfer": views.traslado_inventario_farmaceutico, "entry": views.entrada_inventario}[operation].__wrapped__
        return callback(request, (company or self.empresa).slug)

    def denied(self, operation="lot", method="get", data=None, target=None, request=None, exception=(PermissionDenied, Http404), early=True):
        request = request or self.request(operation, method, data, target); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured, patch.object(views, "_asegurar_bodegas_farmaceuticas", side_effect=AssertionError("Denied request must not initialize")):
            with self.assertRaises(exception): self.direct(request, operation)
        self.no_writes(captured); self.assertEqual(self.snapshot(), before)
        # Scalar identifier validation may perform its own scoped EXISTS. No
        # denied envelope can reach configuration creation or initialization.
        if early: self.assertFalse(any('"core_configuracionavanzadaempresa"' in row["sql"].lower() for row in captured))

    def immutable(self, operation="lot", company=None, method="get", data=None, target=None):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured: response = getattr(self.client, method)(target or self.url(operation, company), data=data or {})
        self.no_writes(captured); self.assertEqual(self.snapshot(), before); return response

    def assert_mobile(self, response, operation="lot", bound=False):
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "facturacion/mobile_stock_operation.html")
        self.assertEqual(response.context["mobile_stock_operation"], "entrada_lote" if operation == "lot" else "transferencia")
        self.assertEqual(response.context["mobile_stock_bound"], bound); self.assertIn("no-store", response["Cache-Control"])

    def test_four_companies_both_roles_prepared_get_head_immutable(self):
        for role in (self.granular, self.legacy):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                for operation in ("lot", "transfer"):
                    self.assert_mobile(self.immutable(operation, company), operation); head = self.immutable(operation, company, "head")
                    self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")

    def test_original_inventory_permission_no_product_calendar_or_create_substitutes(self):
        for granular in (True, False):
            for flag in ("puede_productos", "puede_crear_productos", "puede_editar_productos", "puede_transferir_inventario", "puede_ver_calendario"):
                self.role(granular, **{flag: True}); self.denied(); self.denied("transfer")

    def test_local_none_denies_without_global_fallback(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None); self.denied(); self.denied("transfer")

    def test_local_override_inventory_grants_only_its_company(self):
        self.role(puede_productos=True); UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=self.granular)
        self.assert_mobile(self.immutable()); company = self.empresas["medical_spa"]; request = self.request(target=self.url(company=company)); before = self.snapshot()
        with self.assertRaises(PermissionDenied): self.direct(request, company=company)
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_inactive_accounts_and_membership_denied_without_seed(self):
        request = self.request(); request.user = AnonymousUser(); self.denied(request=request)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_active=False); self.assertEqual(self.immutable().status_code, 302)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_active=True, empresa=self.externa); self.usuario.empresas_acceso.clear(); self.denied()

    def test_inactive_profile_module_license_and_original_admin_super_rules(self):
        for values in ({"activa": False}, {"tipo_solucion": "erp"}, {"estado_licencia": "suspendida"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values); self.denied(); Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=False); self.denied()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=True)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=None, es_administrador_empresa=True); self.assert_mobile(self.immutable())
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida"); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True); self.assert_mobile(self.immutable())

    def test_closed_unique_app_query_and_unknown_destinations(self):
        for operation in ("lot", "transfer"):
            for suffix in ("&app=1", "&app=0", "&next=/", "&empresa=1", "&accion=1", "&page=1"):
                self.denied(operation, target=self.url(operation)+suffix)
            self.denied(operation, target=self.url(operation, app="0"))

    def test_matching_post_marker_query_and_body_required(self):
        for operation in ("lot", "transfer"):
            for target, data in ((self.url(operation, mobile=False), self.data(operation)), (self.url(operation, app="0"), self.data(operation)), (self.url(operation), self.data(operation, app="0")), (self.url(operation), self.data(operation, app=["1", "1"]))): self.denied(operation, "post", data, target)

    def test_unknown_post_keys_and_next_never_override_original_context(self):
        for operation in ("lot", "transfer"):
            for key in ("empresa", "usuario", "tipo", "stock_minimo", "next", "accion", "lote_id", "referencia"):
                self.denied(operation, "post", {**self.data(operation), key: "1"})

    def test_duplicate_every_native_scalar_rejected_before_initializer(self):
        for operation in ("lot", "transfer"):
            for key in (*self.FIELDS[operation], "app", "csrfmiddlewaretoken"): self.denied(operation, "post", {**self.data(operation), key: ["1", "1"]})

    def test_files_raw_get_head_bodies_and_unsupported_methods_rejected(self):
        for operation in ("lot", "transfer"):
            self.denied(operation, "post", {**self.data(operation), "archivo": SimpleUploadedFile("synthetic.txt", b"synthetic")})
            for method in ("get", "head"):
                self.denied(operation, request=self.request(operation, method, body=b"{}"))
                request = self.request(operation, method); request.POST; request._files = MultiValueDict({"archivo": [SimpleUploadedFile("synthetic.txt", b"synthetic")]}); self.denied(operation, request=request)
            for method in ("put", "patch", "delete", "options"): self.denied(operation, method)

    def test_selected_query_product_canonical_and_foreign_before_initializers(self):
        for value in ("0", "00"+str(self.producto.pk), "１２", "-1", "9223372036854775808"):
            self.denied(target=self.url(producto=value))
        for value in (self.producto_externo.pk, 999999999): self.denied(target=self.url(producto=value), exception=Http404)
        response = self.immutable(target=self.url(producto=self.producto.pk)); self.assert_mobile(response)
        field = response.context["mobile_stock_sections"][0]["campos"][0]; self.assertEqual(str(field["value"]), str(self.producto.pk))

    def test_selector_post_noncanonical_ids_rejected_before_decimal_or_save(self):
        for operation, field in (("lot", "producto"), ("lot", "bodega"), ("lot", "proveedor"), ("transfer", "existencia"), ("transfer", "bodega_destino")):
            for value in ("0", "00"+self.data(operation)[field], "9223372036854775808", "１２", "x"):
                self.denied(operation, "post", self.data(operation, **{field: value}))

    def test_foreign_or_missing_selectors_deny_no_fallback(self):
        for operation, field, foreign in (("lot", "producto", self.producto_externo.pk), ("lot", "bodega", self.bodega_externa.pk), ("lot", "proveedor", self.proveedor_externo.pk), ("transfer", "existencia", self.existencia_externa.pk), ("transfer", "bodega_destino", self.bodega_externa.pk)):
            for value in (foreign, 999999999): self.denied(operation, "post", self.data(operation, **{field: str(value)}), exception=Http404)

    def test_empty_required_selection_uses_original_error_not_fallback(self):
        for operation, field in (("lot", "producto"), ("lot", "bodega"), ("transfer", "existencia"), ("transfer", "bodega_destino")):
            response = self.immutable(operation, method="post", data=self.data(operation, **{field: ""})); self.assert_mobile(response, operation, True)
            fields = {item["name"]: item for section in response.context["mobile_stock_sections"] for item in section["campos"]}; self.assertEqual(fields[field]["value"], "")

    def test_inactive_or_noninventory_product_original_selection_rejected(self):
        for values in ({"activo": False}, {"controla_inventario": False}):
            Producto.objects.filter(pk=self.producto.pk).update(**values)
            response = self.immutable(method="post", data=self.data()); self.assert_mobile(response, bound=True)
            self.denied(target=self.url(producto=self.producto.pk), exception=Http404)
            Producto.objects.filter(pk=self.producto.pk).update(activo=True, controla_inventario=True)

    def test_manual_native_descriptors_exact_fields_no_form_validator_invented(self):
        for operation in ("lot", "transfer"):
            response = self.immutable(operation); fields = [item for section in response.context["mobile_stock_sections"] for item in section["campos"]]
            self.assertEqual(tuple(item["name"] for item in fields), self.FIELDS[operation]); self.assertFalse(any("form" in context for context in response.context))
            names = {attrs["name"] for tag, attrs in _StockOperationMarkup(response.content.decode()).nodes if tag in {"input", "select", "textarea"} and attrs.get("name")}
            self.assertEqual(names, set(self.FIELDS[operation])|{"app", "csrfmiddlewaretoken"})

    def test_invalid_bound_keeps_all_manual_values_and_escapes_labels(self):
        body = self.data(cantidad="NaN", numero_lote="Lote literal <script>synthetic</script>")
        response = self.immutable(method="post", data=body); self.assert_mobile(response, bound=True)
        fields = {item["name"]: item for section in response.context["mobile_stock_sections"] for item in section["campos"]}
        for key in self.FIELDS["lot"]: self.assertEqual(str(fields[key]["value"]), body[key])
        self.assertNotContains(response, "<script>synthetic</script>")

    def test_nan_infinity_exponent_capacity_and_precision_return_bound_no_mutation(self):
        for operation in ("lot", "transfer"):
            for value in ("NaN", "Infinity", "-Infinity", "1e999", "10000000000", "0.001", "x", "9"*129):
                response = self.immutable(operation, method="post", data=self.data(operation, cantidad=value)); self.assert_mobile(response, operation, True)

    def test_nonpositive_and_insufficient_preserve_original_business_rejections(self):
        for operation in ("lot", "transfer"):
            for value in ("0", "-1", ""):
                response = self.immutable(operation, method="post", data=self.data(operation, cantidad=value)); self.assert_mobile(response, operation, True)
        response = self.immutable("transfer", method="post", data=self.data("transfer", cantidad="21")); self.assert_mobile(response, "transfer", True)

    def test_fefo_requires_date_and_original_rapid_month_last_day(self):
        response = self.immutable(method="post", data=self.data(fecha_vencimiento="", fecha_vencimiento_rapida="")); self.assert_mobile(response, bound=True)
        response = self.client.post(self.url(), self.data(fecha_vencimiento="", fecha_vencimiento_rapida="agos-27")); self.assertEqual(response.status_code, 302)
        lot = LoteInventario.objects.get(empresa=self.empresa, numero_lote="MSO-NEW"); self.assertEqual(lot.fecha_vencimiento.isoformat(), "2027-08-31")

    def test_invalid_iso_rapid_dates_and_long_lot_numbers_no_500_or_writes(self):
        for values in ({"fecha_vencimiento": "2027-02-30"}, {"fecha_vencimiento": "malformed"}, {"fecha_vencimiento": "", "fecha_vencimiento_rapida": "wrong"}, {"fecha_vencimiento": "", "fecha_vencimiento_rapida": "ago-10000"}, {"numero_lote": "L"*81}):
            response = self.immutable(method="post", data=self.data(**values)); self.assert_mobile(response, bound=True)

    def test_same_warehouse_rejected_and_target_capacity_checked_before_mutation(self):
        response = self.immutable("transfer", method="post", data=self.data("transfer", bodega_destino=str(self.bodegas[self.empresa.slug][0].pk))); self.assert_mobile(response, "transfer", True)
        target = ExistenciaLoteBodega.objects.create(empresa=self.empresa, bodega=self.bodegas[self.empresa.slug][1], lote=self.lote, cantidad=Decimal("9999999999.99"))
        response = self.immutable("transfer", method="post", data=self.data("transfer", cantidad="1")); self.assert_mobile(response, "transfer", True)
        target.refresh_from_db(); self.assertEqual(target.cantidad, Decimal("9999999999.99"))

    def test_entry_general_and_lot_capacity_prevent_partial_save(self):
        InventarioProducto.objects.filter(producto=self.producto).update(existencias=Decimal("9999999999.99"))
        response = self.immutable(method="post", data=self.data(cantidad="1")); self.assert_mobile(response, bound=True)

    def test_valid_lot_four_companies_both_roles_calls_original_movements_once(self):
        for role_index, role in enumerate((self.granular, self.legacy)):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                body = self.data(company=company, numero_lote=f"MSO-MATRIX-{role_index}-{company.pk}"); before = self.snapshot()
                with patch.object(views, "_registrar_movimiento_lote_bodega", wraps=views._registrar_movimiento_lote_bodega) as lots, patch.object(views, "_registrar_movimiento_inventario", wraps=views._registrar_movimiento_inventario) as general: response = self.client.post(self.url(company=company), body)
                self.assertEqual(response.status_code, 302); lots.assert_called_once(); general.assert_called_once(); self.assertTrue(self.changes(before) <= self.ALLOWED_LOT)
                record = LoteInventario.objects.get(empresa=company, numero_lote=body["numero_lote"]); self.assertEqual(record.producto_id, self.productos[company.slug].pk); self.assertEqual(record.proveedor_id, self.proveedores[company.slug].pk)

    def test_existing_lot_is_original_update_and_add_quantity_not_new_record(self):
        old_count = LoteInventario.objects.count(); response = self.client.post(self.url(), self.data(numero_lote=self.lote.numero_lote, fecha_vencimiento="2028-12-31", proveedor="")); self.assertEqual(response.status_code, 302)
        self.assertEqual(LoteInventario.objects.count(), old_count); self.lote.refresh_from_db(); self.assertEqual(self.lote.fecha_vencimiento.isoformat(), "2028-12-31"); self.assertIsNone(self.lote.proveedor_id)
        self.existencia.refresh_from_db(); self.assertEqual(self.existencia.cantidad, Decimal("22"))

    def test_lot_reference_observation_and_authenticated_movement_actor(self):
        response = self.client.post(self.url(), self.data(numero_lote="  MSO-TRIM  ")); self.assertEqual(response.status_code, 302)
        move = MovimientoLoteBodega.objects.get(lote__numero_lote="MSO-TRIM"); general = MovimientoInventario.objects.get(producto=self.producto)
        self.assertEqual(move.referencia, "Lote MSO-TRIM"); self.assertEqual(move.observacion, "Entrada sintética <b>literal</b>"); self.assertEqual(move.usuario_id, self.usuario.pk)
        self.assertEqual(general.referencia, move.referencia); self.assertEqual(general.usuario_id, self.usuario.pk)
        audit = RegistroAuditoria.objects.filter(app_label="facturacion", modelo="movimientolotebodega", objeto_id=str(move.pk)).latest("id"); self.assertEqual(audit.usuario_id, self.usuario.pk); self.assertEqual(audit.empresa_id, self.empresa.pk)

    def test_provider_omitted_or_inactive_original_null_selection_and_no_provider_mutations(self):
        Proveedor.objects.filter(pk=self.proveedores[self.empresa.slug].pk).update(activo=False)
        before = list(Proveedor.objects.order_by("id").values()); response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302); self.assertIsNone(LoteInventario.objects.get(numero_lote="MSO-NEW", empresa=self.empresa).proveedor_id)
        self.assertEqual(list(Proveedor.objects.order_by("id").values()), before)

    def test_transfer_four_companies_both_roles_exact_pair_no_general_stock_change(self):
        for role in (self.granular, self.legacy):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                before = self.snapshot(); general_before = list(InventarioProducto.objects.order_by("id").values())
                with patch.object(views, "_registrar_movimiento_lote_bodega", wraps=views._registrar_movimiento_lote_bodega) as saver: response = self.client.post(self.url("transfer", company), self.data("transfer", company))
                self.assertEqual(response.status_code, 302); self.assertEqual(saver.call_count, 2); self.assertEqual([call.kwargs["tipo"] for call in saver.call_args_list], ["traslado_salida", "traslado_entrada"])
                self.assertEqual(list(InventarioProducto.objects.order_by("id").values()), general_before); self.assertTrue(self.changes(before) <= self.ALLOWED_TRANSFER)

    def test_transfer_pair_same_lot_reference_and_original_snapshots(self):
        response = self.client.post(self.url("transfer"), self.data("transfer")); self.assertEqual(response.status_code, 302)
        moves = list(MovimientoLoteBodega.objects.filter(lote=self.lote).order_by("id")); self.assertEqual(len(moves), 2)
        self.assertEqual([m.cantidad for m in moves], [Decimal("2"), Decimal("2")]); self.assertEqual([m.usuario_id for m in moves], [self.usuario.pk, self.usuario.pk])
        self.assertEqual([m.existencia_anterior for m in moves], [Decimal("20"), Decimal("0")]); self.assertEqual([m.existencia_resultante for m in moves], [Decimal("18"), Decimal("2")])
        self.assertEqual(moves[0].referencia, moves[1].referencia); self.assertEqual(moves[0].observacion, "Traslado sintético")
        self.assertEqual(sum(ExistenciaLoteBodega.objects.filter(lote=self.lote).values_list("cantidad", flat=True)), Decimal("20"))

    def test_transfer_original_atomic_rollback_when_second_movement_fails(self):
        original = views._registrar_movimiento_lote_bodega; calls = []
        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2: raise RuntimeError("Synthetic second movement failure")
            return original(**kwargs)
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "Synthetic second movement failure"): self.client.post(self.url("transfer"), self.data("transfer"))
        self.assertEqual(len(calls), 2); self.assertEqual(self.snapshot(), before)

    def test_lot_original_nonatomic_failure_is_not_falsely_promised_rollback(self):
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_inventario", side_effect=RuntimeError("Synthetic general movement failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic general movement failure"): self.client.post(self.url(), self.data())
        self.assertTrue(LoteInventario.objects.filter(empresa=self.empresa, numero_lote="MSO-NEW").exists())
        self.assertEqual(self.changes(before), {"facturacion.loteinventario", "facturacion.existencialotebodega", "facturacion.movimientolotebodega", "core.registroauditoria"})

    def test_corrupt_lot_company_product_provider_denied_before_initializer(self):
        for values in ({"empresa_id": self.externa.pk}, {"producto_id": self.producto_externo.pk}, {"proveedor_id": self.proveedor_externo.pk}, {"proveedor_id": 999999999}):
            old = {key: getattr(self.lote, key) for key in values}; LoteInventario.objects.filter(pk=self.lote.pk).update(**values)
            try:
                for operation in ("lot", "transfer"):
                    with patch.object(views, "_asegurar_bodegas_farmaceuticas", side_effect=AssertionError("Guard before initializer")): self.denied(operation, early=False)
            finally: LoteInventario.objects.filter(pk=self.lote.pk).update(**old)

    def test_corrupt_stock_including_zero_quantity_hidden_rows_denied_all_scope(self):
        for values in ({"empresa_id": self.externa.pk}, {"bodega_id": self.bodega_externa.pk}, {"lote_id": self.lote_externo.pk}):
            old = {key: getattr(self.existencia, key) for key in values}; ExistenciaLoteBodega.objects.filter(pk=self.existencia.pk).update(cantidad=Decimal("0"), **values)
            try: self.denied("transfer", early=False); self.denied("lot", "post", self.data(), early=False)
            finally: ExistenciaLoteBodega.objects.filter(pk=self.existencia.pk).update(cantidad=Decimal("20"), **old)

    def test_corrupt_general_inventory_relationship_denied_before_any_mutation(self):
        inventory = InventarioProducto.objects.get(producto=self.producto); InventarioProducto.objects.filter(pk=inventory.pk).update(empresa=self.externa)
        try: self.denied(early=False); self.denied("transfer", "post", self.data("transfer"), early=False)
        finally: InventarioProducto.objects.filter(pk=inventory.pk).update(empresa=self.empresa)

    def test_unrelated_other_company_corruption_does_not_poison_own_scope(self):
        LoteInventario.objects.filter(pk=self.lote_externo.pk).update(proveedor_id=999999999)
        try: self.assert_mobile(self.immutable()); self.assert_mobile(self.immutable("transfer"), "transfer")
        finally: LoteInventario.objects.filter(pk=self.lote_externo.pk).update(proveedor=self.proveedor_externo)

    def test_missing_configuration_and_bodegas_preserve_original_get_initializers(self):
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).delete(); before = self.snapshot(); response = self.client.get(self.url("transfer"))
        self.assert_mobile(response, "transfer"); self.assertTrue(response.context["readiness_reason"])
        self.assertEqual(self.changes(before), {"core.configuracionavanzadaempresa", "core.registroauditoria"})
        company = self.empresas["luque_aestetic"]; missing = self.bodegas[company.slug][1]; missing.delete(); before = self.snapshot()
        response = self.client.get(self.url(company=company)); self.assert_mobile(response)
        self.assertEqual(self.changes(before), {"facturacion.bodegainventario", "core.registroauditoria"})

    def test_original_initializer_renames_reactivates_not_readonly_claim(self):
        warehouse = self.bodegas[self.empresa.slug][1]; BodegaInventario.objects.filter(pk=warehouse.pk).update(nombre="Nombre antiguo QA", activa=False)
        before = self.snapshot(); response = self.client.get(self.url()); self.assert_mobile(response); warehouse.refresh_from_db()
        self.assertEqual(warehouse.nombre, "Bodega Hospital"); self.assertTrue(warehouse.activa); self.assertEqual(self.changes(before), {"facturacion.bodegainventario", "core.registroauditoria"})

    def test_entry_alias_get_head_fefo_safe_redirect_and_post_not_new_flow(self):
        for company in self.empresas.values():
            for method in ("get", "head"):
                response = self.immutable("entry", company, method); self.assertEqual(response.status_code, 302); self.assertEqual(response["Location"], self.url(company=company))
        self.denied("entry", "post", self.data())

    def test_hospital_quick_json_route_not_replaced_or_adapted_other_companies(self):
        self.role(puede_inventario=True, puede_ajustar_inventario=True)
        for slug in ("medical_spa", "luque_aestetic", "serviciosmedicos"):
            response = self.client.post(reverse("entrada_inventario_rapida_app", args=[slug]), {"producto_id": self.productos[slug].pk, "cantidad": "1"})
            self.assertEqual(response.status_code, 404); self.assertFalse(response.json()["ok"])
        response = self.client.get(reverse("entrada_inventario_rapida_app", args=[self.empresa.slug])); self.assertEqual(response.status_code, 405)

    def test_original_web_templates_and_outside_company_paths_unchanged(self):
        self.assertTemplateUsed(self.client.get(self.url(mobile=False)), "facturacion/crear_lote_inventario.html")
        self.assertTemplateUsed(self.client.get(self.url("transfer", mobile=False)), "facturacion/traslado_inventario_farmaceutico.html")
        self.assertTemplateUsed(self.client.get(self.url("transfer", self.externa)), "facturacion/traslado_inventario_farmaceutico.html")

    def test_pure_capability_actions_do_not_query_stock_or_grant_extra_access(self):
        from .mobile_stock_operations import acciones_stock_app, stock_disponible_app
        request = self.request(); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(stock_disponible_app(request, self.empresa)); self.assertEqual(set(acciones_stock_app(request, self.empresa)), {"app_stock_entry_url", "app_stock_transfer_url"})
        self.no_writes(captured); self.assertEqual(self.snapshot(), before); self.assertFalse(any('"facturacion_' in row["sql"].lower() for row in captured))
        response = self.immutable(); self.assertFalse(response.context["app_home_disponible"]); self.assertFalse(self.usuario.tiene_permiso_erp("puede_productos", self.empresa)); self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa))

    def test_forms_only_original_fields_safe_navigation_no_manifest_or_web_actions(self):
        for operation in ("lot", "transfer"):
            response = self.immutable(operation); markup = _StockOperationMarkup(response.content.decode())
            self.assertFalse(any(attrs.get("rel") == "manifest" for _tag, attrs in markup.nodes))
            for tag, attrs in markup.nodes:
                if tag == "form": self.assertEqual(attrs.get("action"), self.url(operation))
                if attrs.get("href"): self.assertNotIn("/contabilidad/", attrs["href"]); self.assertNotIn("/clinica/pacientes/", attrs["href"])

    def test_csrf_original_session_notice_then_valid_post_original_save(self):
        from core.views import SESSION_EXPIRED_MESSAGE_KEY
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario); before = self.snapshot()
        response = strict.post(self.url(), self.data()); self.assertEqual(response.status_code, 302); self.assertEqual(strict.session[SESSION_EXPIRED_MESSAGE_KEY], "Vuelve a iniciar sesion para continuar."); self.assertEqual(self.changes(before), {"sessions.session"})
        strict.cookies["csrftoken"] = "a"*32; before = self.snapshot()
        response = strict.post(self.url(), {**self.data(), "csrfmiddlewaretoken": "a"*32}); self.assertEqual(response.status_code, 302); self.assertTrue(self.changes(before) <= self.ALLOWED_LOT)
