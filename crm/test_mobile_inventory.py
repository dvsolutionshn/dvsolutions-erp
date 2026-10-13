"""La consulta móvil comparte inventario, sin escribir ni alterar el flujo actual."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from facturacion.models import (
    BodegaInventario, ExistenciaLoteBodega, InventarioProducto,
    LoteInventario, MovimientoInventario, Producto,
)
from crm.mobile_inventory import productos_inventario_app


class MobileInventoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        cls.rol = RolSistema.objects.create(nombre="Consulta de inventario", codigo="mobile-inventory-reader", puede_inventario=True)
        cls.empresas = []
        cls.productos = []
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"INV-{slug}", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            producto = Producto.objects.create(empresa=empresa, nombre=f"Gomitas {slug}", controla_inventario=True, precio=Decimal("10"))
            InventarioProducto.objects.create(empresa=empresa, producto=producto, existencias=Decimal("8"), stock_minimo=Decimal("10"))
            cls.empresas.append(empresa)
            cls.productos.append(producto)
        cls.usuario = Usuario.objects.create_user(username="mobile-inventory-reader", password="test-only", empresa=cls.empresas[0], rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas)

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_four_clinics_get_mobile_cards_with_only_current_company_data_and_no_writes(self):
        for empresa in self.empresas:
            with self.subTest(empresa=empresa.slug):
                response = self.client.get(reverse("agenda_mobile_inventory", args=[empresa.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "crm/mobile_inventory.html")
                self.assertContains(response, f"Gomitas {empresa.slug}")
                self.assertContains(response, "Stock bajo")
                self.assertContains(response, "Próximos a vencer")
                self.assertNotContains(response, "<table")
                self.assertNotContains(response, "Entrada rápida")
                self.assertIn("no-store", response["Cache-Control"])
                for otra in self.empresas:
                    if otra.pk != empresa.pk:
                        self.assertNotContains(response, f"Gomitas {otra.slug}")
                self.assertEqual(response.context["productos"], productos_inventario_app(empresa))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertEqual(list(InventarioProducto.objects.values_list("existencias", flat=True)), [Decimal("8")] * 4)

    def test_same_payload_as_existing_hospital_panel_with_fefo_bodega_quantities_and_empty_stock(self):
        empresa, producto = self.empresas[0], self.productos[0]
        bodega = BodegaInventario.objects.create(empresa=empresa, nombre="Principal", tipo="principal")
        for numero, dias, cantidad in (("TARDIO", 100, 5), ("PROXIMO", 10, 3)):
            lote = LoteInventario.objects.create(empresa=empresa, producto=producto, numero_lote=numero, fecha_vencimiento=timezone.localdate()+timedelta(days=dias))
            ExistenciaLoteBodega.objects.create(empresa=empresa, lote=lote, bodega=bodega, cantidad=Decimal(cantidad))
        vacio = Producto.objects.create(empresa=empresa, nombre="Producto vacío", controla_inventario=True, precio=Decimal("1"))
        response = self.client.get(reverse("agenda_mobile_inventory", args=[empresa.slug]))
        old = self.client.get(reverse("agenda_mobile", args=[empresa.slug]))
        self.assertEqual(response.context["productos"], old.context["inventario_productos_app_payload"])
        item = next(p for p in response.context["productos"] if p["id"] == producto.pk)
        self.assertEqual([l["numero"] for l in item["lotes"]], ["PROXIMO", "TARDIO"])
        self.assertEqual(item["bodegas"], [{"nombre": "Principal", "cantidad": 8.0}])
        self.assertTrue(item["proximo_vencer"])
        self.assertTrue(next(p for p in response.context["productos"] if p["id"] == vacio.pk)["sin_stock"])
        self.assertContains(response, "Lote PROXIMO")
        self.assertContains(response, "Sin existencia")

    def test_permission_module_company_scope_and_read_only_method_enforced(self):
        empresa = self.empresas[0]
        url = reverse("agenda_mobile_inventory", args=[empresa.slug])
        self.assertEqual(self.client.post(url, {"cantidad": 20}).status_code, 405)
        self.rol.puede_inventario = False
        self.rol.puede_productos = True
        self.rol.save(update_fields=["puede_inventario", "puede_productos"])
        self.assertEqual(self.client.get(url).status_code, 403)
        modulos = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug])).json()["modulos"]
        self.assertNotIn("inventario", {modulo["key"] for modulo in modulos})
        self.rol.puede_inventario = True
        self.rol.save(update_fields=["puede_inventario"])
        EmpresaModulo.objects.filter(empresa=empresa, modulo__codigo="facturacion").update(activo=False)
        self.assertEqual(self.client.get(url).status_code, 403)
        fuera = Empresa.objects.create(nombre="Empresa fuera de alcance", slug="fuera_inventario_app", rtn="INV-FUERA", tipo_solucion="clinica")
        self.usuario.empresas_acceso.add(fuera)
        self.assertEqual(self.client.get(reverse("agenda_mobile_inventory", args=[fuera.slug])).status_code, 404)
        inaccesible = Empresa.objects.create(nombre="Empresa sin acceso", slug="sin_acceso_inventory", rtn="INV-SIN-ACCESO")
        self.assertIn(self.client.get(reverse("agenda_mobile_inventory", args=[inaccesible.slug])).status_code, (302, 403))
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_hospital_quick_stock_entry_is_only_link_to_existing_authorized_flow(self):
        self.rol.puede_ajustar_inventario = True
        self.rol.save(update_fields=["puede_ajustar_inventario"])
        hospital = self.empresas[0]
        response = self.client.get(reverse("agenda_mobile_inventory", args=[hospital.slug]))
        self.assertContains(response, f'href="{reverse("agenda_mobile", args=[hospital.slug])}#productos-app"')
        self.assertContains(response, "Entrada rápida · Flujo actual")
        self.assertNotContains(self.client.get(reverse("agenda_mobile_inventory", args=[self.empresas[1].slug])), "Entrada rápida")
        for empresa in self.empresas:
            modulos = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug])).json()["modulos"]
            self.assertNotIn("inventario", {modulo["key"] for modulo in modulos})
            self.assertEqual(self.client.get(reverse("agenda_mobile_inventory", args=[empresa.slug])).status_code, 200)

    def test_granular_inventory_only_role_does_not_require_calendar_or_grant_other_actions(self):
        self.rol.es_rol_clinico = True
        self.rol.usa_permisos_clinicos_granulares = True
        self.rol.puede_ajustar_inventario = True
        self.rol.save(update_fields=["es_rol_clinico", "usa_permisos_clinicos_granulares", "puede_ajustar_inventario"])
        for empresa in self.empresas:
            with self.subTest(empresa=empresa.slug):
                response = self.client.get(reverse("agenda_mobile_inventory", args=[empresa.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["app_home_disponible"])
                self.assertNotContains(response, "Entrada rápida")
                hub = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
                self.assertContains(response, f'href="{hub}"')
                self.assertEqual(self.client.get(hub).status_code, 200)
                endpoint = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug]))
                self.assertEqual(endpoint.status_code, 200)
                self.assertNotIn("pacientes", {m["key"] for m in endpoint.json()["modulos"]})
                self.assertIn(self.client.post(reverse("agenda_mobile_inventory", args=[empresa.slug]), {}).status_code, (302, 403, 405))
        self.rol.refresh_from_db()
        self.assertFalse(self.rol.puede_ver_calendario)
        self.assertTrue(self.rol.puede_ajustar_inventario)
        self.assertFalse(MovimientoInventario.objects.exists())
