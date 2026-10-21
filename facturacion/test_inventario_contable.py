"""Compras, devoluciones y valoración contable con aislamiento por empresa."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo
from contabilidad.models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, PeriodoContable
from contabilidad.services import registrar_asiento_compra_aplicada, registrar_asiento_factura_emitida, registrar_asiento_movimiento_inventario_manual, registrar_asiento_nota_credito

from .costos_inventario import cantidad_disponible_para_linea, validar_devoluciones_nota
from .models import (
    CAI, Cliente, CompraInventario, Factura, InventarioProducto, LineaCompraInventario,
    LineaFactura, LineaNotaCredito, MovimientoInventario, NotaCredito, Producto, TipoImpuesto,
)
from .views import (
    _aplicar_compra_documento, _reconstruir_nota_credito_emitida,
    _registrar_entrada_nota_credito, _revertir_compra_documento,
    _registrar_movimiento_inventario, _registrar_salida_factura, _revertir_salida_factura,
)


class InventarioContableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fecha = timezone.localdate()
        cls.empresa = Empresa.objects.create(nombre="Inventario contable", slug="inv-contable", rtn="08011999111311")
        cls.otra_empresa = Empresa.objects.create(nombre="Inventario independiente", slug="inv-independiente", rtn="08011999111312")
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        for empresa in (cls.empresa, cls.otra_empresa):
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            ConfiguracionContableEmpresa.objects.create(empresa=empresa, isv_compras_recuperable=empresa == cls.empresa)
            for uso, tipo in (("factura", "01"), ("nota_credito", "03")):
                CAI.objects.create(
                    empresa=empresa, numero_cai=f"CAI-{empresa.pk}-{uso}", uso_documento=uso,
                    establecimiento="001", punto_emision="001", tipo_documento=tipo,
                    rango_inicial=1, rango_final=999, correlativo_actual=0,
                    fecha_activacion=cls.fecha - timedelta(days=30), fecha_limite=cls.fecha + timedelta(days=30),
                )
        cls.producto = Producto.objects.create(
            empresa=cls.empresa, nombre="Artículo valorado", precio="100.00", controla_inventario=True, costo_promedio="60.0000",
        )
        cls.otro_producto = Producto.objects.create(
            empresa=cls.otra_empresa, nombre="Artículo independiente", precio="100.00", controla_inventario=True,
        )
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente inventario")
        cls.impuesto = TipoImpuesto.objects.create(nombre="ISV inventario 15", porcentaje=Decimal("15.00"))

    def compra(self, producto=None, **valores):
        producto = producto or self.producto
        compra = CompraInventario.objects.create(
            empresa=producto.empresa, proveedor_nombre="Proveedor inventario", condicion_pago="credito",
            fecha_documento=self.fecha, estado="aplicada",
        )
        datos = dict(cantidad=Decimal("10.00"), costo_unitario=Decimal("100.0000"), descuento_porcentaje=Decimal("10.00"), impuesto=self.impuesto)
        datos.update(valores)
        linea = LineaCompraInventario.objects.create(compra=compra, producto=producto, **datos)
        return compra, linea

    def factura(self, cantidad="5.00", costo="40.0000"):
        factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, fecha_emision=self.fecha,
            fecha_vencimiento=self.fecha, moneda="HNL", tipo_cambio=1, estado="borrador",
        )
        linea = LineaFactura.objects.create(
            factura=factura, producto=self.producto, cantidad=Decimal(cantidad), costo_unitario=Decimal(costo),
            precio_unitario=Decimal("100.00"), impuesto=self.impuesto,
        )
        factura.calcular_totales()
        factura.estado = "emitida"
        factura.save()
        return factura, linea

    def nota(self, factura, cantidad="2.00", origen=None, emitir=False):
        nota = NotaCredito.objects.create(
            empresa=self.empresa, factura_origen=factura, cliente=self.cliente, fecha_emision=self.fecha,
            moneda=factura.moneda, tipo_cambio=factura.tipo_cambio, estado="borrador",
        )
        datos = dict(nota_credito=nota, producto=self.producto, cantidad=Decimal(cantidad), precio_unitario=Decimal("100.00"), impuesto=self.impuesto)
        if origen:
            datos["linea_factura_origen"] = origen
        linea = LineaNotaCredito.objects.create(**datos)
        nota.calcular_totales()
        if emitir:
            nota.estado = "emitida"
        nota.save()
        return nota, linea

    def saldo_asiento(self, asiento, codigo):
        montos = asiento.lineas.filter(cuenta__codigo=codigo).aggregate(debe=Sum("debe"), haber=Sum("haber"))
        return (montos["debe"] or Decimal("0")) - (montos["haber"] or Decimal("0"))

    def test_descuento_isv_recuperable_coinciden_inventario_y_asiento(self):
        compra, linea = self.compra()
        _aplicar_compra_documento(compra)
        asiento = registrar_asiento_compra_aplicada(compra)
        self.producto.refresh_from_db()
        linea.refresh_from_db()
        self.assertEqual(compra.total_documento, Decimal("1035.00"))
        self.assertEqual(self.producto.costo_promedio, Decimal("90.0000"))
        self.assertEqual(linea.costo_contable_total, Decimal("900.00"))
        self.assertTrue(linea.isv_recuperable_capturado)
        self.assertEqual(self.saldo_asiento(asiento, "112001"), Decimal("900.00"))
        self.assertEqual(self.saldo_asiento(asiento, "113001"), Decimal("135.00"))
        self.assertEqual(asiento.total_debe, Decimal("1035.00"))
        self.assertEqual(asiento.total_haber, Decimal("1035.00"))
        self.assertEqual(MovimientoInventario.objects.get(compra_documento=compra).valor_contable, Decimal("900.00"))

    def test_empresa_sin_isv_recuperable_capitaliza_solo_su_impuesto(self):
        compra, linea = self.compra(producto=self.otro_producto)
        _aplicar_compra_documento(compra)
        asiento = registrar_asiento_compra_aplicada(compra)
        self.otro_producto.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(self.otro_producto.costo_promedio, Decimal("103.5000"))
        self.assertEqual(self.producto.costo_promedio, Decimal("60.0000"))
        self.assertEqual(self.saldo_asiento(asiento, "112001"), Decimal("1035.00"))
        self.assertEqual(self.saldo_asiento(asiento, "113001"), Decimal("0.00"))
        self.assertFalse(ConfiguracionContableEmpresa.objects.get(empresa=self.otra_empresa).isv_compras_recuperable)

    def test_promedio_ponderado_usa_valor_neto_con_existencias_anteriores(self):
        self.producto.costo_promedio = Decimal("50.0000")
        self.producto.save(update_fields=["costo_promedio"])
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="2.00")
        compra, _linea = self.compra()
        _aplicar_compra_documento(compra)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.costo_promedio, Decimal("83.3333"))
        self.assertEqual((self.producto.inventario.existencias * self.producto.costo_promedio).quantize(Decimal("0.01")), Decimal("1000.00"))

    def test_isv_recuperable_tambien_se_separa_de_compra_no_inventariable(self):
        servicio = Producto.objects.create(empresa=self.empresa, nombre="Servicio adquirido", precio="100.00", controla_inventario=False)
        compra, _linea = self.compra(producto=servicio)
        asiento = registrar_asiento_compra_aplicada(compra)
        self.assertEqual(self.saldo_asiento(asiento, "113001"), Decimal("135.00"))
        self.assertEqual(asiento.total_debe, Decimal("1035.00"))
        self.assertFalse(MovimientoInventario.objects.filter(compra_documento=compra).exists())

    def test_reversion_conserva_politica_capturada_despues_de_cambiar_configuracion(self):
        self.producto.costo_promedio = Decimal("50.0000")
        self.producto.save(update_fields=["costo_promedio"])
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="2.00")
        compra, linea = self.compra()
        _aplicar_compra_documento(compra)
        asiento = registrar_asiento_compra_aplicada(compra)
        ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).update(isv_compras_recuperable=False)
        _revertir_compra_documento(compra)
        self.producto.refresh_from_db()
        linea.refresh_from_db()
        self.assertEqual(self.producto.costo_promedio, Decimal("50.0000"))
        self.assertEqual(self.producto.inventario.existencias, Decimal("2.00"))
        self.assertTrue(linea.isv_recuperable_capturado)
        self.assertEqual(registrar_asiento_compra_aplicada(compra).pk, asiento.pk)
        self.assertEqual(MovimientoInventario.objects.get(compra_documento=compra, tipo="reversion_compra").valor_contable, Decimal("900.00"))

    def test_compra_valorada_no_admite_cambio_de_cantidad(self):
        compra, linea = self.compra()
        _aplicar_compra_documento(compra)
        linea.refresh_from_db()
        linea.cantidad = Decimal("11.00")
        with self.assertRaisesMessage(ValidationError, "ya valorada"):
            linea.save()

    def test_compra_rechaza_producto_de_otra_empresa(self):
        compra = CompraInventario.objects.create(empresa=self.empresa, proveedor_nombre="Proveedor", fecha_documento=self.fecha)
        with self.assertRaisesMessage(ValidationError, "misma empresa"):
            LineaCompraInventario.objects.create(compra=compra, producto=self.otro_producto, cantidad=1, costo_unitario=1)

    def test_descuento_total_no_resucita_el_costo_bruto(self):
        compra, _linea = self.compra(descuento_porcentaje=Decimal("100.00"))
        self.assertEqual(compra.total_documento, Decimal("0.00"))
        _aplicar_compra_documento(compra)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.costo_promedio, Decimal("0.0000"))

    def test_periodo_cerrado_impide_valorar_y_contabilizar_compra(self):
        compra, linea = self.compra()
        PeriodoContable.objects.create(empresa=self.empresa, anio=self.fecha.year, mes=self.fecha.month, estado="cerrado")
        for operacion in (_aplicar_compra_documento, registrar_asiento_compra_aplicada):
            with self.assertRaisesMessage(ValidationError, "cerrado"):
                operacion(compra)
        linea.refresh_from_db()
        self.assertIsNone(linea.costo_contable_total)
        self.assertFalse(MovimientoInventario.objects.filter(compra_documento=compra).exists())

    def test_devolucion_usa_costo_original_y_valora_promedio_y_asiento(self):
        factura, origen = self.factura()
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="3.00")
        nota, linea = self.nota(factura, emitir=True)
        self.assertEqual(linea.linea_factura_origen_id, origen.pk)
        self.assertEqual(linea.costo_unitario, Decimal("40.0000"))
        _registrar_entrada_nota_credito(nota)
        asiento = registrar_asiento_nota_credito(nota)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.inventario.existencias, Decimal("5.00"))
        self.assertEqual(self.producto.costo_promedio, Decimal("52.0000"))
        self.assertEqual(self.saldo_asiento(asiento, "112001"), Decimal("80.00"))
        self.assertEqual(MovimientoInventario.objects.get(nota_credito=nota).valor_contable, Decimal("80.00"))

    def test_costo_cero_original_no_se_sustituye_por_promedio_actual(self):
        factura, origen = self.factura()
        LineaFactura.objects.filter(pk=origen.pk).update(costo_unitario=Decimal("0.0000"))
        nota, linea = self.nota(factura, cantidad="1.00")
        self.assertEqual(linea.costo_unitario, Decimal("0.0000"))

    def test_devoluciones_parciales_acumuladas_no_exceden_cantidad_original(self):
        factura, origen = self.factura()
        self.nota(factura, cantidad="3.00", emitir=True)
        self.assertEqual(cantidad_disponible_para_linea(origen), Decimal("2.00"))
        with self.assertRaisesMessage(ValidationError, "cantidad original"):
            self.nota(factura, cantidad="3.00")

    def test_borrador_se_revalida_al_emitir_si_otro_consumio_su_cantidad(self):
        factura, _origen = self.factura()
        borrador, _linea = self.nota(factura, cantidad="4.00")
        self.nota(factura, cantidad="3.00", emitir=True)
        borrador.estado = "emitida"
        with self.assertRaisesMessage(ValidationError, "cantidad original"):
            borrador.save()

    def test_anulacion_de_nota_libera_cantidad_para_otra_devolucion(self):
        factura, origen = self.factura()
        nota, _linea = self.nota(factura, cantidad="3.00", emitir=True)
        nota.estado = "anulada"
        nota.save(update_fields=["estado"])
        self.assertEqual(cantidad_disponible_para_linea(origen), Decimal("5.00"))
        self.nota(factura, cantidad="5.00")

    def test_producto_repetido_en_factura_requiere_linea_original_inequivoca(self):
        factura, origen = self.factura()
        segunda = LineaFactura.objects.create(
            factura=factura, producto=self.producto, cantidad=2, costo_unitario=Decimal("20.0000"),
            precio_unitario=100, impuesto=self.impuesto,
        )
        with self.assertRaisesMessage(ValidationError, "línea original"):
            self.nota(factura, cantidad="1.00")
        _nota, linea = self.nota(factura, cantidad="1.00", origen=segunda)
        self.assertEqual(linea.costo_unitario, Decimal("20.0000"))
        self.assertNotEqual(linea.linea_factura_origen_id, origen.pk)

    def test_origen_de_otra_factura_no_se_mezcla(self):
        factura, _origen = self.factura()
        _otra, origen_ajeno = self.factura()
        with self.assertRaisesMessage(ValidationError, "factura y empresa"):
            self.nota(factura, cantidad="1.00", origen=origen_ajeno)

    def test_devolucion_historica_sin_vinculo_sigue_limitando_cantidad(self):
        factura, _origen = self.factura()
        nota, linea = self.nota(factura, cantidad="3.00", emitir=True)
        LineaNotaCredito.objects.filter(pk=linea.pk).update(linea_factura_origen=None)
        with self.assertRaisesMessage(ValidationError, "cantidad original"):
            self.nota(factura, cantidad="3.00")
        validar_devoluciones_nota(nota)

    def test_editar_devolucion_reemplaza_movimiento_sin_duplicar_existencias(self):
        factura, _origen = self.factura()
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="3.00")
        nota, linea = self.nota(factura, emitir=True)
        _registrar_entrada_nota_credito(nota)
        registrar_asiento_nota_credito(nota)
        linea.cantidad = Decimal("1.00")
        linea.save()
        nota.calcular_totales()
        nota.save()
        _reconstruir_nota_credito_emitida(nota)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.inventario.existencias, Decimal("4.00"))
        self.assertEqual(self.producto.costo_promedio, Decimal("55.0000"))
        self.assertEqual(MovimientoInventario.objects.filter(nota_credito=nota).count(), 1)
        self.assertEqual(MovimientoInventario.objects.get(nota_credito=nota).valor_contable, Decimal("40.00"))

    def test_venta_captura_costo_al_emitir_despues_de_compra(self):
        factura, linea = self.factura(cantidad="2.00")
        compra, _linea_compra = self.compra()
        _aplicar_compra_documento(compra)
        _registrar_salida_factura(factura)
        asiento = registrar_asiento_factura_emitida(factura)
        linea.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(linea.costo_unitario, Decimal("90.0000"))
        self.assertEqual(linea.costo_contable_total, Decimal("180.00"))
        self.assertEqual(MovimientoInventario.objects.get(factura=factura).valor_contable, Decimal("180.00"))
        self.assertEqual(self.saldo_asiento(asiento, "112001"), Decimal("-180.00"))
        self.assertEqual(self.producto.inventario.valor_contable, Decimal("720.00"))

    def test_reversion_venta_usa_snapshot_despues_de_compra_con_otro_costo(self):
        compra, _linea_compra = self.compra()
        _aplicar_compra_documento(compra)
        factura, _linea = self.factura(cantidad="2.00")
        _registrar_salida_factura(factura)
        registrar_asiento_factura_emitida(factura)
        segunda, _segunda_linea = self.compra(cantidad=Decimal("2.00"), costo_unitario=Decimal("50.0000"), descuento_porcentaje=Decimal("0.00"))
        _aplicar_compra_documento(segunda)
        _revertir_salida_factura(factura)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.inventario.existencias, Decimal("12.00"))
        self.assertEqual(self.producto.inventario.valor_contable, Decimal("1000.00"))
        self.assertEqual(MovimientoInventario.objects.get(factura=factura, tipo="reversion_factura").valor_contable, Decimal("180.00"))

    def test_venta_con_asiento_previo_conserva_costo_historico(self):
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="10.00", valor_contable="600.00")
        factura, linea = self.factura(cantidad="2.00")
        registrar_asiento_factura_emitida(factura)
        _registrar_salida_factura(factura)
        linea.refresh_from_db()
        self.assertEqual(linea.costo_unitario, Decimal("40.0000"))
        self.assertEqual(MovimientoInventario.objects.get(factura=factura).valor_contable, Decimal("80.00"))

    def test_saldo_exacto_no_pierde_centavos_con_grandes_cantidades(self):
        InventarioProducto.objects.create(
            empresa=self.empresa, producto=self.producto, existencias="1000000.00", valor_contable="60000000.01",
        )
        factura, linea = self.factura(cantidad="1000000.00")
        _registrar_salida_factura(factura)
        linea.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(linea.costo_contable_total, Decimal("60000000.01"))
        self.assertEqual(self.producto.inventario.valor_contable, Decimal("0.00"))
        self.assertEqual(self.producto.inventario.existencias, Decimal("0.00"))

    def test_ajuste_manual_actualiza_valor_capturado(self):
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="10.00", valor_contable="600.00")
        _registrar_movimiento_inventario(empresa=self.empresa, producto=self.producto, tipo="ajuste_entrada", cantidad=Decimal("2.00"))
        inventario.refresh_from_db()
        self.assertEqual(inventario.valor_contable, Decimal("720.00"))
        self.assertEqual(inventario.existencias, Decimal("12.00"))

    def test_cambio_directo_de_promedio_descarta_saldo_capturado_obsoleto(self):
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="10.00", valor_contable="600.00")
        Producto.objects.filter(pk=self.producto.pk).update(costo_promedio=Decimal("80.0000"))
        _registrar_movimiento_inventario(empresa=self.empresa, producto=self.producto, tipo="ajuste_entrada", cantidad=Decimal("1.00"))
        inventario.refresh_from_db()
        self.assertEqual(inventario.valor_contable, Decimal("880.00"))

    def test_ultima_devolucion_restituye_remanente_del_centavo(self):
        factura, origen = self.factura(cantidad="3.00")
        LineaFactura.objects.filter(pk=origen.pk).update(costo_unitario=Decimal("0.0033"), costo_contable_total=Decimal("0.01"))
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias="0.00", valor_contable="0.00")
        valores = []
        for _indice in range(3):
            nota, linea = self.nota(factura, cantidad="1.00", emitir=True)
            _registrar_entrada_nota_credito(nota)
            registrar_asiento_nota_credito(nota)
            linea.refresh_from_db()
            valores.append(linea.costo_contable_total)
        self.assertEqual(sum(valores), Decimal("0.01"))
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.inventario.valor_contable, Decimal("0.01"))

    def test_ajuste_positivo_genera_partida_de_inventario_solo_en_su_empresa(self):
        movimiento = _registrar_movimiento_inventario(
            empresa=self.empresa, producto=self.producto, tipo='ajuste_entrada', cantidad=Decimal('2.00'),
        )
        asiento = AsientoContable.objects.get(empresa=self.empresa, documento_tipo='movimiento_inventario', documento_id=movimiento.pk, evento='ajuste')
        self.assertEqual(movimiento.valor_contable, Decimal('120.00'))
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('120.00'))
        self.assertEqual(self.saldo_asiento(asiento, '420102'), Decimal('-120.00'))
        self.assertFalse(AsientoContable.objects.filter(empresa=self.otra_empresa).exists())

    def test_regalia_genera_gasto_y_rebaja_inventario_sin_venta(self):
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='10.00', valor_contable='600.00')
        movimiento = _registrar_movimiento_inventario(
            empresa=self.empresa, producto=self.producto, tipo='regalia', cantidad=Decimal('2.00'),
        )
        asiento = AsientoContable.objects.get(empresa=self.empresa, documento_tipo='movimiento_inventario', documento_id=movimiento.pk, evento='ajuste')
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('-120.00'))
        self.assertEqual(self.saldo_asiento(asiento, '610114'), Decimal('120.00'))
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.inventario.valor_contable, Decimal('480.00'))

    def test_entrada_con_contrapartida_configurada_no_inventa_proveedor(self):
        contrapartida = CuentaContable.objects.create(empresa=self.empresa, codigo='310999', nombre='Inventario de apertura', tipo='patrimonio')
        ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).update(cuenta_entradas_inventario=contrapartida)
        movimiento = _registrar_movimiento_inventario(
            empresa=self.empresa, producto=self.producto, tipo='entrada', cantidad=Decimal('2.00'),
        )
        asiento = AsientoContable.objects.get(empresa=self.empresa, documento_tipo='movimiento_inventario', documento_id=movimiento.pk)
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('120.00'))
        self.assertEqual(self.saldo_asiento(asiento, '310999'), Decimal('-120.00'))
        self.assertEqual(asiento.lineas.count(), 2)

    def test_entrada_sin_contrapartida_hace_rollback_del_stock_y_valor(self):
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='10.00', valor_contable='600.00')
        with self.assertRaisesMessage(ValidationError, 'contrapartida'):
            _registrar_movimiento_inventario(empresa=self.empresa, producto=self.producto, tipo='entrada', cantidad=Decimal('2.00'))
        inventario.refresh_from_db()
        self.assertEqual(inventario.existencias, Decimal('10.00'))
        self.assertEqual(inventario.valor_contable, Decimal('600.00'))
        self.assertFalse(MovimientoInventario.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(AsientoContable.objects.filter(empresa=self.empresa).exists())

    def test_ajuste_manual_no_cambia_stock_si_periodo_cerrado(self):
        PeriodoContable.objects.create(empresa=self.empresa, anio=self.fecha.year, mes=self.fecha.month, estado='cerrado')
        with self.assertRaisesMessage(ValidationError, 'cerrado'):
            _registrar_movimiento_inventario(empresa=self.empresa, producto=self.producto, tipo='ajuste_entrada', cantidad=Decimal('2.00'))
        self.assertFalse(MovimientoInventario.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(InventarioProducto.objects.filter(producto=self.producto).exists())

    def test_movimiento_manual_contabilizado_es_idempotente(self):
        movimiento = _registrar_movimiento_inventario(
            empresa=self.empresa, producto=self.producto, tipo='ajuste_entrada', cantidad=Decimal('2.00'),
        )
        primero = registrar_asiento_movimiento_inventario_manual(movimiento)
        segundo = registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(AsientoContable.objects.filter(documento_tipo='movimiento_inventario', documento_id=movimiento.pk).count(), 1)

    def test_ultimo_ajuste_salida_retira_el_saldo_monetario_exacto(self):
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='1000000.00', valor_contable='60000000.01')
        movimiento = _registrar_movimiento_inventario(
            empresa=self.empresa, producto=self.producto, tipo='ajuste_salida', cantidad=Decimal('1000000.00'),
        )
        inventario.refresh_from_db()
        self.assertEqual(movimiento.valor_contable, Decimal('60000000.01'))
        self.assertEqual(inventario.valor_contable, Decimal('0.00'))
        self.assertEqual(inventario.existencias, Decimal('0.00'))

    def test_venta_de_cuatro_lineas_reparte_centavos_sin_costos_negativos(self):
        self.producto.costo_promedio = Decimal('0.0050')
        self.producto.save(update_fields=['costo_promedio'])
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='4.00', valor_contable='0.02')
        factura, primera = self.factura(cantidad='4.00')
        primera.cantidad = Decimal('1.00')
        primera.save()
        for _indice in range(3):
            LineaFactura.objects.create(factura=factura, producto=self.producto, cantidad=1, precio_unitario=100, impuesto=self.impuesto)
        factura.calcular_totales()
        factura.save()
        _registrar_salida_factura(factura)
        costos = list(factura.lineas.order_by('pk').values_list('costo_contable_total', flat=True))
        self.assertEqual(sum(costos), Decimal('0.02'))
        self.assertTrue(all(costo >= 0 for costo in costos))
        self.assertEqual(MovimientoInventario.objects.filter(factura=factura).aggregate(total=Sum('valor_contable'))['total'], Decimal('0.02'))
        asiento = registrar_asiento_factura_emitida(factura)
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('-0.02'))

    def test_nota_con_cuatro_lineas_reparte_centavos_sin_costos_negativos(self):
        factura, origen = self.factura(cantidad='4.00')
        LineaFactura.objects.filter(pk=origen.pk).update(costo_unitario=Decimal('0.0050'), costo_contable_total=Decimal('0.02'))
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='0.00', valor_contable='0.00')
        nota, _primera = self.nota(factura, cantidad='1.00', origen=origen)
        for _indice in range(3):
            LineaNotaCredito.objects.create(
                nota_credito=nota, linea_factura_origen=origen, producto=self.producto,
                cantidad=1, precio_unitario=100, impuesto=self.impuesto,
            )
        nota.calcular_totales()
        nota.estado = 'emitida'
        nota.save()
        _registrar_entrada_nota_credito(nota)
        costos = list(nota.lineas.order_by('pk').values_list('costo_contable_total', flat=True))
        self.assertEqual(sum(costos), Decimal('0.02'))
        self.assertTrue(all(costo >= 0 for costo in costos))
        self.assertEqual(MovimientoInventario.objects.filter(nota_credito=nota).aggregate(total=Sum('valor_contable'))['total'], Decimal('0.02'))
        asiento = registrar_asiento_nota_credito(nota)
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('0.02'))

    def test_captura_compra_conserva_isv_documentado_si_cambia_tasa_general(self):
        compra, linea = self.compra(cantidad=Decimal('1.00'), descuento_porcentaje=Decimal('0.00'))
        TipoImpuesto.objects.filter(pk=self.impuesto.pk).update(porcentaje=Decimal('18.00'))
        _aplicar_compra_documento(compra)
        asiento = registrar_asiento_compra_aplicada(compra)
        linea.refresh_from_db()
        self.assertEqual(linea.subtotal, Decimal('100.00'))
        self.assertEqual(linea.impuesto_monto, Decimal('15.00'))
        self.assertEqual(compra.total_documento, Decimal('115.00'))
        self.assertEqual(self.saldo_asiento(asiento, '112001'), Decimal('100.00'))
        self.assertEqual(self.saldo_asiento(asiento, '113001'), Decimal('15.00'))
        self.assertEqual(asiento.total_debe, Decimal('115.00'))
        self.assertEqual(asiento.total_haber, Decimal('115.00'))

    def test_cuatro_ajustes_salida_no_reintroducen_centavos_al_promedio(self):
        self.producto.costo_promedio = Decimal('0.0050')
        self.producto.save(update_fields=['costo_promedio'])
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias='4.00', valor_contable='0.02')
        valores = []
        for _indice in range(4):
            movimiento = _registrar_movimiento_inventario(
                empresa=self.empresa, producto=self.producto, tipo='ajuste_salida', cantidad=Decimal('1.00'),
            )
            valores.append(movimiento.valor_contable)
        inventario.refresh_from_db()
        self.assertEqual(sum(valores), Decimal('0.02'))
        self.assertEqual(inventario.valor_contable, Decimal('0.00'))
        self.assertEqual(inventario.existencias, Decimal('0.00'))

    def test_guardado_completo_de_comentario_conserva_isv_de_compra_valorada(self):
        compra, linea = self.compra(cantidad=Decimal('1.00'), descuento_porcentaje=Decimal('0.00'))
        _aplicar_compra_documento(compra)
        registrar_asiento_compra_aplicada(compra)
        linea.refresh_from_db()
        TipoImpuesto.objects.filter(pk=self.impuesto.pk).update(porcentaje=Decimal('18.00'))
        linea = LineaCompraInventario.objects.select_related('impuesto').get(pk=linea.pk)
        linea.comentario = 'Aclaración documental después de aplicar la compra'
        linea.save()
        linea.refresh_from_db()
        self.assertEqual(linea.impuesto_monto, Decimal('15.00'))
        self.assertEqual(linea.costo_contable_total, Decimal('100.00'))
        self.assertEqual(compra.total_documento, Decimal('115.00'))
        self.assertEqual(linea.comentario, 'Aclaración documental después de aplicar la compra')
