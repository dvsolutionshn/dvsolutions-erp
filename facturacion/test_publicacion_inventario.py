"""La entrada de lotes conserva unidos el stock, sus kardex y la contabilidad."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.models import EmpresaModulo, Modulo, Usuario
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, PeriodoContable

from . import test_inventario_contable as datos_inventario
from .models import (
    BodegaInventario, ExistenciaLoteBodega, InventarioProducto, LoteInventario,
    MovimientoInventario, MovimientoLoteBodega,
)


class PublicacionInventarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos_inventario.InventarioContableTests.setUpTestData.__func__(cls)
        modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        cls.usuario = Usuario.objects.create_user(
            username="publicacion_inventario", password="prueba-inventario", empresa=cls.empresa,
            is_superuser=True, is_staff=True,
        )
        cls.bodega = BodegaInventario.objects.create(
            empresa=cls.empresa, nombre="Bodega principal", tipo="principal", activa=True,
        )
        cls.inventario = InventarioProducto.objects.create(
            empresa=cls.empresa, producto=cls.producto, existencias=Decimal("7.00"),
            valor_contable=Decimal("420.00"), stock_minimo=Decimal("2.00"),
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("crear_lote_inventario", args=[self.empresa.slug])

    def intentar_entrada(self):
        with patch("facturacion.views._empresa_usa_inventario_por_lotes", return_value=True), patch(
            "facturacion.views._empresa_usa_control_lotes_fefo", return_value=False
        ):
            return self.client.post(self.url, {
                "producto": str(self.producto.pk), "bodega": str(self.bodega.pk),
                "numero_lote": "LOTE-PUBLICACION", "cantidad": "1.00",
                "fecha_vencimiento": "2031-06-15", "fecha_vencimiento_rapida": "",
                "proveedor": "", "observacion": "Entrada de prueba contable",
            })

    def assert_entrada_revertida(self, respuesta, motivo):
        self.assertEqual(respuesta.status_code, 200)
        contenido = respuesta.content.decode("utf-8").lower()
        self.assertTrue(
            motivo in contenido or "no fue posible registrar el movimiento inicial" in contenido,
            "El rechazo debe mostrarse al usuario en español.",
        )
        self.assertContains(respuesta, "LOTE-PUBLICACION")
        for modelo in (LoteInventario, ExistenciaLoteBodega, MovimientoLoteBodega, MovimientoInventario, AsientoContable):
            with self.subTest(modelo=modelo.__name__):
                self.assertFalse(modelo.objects.filter(empresa=self.empresa).exists())
                self.assertFalse(modelo.objects.filter(empresa=self.otra_empresa).exists())
        self.inventario.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(self.inventario.existencias, Decimal("7.00"))
        self.assertEqual(self.inventario.valor_contable, Decimal("420.00"))
        self.assertEqual(self.inventario.stock_minimo, Decimal("2.00"))
        self.assertEqual(self.producto.costo_promedio, Decimal("60.0000"))

    def test_mes_cerrado_revierte_lote_stock_y_ambos_kardex(self):
        cuenta = CuentaContable.objects.create(
            empresa=self.empresa, codigo="310999", nombre="Inventario de apertura", tipo="patrimonio",
        )
        ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).update(cuenta_entradas_inventario=cuenta)
        PeriodoContable.objects.create(
            empresa=self.empresa, anio=self.fecha.year, mes=self.fecha.month, estado="cerrado",
        )
        self.assert_entrada_revertida(self.intentar_entrada(), "cerrado")

    def test_falta_contrapartida_revierte_lote_stock_y_ambos_kardex(self):
        ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).update(cuenta_entradas_inventario=None)
        self.assert_entrada_revertida(self.intentar_entrada(), "contrapartida")
