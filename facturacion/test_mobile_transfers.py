"""Mobile transfers reuse the original warehouse movements without new grants."""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Sum
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .models import BodegaInventario, ExistenciaLoteBodega, Factura, InventarioProducto, LoteInventario, MovimientoInventario, MovimientoLoteBodega, Producto, Proveedor


class MobileTransfersIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ROUTES = {
        "hospital_mia": {"principal_provisional": ("principal", "provisional"), "principal_vitrina": ("principal", "vitrina"), "provisional_vitrina": ("provisional", "vitrina")},
        "serviciosmedicos": {"principal_provisional": ("principal", "provisional"), "principal_vitrina": ("principal", "vitrina"), "provisional_vitrina": ("provisional", "vitrina")},
        "luque_aestetic": {"principal_clinica": ("principal", "provisional"), "clinica_principal": ("provisional", "principal")},
    }

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.empresas, cls.bodegas, cls.productos, cls.lotes, cls.existencias = {}, {}, {}, {}, {}
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Transferencias QA {slug}", slug=slug, rtn=f"MT-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, usa_bodegas_internas=True, usa_inventario_farmaceutico=True)
            nombres = (("Bodega Principal", "principal"), ("Bodega Clinica", "provisional")) if slug == "luque_aestetic" else (("Bodega General", "principal"), ("Bodega Hospital", "provisional"), ("Vitrina", "vitrina")) if slug in {"hospital_mia", "medical_spa"} else (("Bodega principal", "principal"), ("Bodega provisional", "provisional"), ("Vitrina", "vitrina"))
            for name, tipo in nombres:
                cls.bodegas[(slug, tipo)] = BodegaInventario.objects.create(empresa=empresa, nombre=name, tipo=tipo)
            producto = Producto.objects.create(empresa=empresa, nombre=f"Producto sintético {slug}", codigo=f"MT-{index}", controla_inventario=True, precio=Decimal("10"))
            cls.productos[slug] = producto
            lote = LoteInventario.objects.create(empresa=empresa, producto=producto, numero_lote=f"MT-LOTE-{index}", fecha_vencimiento=timezone.localdate()+timedelta(days=80))
            cls.lotes[slug] = lote
            total = Decimal("0")
            for tipo, cantidad in (("principal", "20"), ("provisional", "8"), ("vitrina", "4")):
                if (slug, tipo) in cls.bodegas:
                    cls.existencias[(slug, tipo)] = ExistenciaLoteBodega.objects.create(empresa=empresa, bodega=cls.bodegas[(slug, tipo)], lote=lote, cantidad=Decimal(cantidad))
                    total += Decimal(cantidad)
            InventarioProducto.objects.create(empresa=empresa, producto=producto, existencias=total)
        cls.empresa = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Transferencias móviles sin consulta", codigo="mobile-transfer-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_transferir_inventario=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-transfers", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)

    def asignar_rol(self, empresa=None, granular=True, **permisos):
        rol = RolSistema.objects.create(nombre="Rol de transferencia restringido", codigo=f"mobile-transfers-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permisos)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, empresa=None, mobile=True, **params):
        route = reverse("traslado_rapido_farmaceutico", args=[(empresa or self.empresa).slug])
        query = {"app": "1"} if mobile else {}
        query.update(params)
        return route+("?"+urlencode(query) if query else "")

    def datos(self, empresa=None, ruta=None, **extra):
        empresa = empresa or self.empresa
        if empresa.slug == "medical_spa":
            origen, destino = "principal", "provisional"
            data = {"bodega_origen": str(self.bodegas[(empresa.slug, origen)].pk), "bodega_destino": str(self.bodegas[(empresa.slug, destino)].pk)}
        else:
            ruta = ruta or ("principal_clinica" if empresa.slug == "luque_aestetic" else "principal_vitrina")
            origen, destino = self.ROUTES[empresa.slug][ruta]
            data = {"ruta": ruta}
        data.update({"app": "1", "existencia": str(self.existencias[(empresa.slug, origen)].pk), "cantidad": "2.50", "observacion": "Trabajo sintético conservado"})
        data.update(extra)
        return data

    def snapshot(self):
        return {
            "bodegas": list(BodegaInventario.objects.order_by("pk").values_list("pk", "empresa_id", "nombre", "tipo", "activa")),
            "config": list(ConfiguracionAvanzadaEmpresa.objects.order_by("pk").values_list("pk", "empresa_id", "usa_bodegas_internas", "usa_inventario_farmaceutico")),
            "existencias": list(ExistenciaLoteBodega.objects.order_by("pk").values_list("pk", "empresa_id", "bodega_id", "lote_id", "cantidad")),
            "inventario": list(InventarioProducto.objects.order_by("pk").values_list("pk", "empresa_id", "producto_id", "existencias")),
            "movimientos": list(MovimientoLoteBodega.objects.order_by("pk").values_list("pk", "tipo", "cantidad")),
            "counts": tuple(model.objects.count() for model in (Producto, LoteInventario, MovimientoInventario, RegistroAuditoria, Factura)),
        }

    def directo(self, empresa=None, method="get", data=None, **query):
        empresa = empresa or self.empresa
        factory = RequestFactory()
        url = self.url(empresa, **query)
        request = factory.post(url, self.datos(empresa) if data is None else data) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        return views.traslado_rapido_farmaceutico(request, empresa_slug=empresa.slug)

    def assert_denied(self, response):
        self.assertIn(response.status_code, (403, 404))

    def test_four_company_mobile_templates_scope_and_passive_get_without_seed(self):
        before = self.snapshot()
        with patch.object(views, "_asegurar_bodegas_farmaceuticas") as seed, patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa") as config_seed:
            for empresa in self.empresas.values():
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "facturacion/mobile_transfer.html")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertContains(response, self.productos[empresa.slug].nombre)
                self.assertNotContains(response, "<table")
                for otra in self.empresas.values():
                    if otra.pk != empresa.pk:
                        self.assertNotContains(response, self.productos[otra.slug].nombre)
            seed.assert_not_called()
            config_seed.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_original_fixed_routes_and_medical_spa_active_selectors_are_reused(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            if empresa.slug == "medical_spa":
                extra = BodegaInventario.objects.create(empresa=empresa, nombre="Cabina sintética", tipo="otra")
                closed = BodegaInventario.objects.create(empresa=empresa, nombre="Cabina cerrada", tipo="otra", activa=False)
                response = self.client.get(self.url(empresa))
                self.assertTrue(response.context["usar_selectores_bodega"])
                self.assertIn(extra, response.context["bodegas"])
                self.assertNotIn(closed, response.context["bodegas"])
                self.assertContains(response, 'name="bodega_origen"')
                self.assertContains(response, 'name="bodega_destino"')
            else:
                self.assertFalse(response.context["usar_selectores_bodega"])
                self.assertEqual(set(response.context["rutas"]), set(self.ROUTES[empresa.slug]))
                for key, (origen, destino) in self.ROUTES[empresa.slug].items():
                    route = response.context["rutas"][key]
                    self.assertEqual((route["origen"].pk, route["destino"].pk), (self.bodegas[(empresa.slug, origen)].pk, self.bodegas[(empresa.slug, destino)].pk))

    def test_missing_configuration_and_missing_warehouses_explain_without_writes_or_post_form(self):
        for empresa in self.empresas.values():
            config = ConfiguracionAvanzadaEmpresa.objects.get(empresa=empresa)
            config.delete()
            before = self.snapshot()
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/mobile_transfer.html")
            self.assertNotContains(response, 'method="post"')
            self.assertEqual(self.snapshot(), before)
            config = ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, usa_bodegas_internas=True)
            BodegaInventario.objects.filter(empresa=empresa).delete()
            before = self.snapshot()
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'method="post"')
            self.assertEqual(self.snapshot(), before)

    def test_disabled_configuration_and_one_medical_spa_warehouse_are_robust(self):
        spa = self.empresas["medical_spa"]
        BodegaInventario.objects.filter(empresa=spa).exclude(tipo="principal").update(activa=False)
        before = self.snapshot()
        response = self.client.get(self.url(spa))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'method="post"')
        self.assertEqual(self.snapshot(), before)
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(usa_bodegas_internas=False)
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'method="post"')
        unavailable = self.client.post(self.url(), self.datos())
        self.assertEqual(unavailable.status_code, 200)
        self.assertNotContains(unavailable, 'method="post"')
        self.assertEqual(self.snapshot(), before)

    def test_app_does_not_rename_reactivate_or_disable_existing_warehouses(self):
        BodegaInventario.objects.filter(pk=self.bodegas[(self.empresa.slug, "principal")].pk).update(nombre="Nombre histórico propio")
        BodegaInventario.objects.filter(pk=self.bodegas[(self.empresa.slug, "provisional")].pk).update(activa=False)
        luque = self.empresas["luque_aestetic"]
        BodegaInventario.objects.create(empresa=luque, nombre="Bodega extra vacía", tipo="otra")
        before = self.snapshot()
        for empresa in (self.empresa, luque):
            self.assertEqual(self.client.get(self.url(empresa)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_preferred_original_warehouse_name_wins_over_older_type_and_no_inactive_fallback(self):
        for empresa in (self.empresa, self.empresas["luque_aestetic"], self.empresas["serviciosmedicos"]):
            old = self.bodegas[(empresa.slug, "principal")]
            preferred_name = old.nombre
            BodegaInventario.objects.filter(pk=old.pk).update(nombre=f"Antigua principal {empresa.slug}")
            preferred = BodegaInventario.objects.create(empresa=empresa, nombre=preferred_name, tipo="principal")
            before = self.snapshot()
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["ruta"]["origen"].pk, preferred.pk)
            self.assertGreater(preferred.pk, old.pk)
            self.assertEqual(self.snapshot(), before)
            for updates in ({"activa": False}, {"tipo": "otra"}):
                BodegaInventario.objects.filter(pk=preferred.pk).update(**updates)
                before = self.snapshot()
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["mobile_transfer_ready"])
                self.assertNotContains(response, 'method="post"')
                self.assertEqual(self.snapshot(), before)
                BodegaInventario.objects.filter(pk=preferred.pk).update(activa=True, tipo="principal")

    def test_lot_order_and_expired_inactive_lot_flags_preserve_original_read_values(self):
        lote = LoteInventario.objects.create(empresa=self.empresa, producto=self.productos[self.empresa.slug], numero_lote="MT-EXPIRADO", fecha_vencimiento=timezone.localdate()-timedelta(days=1), activo=False)
        expired = ExistenciaLoteBodega.objects.create(empresa=self.empresa, bodega=self.bodegas[(self.empresa.slug, "principal")], lote=lote, cantidad=Decimal("1"))
        mobile = self.client.get(self.url())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(list(mobile.context["existencias"]), list(web.context["existencias"]))
        self.assertEqual(mobile.context["existencias"][0].pk, expired.pk)
        item = next(item for item in mobile.context["mobile_transfer_items"] if item["id"] == expired.pk)
        self.assertTrue(item["vencido"])
        self.assertFalse(item["lote_activo"])
        self.assertContains(mobile, lote.numero_lote)

    def test_granular_transfer_permission_needs_no_inventory_calendar_or_patient_grant(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
        self.asignar_rol(puede_inventario=True, puede_ajustar_inventario=True)
        before = self.snapshot()
        for method in ("get", "post"):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(method=method)
            self.assertFalse(any(f'"{ExistenciaLoteBodega._meta.db_table}"' in query["sql"] for query in queries))
        self.assertEqual(self.snapshot(), before)

    def test_legacy_inventory_permission_preserved_without_agenda_or_granular_fallback(self):
        self.asignar_rol(granular=False, puede_inventario=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertEqual(self.client.post(self.url(), self.datos()).status_code, 302)
        self.asignar_rol(granular=False, puede_clinica=True, puede_citas=True, puede_productos=True)
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo()

    def test_calendar_permission_without_agenda_module_uses_safe_hub_and_no_manifest(self):
        self.assertFalse(self.empresa.tiene_modulo_activo("agenda_citas"))
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for granular, permissions in ((True, {"puede_transferir_inventario": True, "puede_ver_calendario": True}), (False, {"puede_inventario": True, "puede_citas": True})):
            self.asignar_rol(granular=granular, **permissions)
            before = self.snapshot()
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertEqual(response.context["app_modules_url"], hub)
            self.assertContains(response, f'href="{hub}"')
            self.assertNotContains(response, 'rel="manifest"')
            self.assertEqual(self.snapshot(), before)

    def test_membership_active_license_and_facturacion_module_guard_direct_access(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        before = self.snapshot()
        for method in ("get", "post"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(method=method)
        self.assertEqual(self.snapshot(), before)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for updates in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**updates)
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo()
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        for method in ("get", "post"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(other, method=method)

    def test_company_only_scope_other_clinic_app_flag_does_not_replace_original_web(self):
        other = Empresa.objects.create(nombre="Otra empresa sintética", slug="other_mobile_transfer", rtn="MT-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=other, modulo=self.modulo)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=other, usa_bodegas_internas=True)
        self.usuario.empresas_acceso.add(other)
        app = self.client.get(self.url(other))
        web = self.client.get(self.url(other, mobile=False))
        self.assertEqual(app.status_code, web.status_code)
        self.assertTemplateUsed(app, "facturacion/traslado_rapido_farmaceutico.html")
        self.assertTemplateNotUsed(app, "facturacion/mobile_transfer.html")

    def test_normal_web_get_keeps_original_template_routes_and_seeding(self):
        for empresa in self.empresas.values():
            original = self.client.get(self.url(empresa, mobile=False))
            mobile = self.client.get(self.url(empresa))
            self.assertEqual(original.status_code, 200)
            self.assertTemplateUsed(original, "facturacion/traslado_rapido_farmaceutico.html")
            self.assertTemplateNotUsed(original, "facturacion/mobile_transfer.html")
            self.assertEqual(set(original.context["rutas"]), set(mobile.context["rutas"]))
            self.assertEqual(list(original.context["existencias"]), list(mobile.context["existencias"]))

    def test_transfers_all_original_routes_preserve_lot_user_totals_and_two_movements(self):
        for empresa in self.empresas.values():
            keys = (None,) if empresa.slug == "medical_spa" else tuple(self.ROUTES[empresa.slug])
            for key in keys:
                data = self.datos(empresa, key)
                origin = ExistenciaLoteBodega.objects.get(pk=data["existencia"])
                target = self.bodegas[(empresa.slug, "provisional")] if empresa.slug == "medical_spa" else self.bodegas[(empresa.slug, self.ROUTES[empresa.slug][key][1])]
                old_origin = origin.cantidad
                old_target = ExistenciaLoteBodega.objects.get(bodega=target, lote=origin.lote).cantidad
                old_general = InventarioProducto.objects.get(producto=origin.lote.producto).existencias
                old_sum = ExistenciaLoteBodega.objects.filter(lote=origin.lote).aggregate(total=Sum("cantidad"))["total"]
                previous = set(MovimientoLoteBodega.objects.values_list("pk", flat=True))
                response = self.client.post(self.url(empresa), data)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])
                origin.refresh_from_db()
                self.assertEqual(origin.cantidad, old_origin-Decimal(data["cantidad"]))
                self.assertEqual(ExistenciaLoteBodega.objects.get(bodega=target, lote=origin.lote).cantidad, old_target+Decimal(data["cantidad"]))
                self.assertEqual(ExistenciaLoteBodega.objects.filter(lote=origin.lote).aggregate(total=Sum("cantidad"))["total"], old_sum)
                self.assertEqual(InventarioProducto.objects.get(producto=origin.lote.producto).existencias, old_general)
                movements = list(MovimientoLoteBodega.objects.exclude(pk__in=previous).order_by("pk"))
                self.assertEqual([(m.tipo, m.bodega_id, m.lote_id, m.empresa_id, m.usuario_id, m.cantidad) for m in movements], [("traslado_salida", origin.bodega_id, origin.lote_id, empresa.pk, self.usuario.pk, Decimal("2.50")), ("traslado_entrada", target.pk, origin.lote_id, empresa.pk, self.usuario.pk, Decimal("2.50"))])
                self.assertEqual(movements[0].referencia, movements[1].referencia)
                self.assertEqual(movements[0].observacion, data["observacion"])
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_original_atomic_transaction_rolls_back_first_movement_when_second_fails(self):
        real = views._registrar_movimiento_lote_bodega
        for empresa in self.empresas.values():
            before = self.snapshot()
            calls = []
            def fail_second(**kwargs):
                calls.append(kwargs)
                if len(calls) == 2:
                    raise RuntimeError("Fallo sintético de segundo movimiento")
                return real(**kwargs)
            with patch.object(views, "_registrar_movimiento_lote_bodega", side_effect=fail_second):
                with self.assertRaises(RuntimeError):
                    self.client.post(self.url(empresa), self.datos(empresa))
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.snapshot(), before)

    def test_app_and_web_post_use_identical_original_movements_and_quantities(self):
        for empresa in self.empresas.values():
            data = self.datos(empresa)
            response = self.client.post(self.url(empresa), data)
            self.assertEqual(response.status_code, 302)
            app_moves = list(MovimientoLoteBodega.objects.filter(empresa=empresa).order_by("pk").values_list("tipo", "bodega_id", "lote_id", "cantidad", "referencia", "observacion", "usuario_id"))
            del data["app"]
            response = self.client.post(self.url(empresa, mobile=False), data)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("app=1", response.url)
            moves = list(MovimientoLoteBodega.objects.filter(empresa=empresa).order_by("pk").values_list("tipo", "bodega_id", "lote_id", "cantidad", "referencia", "observacion", "usuario_id"))
            self.assertEqual(moves[2:], app_moves)

    def test_nonpositive_insufficient_and_blank_quantities_preserve_posted_work_without_writes(self):
        for empresa in self.empresas.values():
            for value in ("", "abc", "0", "-1", "999"):
                before = self.snapshot()
                data = self.datos(empresa, cantidad=value)
                response = self.client.post(self.url(empresa), data)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "facturacion/mobile_transfer.html")
                self.assertEqual(response.context["valores"]["cantidad"], value)
                self.assertEqual(response.context["valores"]["observacion"], data["observacion"])
                self.assertEqual(response.context["valores"]["existencia"], data["existencia"])
                self.assertEqual(self.snapshot(), before)

    def test_nonfinite_or_unrepresentable_decimal_is_bound_error_not_server_error(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "1e999999", "-1e999999", "1e-999999", "99999999999.99", "1.001"):
            before = self.snapshot()
            response = self.client.post(self.url(), self.datos(cantidad=value))
            self.assertEqual(response.status_code, 200, value)
            self.assertEqual(response.context["valores"]["cantidad"], value)
            self.assertEqual(self.snapshot(), before, value)

    def test_destination_decimal_capacity_is_validated_before_mutation(self):
        destination = self.existencias[(self.empresa.slug, "vitrina")]
        ExistenciaLoteBodega.objects.filter(pk=destination.pk).update(cantidad=Decimal("9999999999.99"))
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            response = self.client.post(self.url(), self.datos(cantidad="1"))
            self.assertEqual(response.status_code, 200)
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_original_reference_capacity_returns_bound_error_without_truncation_or_stock_write(self):
        spa = self.empresas["medical_spa"]
        BodegaInventario.objects.filter(pk=self.bodegas[(spa.slug, "principal")].pk).update(nombre="Origen " + "O"*93)
        BodegaInventario.objects.filter(pk=self.bodegas[(spa.slug, "provisional")].pk).update(nombre="Destino " + "D"*92)
        data = self.datos(spa)
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            response = self.client.post(self.url(spa), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_transfer_errors"])
            for key in ("existencia", "cantidad", "observacion", "bodega_origen", "bodega_destino"):
                self.assertEqual(response.context["valores"][key], data[key])
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_same_medical_spa_warehouse_preserves_original_error_no_mutation(self):
        spa = self.empresas["medical_spa"]
        data = self.datos(spa, bodega_destino=str(self.bodegas[(spa.slug, "principal")].pk))
        before = self.snapshot()
        response = self.client.post(self.url(spa), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La bodega destino debe ser diferente")
        self.assertEqual(response.context["valores"]["bodega_destino"], data["bodega_destino"])
        self.assertEqual(self.snapshot(), before)

    def test_foreign_nonexistent_noncanonical_and_overflow_ids_deny_before_save(self):
        spa = self.empresas["medical_spa"]
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            for empresa in self.empresas.values():
                foreign = self.existencias[("medical_spa" if empresa.slug != "medical_spa" else "hospital_mia", "principal")]
                for value in (str(foreign.pk), "abc", "0", "-1", "999999", str(2**90), "00"+str(self.existencias[(empresa.slug, "principal")].pk)):
                    self.assert_denied(self.client.post(self.url(empresa), self.datos(empresa, existencia=value)))
            for field in ("bodega_origen", "bodega_destino"):
                for value in (str(self.bodegas[("hospital_mia", "principal")].pk), "bad", str(2**90), "00"+str(self.bodegas[(spa.slug, "principal")].pk)):
                    self.assert_denied(self.client.post(self.url(spa), self.datos(spa, **{field: value})))
                    self.assert_denied(self.client.get(self.url(spa, **{field: value})))
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_fixed_route_wrong_origin_and_unknown_route_never_fall_back(self):
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            for empresa in (self.empresa, self.empresas["luque_aestetic"], self.empresas["serviciosmedicos"]):
                data = self.datos(empresa)
                data["ruta"] = "unknown"
                self.assert_denied(self.client.post(self.url(empresa), data))
                self.assert_denied(self.client.get(self.url(empresa, ruta="unknown")))
                data = self.datos(empresa, existencia=str(self.existencias[(empresa.slug, "provisional")].pk))
                self.assert_denied(self.client.post(self.url(empresa), data))
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_envelope_unknown_fields_and_mixed_route_selectors_are_rejected(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            base = QueryDict(mutable=True)
            for key, value in self.datos(empresa).items():
                base[key] = str(value)
            for key in base:
                data = base.copy()
                data.setlist(key, [base[key], base[key]])
                self.assert_denied(self.client.post(self.url(empresa), data.urlencode(), content_type="application/x-www-form-urlencoded"))
            mixed = {"ruta": "principal_vitrina"} if empresa.slug == "medical_spa" else {"bodega_origen": "1"}
            for extra in (mixed, {"accion": "eliminar"}, {"empresa": empresa.pk}, {"producto": self.productos[empresa.slug].pk}, {"app": "0"}):
                data = self.datos(empresa)
                data.update(extra)
                self.assert_denied(self.client.post(self.url(empresa), data))
            self.assert_denied(self.client.post(self.url(empresa, app="0"), self.datos(empresa)))
            self.assert_denied(self.client.get(self.url(empresa)+"&app=1"))
            self.assert_denied(self.client.get(self.url(empresa, unknown="1")))
        self.assertEqual(self.snapshot(), before)

    def test_different_valid_query_and_post_route_or_warehouse_selection_are_rejected(self):
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            self.assert_denied(self.client.post(self.url(ruta="principal_provisional"), self.datos(ruta="principal_vitrina")))
            spa = self.empresas["medical_spa"]
            for field, value in (("bodega_origen", self.bodegas[(spa.slug, "vitrina")].pk), ("bodega_destino", self.bodegas[(spa.slug, "vitrina")].pk)):
                self.assert_denied(self.client.post(self.url(spa, **{field: value}), self.datos(spa)))
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_imported_cross_company_origin_lot_product_and_destination_relations_denied(self):
        empresa, foreign = self.empresa, self.empresas["medical_spa"]
        origin = self.existencias[(empresa.slug, "principal")]
        lote = self.lotes[empresa.slug]
        target = self.existencias[(empresa.slug, "vitrina")]
        corruptions = ((ExistenciaLoteBodega, origin.pk, {"empresa": foreign}, {"empresa": empresa}),
                       (LoteInventario, lote.pk, {"empresa": foreign}, {"empresa": empresa}),
                       (LoteInventario, lote.pk, {"producto": self.productos[foreign.slug]}, {"producto": self.productos[empresa.slug]}),
                       (ExistenciaLoteBodega, target.pk, {"empresa": foreign}, {"empresa": empresa}))
        for model, pk, forged, original in corruptions:
            model.objects.filter(pk=pk).update(**forged)
            before = self.snapshot()
            with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
                self.assert_denied(self.client.post(self.url(), self.datos()))
                save.assert_not_called()
            self.assertEqual(self.snapshot(), before)
            model.objects.filter(pk=pk).update(**original)

    def test_imported_foreign_warehouse_or_supplier_not_exposed_or_moved(self):
        origin = self.existencias[(self.empresa.slug, "principal")]
        bodega = self.bodegas[(self.empresa.slug, "principal")]
        foreign = self.empresas["medical_spa"]
        BodegaInventario.objects.filter(pk=bodega.pk).update(empresa=foreign, nombre="Bodega importada extranjera")
        before = self.snapshot()
        with patch.object(views, "_registrar_movimiento_lote_bodega") as save:
            unavailable = self.client.post(self.url(), self.datos())
            self.assertEqual(unavailable.status_code, 200)
            self.assertNotContains(unavailable, 'method="post"')
            self.assertNotContains(unavailable, "Bodega importada extranjera")
            save.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        BodegaInventario.objects.filter(pk=bodega.pk).update(empresa=self.empresa, nombre="Bodega General")
        proveedor = Proveedor.objects.create(empresa=foreign, nombre="Proveedor ajeno sintético")
        LoteInventario.objects.filter(pk=origin.lote_id).update(proveedor=proveedor)
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assert_denied(response)
        self.assertNotContains(response, proveedor.nombre, status_code=response.status_code)
        self.assert_denied(self.client.post(self.url(), self.datos()))
        self.assertEqual(self.snapshot(), before)

    def test_mobile_get_head_post_methods_and_csrf_are_preserved(self):
        before = self.snapshot()
        self.assertEqual(self.client.head(self.url()).status_code, 200)
        self.assertIn(self.client.put(self.url(), "app=1", content_type="application/x-www-form-urlencoded").status_code, (403, 405))
        self.assertIn(self.client.delete(self.url()).status_code, (403, 405))
        self.assertEqual(self.snapshot(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()  # force_login legitimately audits last_login.
        self.assertEqual(protected.get(self.url()).status_code, 200)
        response = protected.post(self.url(), self.datos())
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        token = protected.cookies["csrftoken"].value
        self.assertEqual(protected.post(self.url(), self.datos(), HTTP_X_CSRFTOKEN=token).status_code, 302)

    def test_empty_existence_selection_is_original_bound_error_without_creating_anything(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            response = self.client.post(self.url(empresa), self.datos(empresa, existencia=""))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["valores"]["existencia"], "")
            self.assertEqual(response.context["valores"]["cantidad"], "2.50")
        self.assertEqual(self.snapshot(), before)

    def test_hub_transfer_link_is_additive_role_scoped_and_does_not_grant_calendar(self):
        for empresa in self.empresas.values():
            hub = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
            response = self.client.get(hub)
            self.assertEqual(response.status_code, 200)
            card = next(item for item in response.context["modulos_app"] if item["key"] == "transferencias")
            self.assertEqual(card["estado"], "parcial")
            self.assertEqual(card["url"], self.url(empresa))
            self.assertEqual(self.client.get(card["url"]).status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            metadata_url = reverse("agenda_mobile_modules", args=[empresa.slug])
            metadata = self.client.get(metadata_url)
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["inicio_url"], hub)
            metadata_card = next(item for item in metadata.json()["modulos"] if item["key"] == "transferencias")
            self.assertEqual(metadata_card["url"], card["url"])
            before = self.snapshot()
            self.assertIn(self.client.post(metadata_url, {"app": "1"}).status_code, (302, 403, 405))
            self.assertEqual(self.snapshot(), before)
            from crm import views as crm_views
            request = RequestFactory().post(metadata_url, {"app": "1"})
            request.user = self.usuario
            self.assertEqual(crm_views.agenda_mobile_modules(request, empresa_slug=empresa.slug).status_code, 405)
            self.asignar_rol(empresa, puede_inventario=True)
            response = self.client.get(hub)
            self.assertFalse(any(item["key"] == "transferencias" and item["url"] for item in response.context["modulos_app"]))

    def test_catalog_helper_is_pure_and_missing_configuration_remains_safe_explanatory_link(self):
        from .mobile_transfers import transferencia_disponible_app
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).delete()
        before = self.snapshot()
        request = RequestFactory().get(self.url())
        request.user = self.usuario
        self.assertTrue(transferencia_disponible_app(request, self.empresa))
        response = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        card = next(item for item in response.context["modulos_app"] if item["key"] == "transferencias")
        screen = self.client.get(card["url"])
        self.assertEqual(screen.status_code, 200)
        self.assertNotContains(screen, 'method="post"')
        self.assertEqual(self.snapshot(), before)
