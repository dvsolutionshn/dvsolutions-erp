"""Valor histórico, centavos y transacciones de los ajustes de factura."""

from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa
from contabilidad.services import registrar_asiento_factura_emitida
from core.models import EmpresaModulo, Modulo

from .models import InventarioProducto, LineaFactura, MovimientoInventario, Producto
from .test_edicion_inventario import EdicionInventarioMixin


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionInventarioCostosTests(TestCase):
    # Reutiliza únicamente los datos y ayudantes, sin heredar las otras pruebas.
    modo = "simple"
    setUp = EdicionInventarioMixin.setUp
    producto_con_stock = EdicionInventarioMixin.producto_con_stock
    datos = EdicionInventarioMixin.datos
    datos_linea = EdicionInventarioMixin.datos_linea
    editar = EdicionInventarioMixin.editar
    marca = EdicionInventarioMixin.marca

    def reponer_a_costo(self, cantidad, costo):
        inventario = InventarioProducto.objects.get(producto=self.producto)
        inventario.existencias += Decimal(cantidad)
        inventario.valor_contable += Decimal(cantidad) * Decimal(costo)
        inventario.save(update_fields=["existencias", "valor_contable"])
        Producto.objects.filter(pk=self.producto.pk).update(
            costo_promedio=(inventario.valor_contable / inventario.existencias).quantize(Decimal("0.0001")),
        )

    def assert_costos(self, cantidad, valor_stock, costo_vendido):
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal(cantidad))
        self.assertEqual(inventario.valor_contable, Decimal(valor_stock))
        lineas = self.factura.lineas.filter(producto=self.producto)
        costo = sum((linea.costo_contable_total for linea in lineas), Decimal(0))
        self.assertEqual(costo, Decimal(costo_vendido))
        movimientos = MovimientoInventario.objects.filter(factura=self.factura, producto=self.producto)
        neto = sum((
            movimiento.valor_contable * (1 if movimiento.tipo == "salida_factura" else -1)
            for movimiento in movimientos
        ), Decimal(0))
        self.assertEqual(neto, costo)

    def anular(self):
        response = self.client.post(
            reverse("anular_factura", args=[self.empresa.slug, self.factura.pk]),
            {"motivo": "Anulación para comprobar conservación de valores históricos"},
        )
        self.assertEqual(response.status_code, 302)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "anulada")

    def test_aumento_y_disminuciones_conservan_costos_de_cada_salida(self):
        self.reponer_a_costo("2", "5")
        self.editar(**{"lineas-0-cantidad": "7"})
        self.assert_costos("0", "0", "20")
        self.editar(**{"lineas-0-cantidad": "5"})
        self.assert_costos("2", "10", "10")
        self.editar(**{"lineas-0-cantidad": "3"})
        self.assert_costos("4", "14", "6")
        self.anular()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("7"))
        self.assertEqual(inventario.valor_contable, Decimal("20"))
        movimientos = MovimientoInventario.objects.filter(factura=self.factura)
        self.assertEqual(sum((
            m.valor_contable * (1 if m.tipo == "salida_factura" else -1) for m in movimientos
        ), Decimal(0)), Decimal(0))

    def test_edicion_fiscal_no_revalora_costo_historico_con_promedio_actual_distinto(self):
        self.reponer_a_costo("2", "5")
        antes = self.marca()
        original = self.factura.lineas.values("costo_unitario", "costo_contable_total").get()
        self.editar(**{"lineas-0-impuesto": str(self.exento.pk), "lineas-0-precio_unitario": "20"})
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.values("costo_unitario", "costo_contable_total").get(), original)
        self.assert_costos("2", "10", "10")

    def test_salida_historica_sin_valor_no_adopta_costo_de_incrementos_posteriores(self):
        MovimientoInventario.objects.filter(factura=self.factura).update(valor_contable=None)
        self.reponer_a_costo("2", "4")
        self.editar(**{"lineas-0-cantidad": "7"})
        self.assertEqual(self.factura.lineas.get().costo_contable_total, Decimal("18"))
        self.editar(**{"lineas-0-cantidad": "6"})
        self.assertEqual(self.factura.lineas.get().costo_contable_total, Decimal("14"))
        self.editar(**{"lineas-0-cantidad": "3"})
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("4"))
        self.assertEqual(inventario.valor_contable, Decimal("12"))
        self.assertEqual(self.factura.lineas.get().costo_contable_total, Decimal("6"))
        devoluciones = MovimientoInventario.objects.filter(factura=self.factura, tipo="reversion_factura").order_by("pk")
        self.assertEqual(list(devoluciones.values_list("valor_contable", flat=True)), [Decimal("4"), Decimal("4"), Decimal("4")])
        self.anular()
        inventario.refresh_from_db()
        self.assertEqual(inventario.existencias, Decimal("7"))
        self.assertEqual(inventario.valor_contable, Decimal("18"))

    def test_redondeo_de_varias_devoluciones_e_incrementos_no_pierde_centavos(self):
        # Una salida histórica cuyo importe exacto no cabe en un costo de 2 decimales.
        LineaFactura.objects.filter(pk=self.linea.pk).update(costo_unitario="2.0020", costo_contable_total="10.01")
        MovimientoInventario.objects.filter(factura=self.factura).update(valor_contable="10.01")
        Producto.objects.filter(pk=self.producto.pk).update(costo_promedio="2.0020")
        for cantidad in ("4.99", "4.98", "3.33", "1.01"):
            with self.subTest(cantidad=cantidad):
                self.editar(**{"lineas-0-cantidad": cantidad})
                inventario = InventarioProducto.objects.get(producto=self.producto)
                vendido = self.factura.lineas.get().costo_contable_total
                self.assertEqual(inventario.valor_contable + vendido, Decimal("10.01"))
                self.assert_costos(str(Decimal(5) - Decimal(cantidad)), str(inventario.valor_contable), str(vendido))
        self.reponer_a_costo("2", "5")
        for cantidad in ("1.77", "0.01"):
            self.editar(**{"lineas-0-cantidad": cantidad})
            inventario = InventarioProducto.objects.get(producto=self.producto)
            vendido = self.factura.lineas.get().costo_contable_total
            self.assertEqual(inventario.valor_contable + vendido, Decimal("20.01"))
            self.assert_costos(str(Decimal(7) - Decimal(cantidad)), str(inventario.valor_contable), str(vendido))
        self.anular()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal(7))
        self.assertEqual(inventario.valor_contable, Decimal("20.01"))

    def test_dividir_lineas_conserva_valor_total_sin_movimientos(self):
        antes = self.marca()
        datos = self.datos(**{"lineas-0-cantidad": "3.33", "lineas-TOTAL_FORMS": "2"})
        datos.update(self.datos_linea(1, self.producto, "1.67"))
        self.editar(datos)
        self.assertEqual(self.marca(), antes)
        self.assert_costos("0", "0", "10")
        self.assertEqual(list(self.factura.lineas.order_by("pk").values_list("costo_contable_total", flat=True)), [Decimal("6.66"), Decimal("3.34")])

    def test_reemplazar_inventario_por_servicio_no_hereda_costo_congelado(self):
        servicio = Producto.objects.create(
            empresa=self.empresa, nombre="Servicio sustituto", tipo_item="servicio",
            controla_inventario=False, precio=10, costo_promedio=3,
            impuesto_predeterminado=self.exento,
        )
        self.editar(**{"lineas-0-producto": str(servicio.pk)})
        linea = self.factura.lineas.get()
        self.assertEqual(linea.producto_id, servicio.pk)
        self.assertIsNone(linea.costo_contable_total)
        self.assertEqual(linea.costo_unitario, Decimal("3"))
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual((inventario.existencias, inventario.valor_contable), (Decimal("5"), Decimal("10")))
        self.assertFalse(MovimientoInventario.objects.filter(factura=self.factura, producto=servicio).exists())
        self.assertEqual(MovimientoInventario.objects.filter(factura=self.factura, tipo="salida_factura").count(), 1)

    def test_reemplazar_inventario_por_descripcion_manual_limpia_costos(self):
        self.editar(**{"lineas-0-producto": "", "lineas-0-descripcion_manual": "Servicio descrito manualmente"})
        linea = self.factura.lineas.get()
        self.assertIsNone(linea.producto_id)
        self.assertIsNone(linea.costo_contable_total)
        self.assertEqual(linea.costo_unitario, Decimal("0"))
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual((inventario.existencias, inventario.valor_contable), (Decimal("5"), Decimal("10")))
        self.assertEqual(MovimientoInventario.objects.filter(factura=self.factura, tipo="salida_factura").count(), 1)

    def test_asiento_reconstruido_refleja_costo_original_mas_solo_incremento(self):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo)
        ConfiguracionContableEmpresa.objects.get_or_create(empresa=self.empresa)
        self.empresa.refresh_from_db()
        registrar_asiento_factura_emitida(self.factura)

        def costo_asiento(esperado):
            asientos = AsientoContable.objects.filter(
                empresa=self.empresa, documento_tipo="factura", documento_id=self.factura.pk,
                evento="emision", estado="contabilizado",
            )
            self.assertEqual(asientos.count(), 1)
            asiento = asientos.get()
            self.assertEqual(asiento.total_debe, asiento.total_haber)
            valores = asiento.lineas.filter(cuenta__codigo="5101").aggregate(debe=Sum("debe"))
            self.assertEqual(valores["debe"], Decimal(esperado))

        costo_asiento("10")
        self.reponer_a_costo("2", "5")
        antes = self.marca()
        self.editar(**{"lineas-0-impuesto": str(self.exento.pk)})
        self.assertEqual(self.marca(), antes)
        costo_asiento("10")
        self.editar(**{"lineas-0-cantidad": "7"})
        costo_asiento("20")
        self.editar(**{"lineas-0-cantidad": "5"})
        costo_asiento("10")

    def test_cantidad_cero_o_negativa_rechaza_sin_modificar_inventario(self):
        for cantidad in ("0", "-1"):
            with self.subTest(cantidad=cantidad):
                antes = self.marca()
                response = self.client.post(
                    reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]),
                    self.datos(**{"lineas-0-cantidad": cantidad}),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.factura.lineas.get().cantidad, Decimal(5))
                self.assertEqual(self.marca(), antes)
