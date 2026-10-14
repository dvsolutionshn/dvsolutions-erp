from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import Empresa
from facturacion.models import Factura, LineaFactura, LineaNotaCredito, MovimientoInventario, NotaCredito, PagoComisionProveedor, PagoFactura, TipoImpuesto

from .models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, PeriodoContable
from .moneda import convertir_partidas_balanceadas, importe_historico_parcial, validar_moneda_tipo_cambio
from .services import obtener_cuenta_operativa, registrar_asiento_documento, registrar_asiento_factura_emitida, registrar_asiento_movimiento_inventario_manual, registrar_asiento_nota_credito, registrar_asiento_pago_cliente, registrar_asiento_pago_comision_proveedor, registrar_reversion_documento
from . import tests as pruebas_base


class ConversionMonetariaTests(SimpleTestCase):
    def test_tasa_hnl_se_normaliza_y_usd_rechaza_valores_invalidos(self):
        self.assertEqual(validar_moneda_tipo_cambio("HNL", 25), ("HNL", Decimal("1.000000")))
        for tasa in [0, -1, None, "NaN", "Infinity", "texto"]:
            with self.subTest(tasa=tasa), self.assertRaises(ValidationError):
                validar_moneda_tipo_cambio("USD", tasa)

    def test_conversion_distribuye_centavos_sin_descuadrar_impuestos(self):
        lineas = convertir_partidas_balanceadas([
            {"cuenta": "clientes", "debe": ".03", "haber": 0},
            {"cuenta": "ventas", "debe": 0, "haber": ".01"},
            {"cuenta": "isv_por_pagar", "debe": 0, "haber": ".02"},
        ], "USD", "25.5")
        self.assertEqual(sum(linea["debe"] for linea in lineas), Decimal(".77"))
        self.assertEqual(sum(linea["haber"] for linea in lineas), Decimal(".77"))
        self.assertEqual(lineas[0]["debe_origen"], Decimal(".03"))

    def test_ultima_cuota_cancela_centavo_historico(self):
        acumulado = Decimal("0")
        originales = Decimal("0")
        cuotas = []
        for importe in [Decimal(".01")] * 3:
            cuota = importe_historico_parcial(
                total_origen=".03", total_lempiras=".77", aplicado_origen=originales,
                aplicado_lempiras=acumulado, importe_origen=importe,
            )
            cuotas.append(cuota)
            acumulado += cuota
            originales += importe
        self.assertEqual(cuotas, [Decimal(".26"), Decimal(".25"), Decimal(".26")])
        self.assertEqual(acumulado, Decimal(".77"))

    def test_no_admite_aplicaciones_que_exceden_original(self):
        with self.assertRaises(ValidationError):
            importe_historico_parcial(total_origen=1, total_lempiras=25,
                                      aplicado_origen=1, aplicado_lempiras=25, importe_origen=".01")


class MonedaContableTests(TestCase):
    def setUp(self):
        pruebas_base.ContabilidadTests.setUp(self)
        for cai in [self.cai_factura, self.cai_nota]:
            cai.fecha_activacion = date(2020, 1, 1)
            cai.fecha_limite = date(2030, 12, 31)
            cai.save(update_fields=["fecha_activacion", "fecha_limite"])

    def _factura(self, *, precio="100", cantidad=1, tasa="25", moneda="USD", impuesto=None):
        factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, vendedor=self.usuario,
            fecha_emision=date(2026, 4, 10), fecha_vencimiento=date(2026, 4, 30),
            moneda=moneda, tipo_cambio=Decimal(tasa), estado="borrador",
        )
        LineaFactura.objects.create(
            factura=factura, producto=self.producto, cantidad=cantidad,
            precio_unitario=Decimal(precio), impuesto=impuesto or self.impuesto,
        )
        factura.calcular_totales()
        factura.estado = "emitida"
        factura.save()
        return factura, registrar_asiento_factura_emitida(factura)

    def _pago(self, factura, monto, tasa=None, **kwargs):
        pago = PagoFactura.objects.create(factura=factura, monto=Decimal(monto),
                                        tipo_cambio=tasa, fecha=date(2026, 5, 5), **kwargs)
        return pago, registrar_asiento_pago_cliente(pago)

    def _nota(self, factura, *, cantidad=".5", precio="100", tasa="29"):
        nota = NotaCredito.objects.create(
            empresa=self.empresa, factura_origen=factura, cliente=self.cliente,
            vendedor=self.usuario, moneda=factura.moneda, tipo_cambio=Decimal(tasa),
            fecha_emision=date(2026, 5, 6), motivo="Devolución parcial", estado="borrador",
        )
        LineaNotaCredito.objects.create(nota_credito=nota, producto=self.producto,
                                       cantidad=Decimal(cantidad), precio_unitario=Decimal(precio),
                                       impuesto=self.impuesto)
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save()
        return nota, registrar_asiento_nota_credito(nota)

    def test_venta_usd_115_a_25_registra_2875_lempiras(self):
        factura, asiento = self._factura()
        self.assertEqual(factura.total, Decimal("115"))
        self.assertEqual(asiento.total_debe, Decimal("2875"))
        self.assertEqual(asiento.total_haber, Decimal("2875"))
        self.assertEqual(asiento.moneda_origen, "USD")
        cliente = asiento.lineas.get(cuenta__tipo="activo")
        self.assertEqual(cliente.debe_origen, Decimal("115"))
        self.assertEqual(cliente.tipo_cambio, Decimal("25"))

    def test_costo_de_venta_en_lempiras_no_se_convierte_dos_veces(self):
        self.producto.controla_inventario = True
        self.producto.tipo_item = "producto"
        self.producto.costo_promedio = Decimal("40")
        self.producto.save()
        _, asiento = self._factura()
        costo = asiento.lineas.get(cuenta__codigo="5101")
        self.assertEqual(costo.debe, Decimal("40"))
        self.assertEqual(costo.moneda_origen, "HNL")
        self.assertEqual(asiento.total_debe, Decimal("2915"))

    def test_cobro_usd_a_26_cancela_historico_y_reconoce_ganancia_115(self):
        factura, origen = self._factura()
        _, cobro = self._pago(factura, "115", Decimal("26"))
        self.assertEqual(cobro.lineas.get(cuenta__codigo="420101").haber, Decimal("115"))
        self.assertEqual(cobro.lineas.get(detalle__startswith="Ingreso").debe, Decimal("2990"))
        self.assertEqual(cobro.lineas.get(detalle__startswith="Aplicacion").haber, Decimal("2875"))
        self.assertTrue(cobro.esta_balanceado)
        origen.refresh_from_db()
        self.assertEqual(origen.total_debe, Decimal("2875"))

    def test_cobro_a_24_reconoce_perdida_cambiaria(self):
        factura, _ = self._factura()
        _, cobro = self._pago(factura, "115", Decimal("24"))
        self.assertEqual(cobro.lineas.get(cuenta__codigo="610203").debe, Decimal("115"))
        self.assertTrue(cobro.esta_balanceado)

    def test_retenciones_isv_separado_y_comision_conservan_sus_importes_originales(self):
        factura, _ = self._factura()
        pago, cobro = self._pago(
            factura, "110", Decimal("26"), retencion_isr=Decimal("3"),
            retencion_isv=Decimal("2"), separar_isv=True,
            proveedor_comision=self.proveedor, monto_comision=Decimal("10"),
        )
        self.assertEqual(cobro.lineas.get(cuenta__codigo="113003").debe, Decimal("78"))
        self.assertEqual(cobro.lineas.get(cuenta__codigo="113005").debe, Decimal("52"))
        self.assertEqual(cobro.lineas.get(cuenta__codigo="610302").debe, Decimal("260"))
        self.assertEqual(cobro.lineas.get(cuenta__codigo="420101").haber, Decimal("115"))
        self.assertEqual(pago.total_aplicado, Decimal("115"))
        self.assertTrue(cobro.esta_balanceado)

    def test_cobros_parciales_cancelan_exactamente_saldo_historico(self):
        exento = TipoImpuesto.objects.create(nombre="Exento centavos", porcentaje=0)
        factura, venta = self._factura(precio=".01", cantidad=3, tasa="25.5", impuesto=exento)
        self.assertEqual(factura.total_lempiras, Decimal(".77"))
        cuenta = venta.lineas.get(detalle__startswith="Cobro a cliente").cuenta
        creditos = []
        for _ in range(3):
            _, cobro = self._pago(factura, ".01")
            creditos.append(cobro.lineas.get(cuenta=cuenta).haber)
            self.assertTrue(cobro.esta_balanceado)
        self.assertEqual(creditos, [Decimal(".26"), Decimal(".25"), Decimal(".26")])
        self.assertEqual(sum(creditos), venta.lineas.get(cuenta=cuenta).debe)

    def test_nota_credito_toma_tasa_historica_de_factura(self):
        factura, _ = self._factura()
        _, nota = self._nota(factura)
        self.assertEqual(nota.tipo_cambio, Decimal("25"))
        self.assertEqual(nota.total_debe, Decimal("1437.50"))
        self.assertTrue(nota.esta_balanceado)
        _, cobro = self._pago(factura, "57.50", Decimal("26"))
        self.assertEqual(cobro.lineas.get(detalle__startswith="Aplicacion").haber, Decimal("1437.50"))

    def test_comision_usd_se_paga_a_tasa_de_obligacion_y_reconoce_diferencia(self):
        factura, _ = self._factura()
        origen, cobro = self._pago(factura, "115", Decimal("26"),
                                   proveedor_comision=self.proveedor, monto_comision=Decimal("10.01"))
        cuenta_banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco comisión", tipo="banco",
            cuenta_contable=obtener_cuenta_operativa(self.empresa, "bancos"),
        )
        debitos = []
        for monto in ["3.33", "3.33", "3.35"]:
            pago = PagoComisionProveedor.objects.create(
                comision_origen=origen, fecha=date(2026, 5, 6), monto=Decimal(monto),
                tipo_cambio=Decimal("27"), cuenta_financiera=cuenta_banco,
            )
            asiento = registrar_asiento_pago_comision_proveedor(pago)
            debitos.append(asiento.lineas.get(detalle__startswith="Cancelación").debe)
            self.assertTrue(asiento.esta_balanceado)
        self.assertEqual(sum(debitos), Decimal("260.26"))
        self.assertEqual(cobro.lineas.get(detalle__startswith="Comision por pagar").haber, sum(debitos))

    def test_asiento_manual_y_factura_hnl_normalizan_tasa_a_uno(self):
        _, asiento = self._factura(moneda="HNL", tasa="25")
        self.assertEqual(asiento.tipo_cambio, Decimal("1"))
        self.assertEqual(asiento.total_debe, Decimal("115"))
        manual = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="manual_prueba", documento_id=1,
            evento="alta", fecha=date(2026, 5, 6), descripcion="Aporte en lempiras",
            referencia="Manual", origen_modulo="contabilidad", lineas=[
                {"cuenta": "caja", "debe": 10, "haber": 0},
                {"cuenta": "ventas", "debe": 0, "haber": 10},
            ],
        )
        self.assertEqual(manual.moneda_origen, "HNL")
        self.assertEqual(manual.lineas.get(debe__gt=0).debe_origen, Decimal("10"))

    def test_idempotencia_preserva_moneda_tasa_y_montos_contabilizados(self):
        factura, asiento = self._factura()
        factura.tipo_cambio = 29
        segundo = registrar_asiento_factura_emitida(factura)
        self.assertEqual(segundo.pk, asiento.pk)
        self.assertEqual(segundo.tipo_cambio, Decimal("25"))
        self.assertEqual(segundo.total_debe, Decimal("2875"))

    def test_historico_usd_sin_conversion_verificada_exige_regularizacion(self):
        factura, asiento = self._factura()
        asiento.conversion_verificada = False
        asiento.save(update_fields=["conversion_verificada"])
        pago = PagoFactura.objects.create(factura=factura, monto=115, fecha=date(2026, 5, 6))
        with self.assertRaisesMessage(ValidationError, "regulariza"):
            registrar_asiento_pago_cliente(pago)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="pago_factura", documento_id=pago.pk).exists())
        asiento.refresh_from_db()
        self.assertEqual(asiento.total_debe, Decimal("2875"))

    def test_cobro_mes_abierto_no_altera_factura_en_mes_cerrado(self):
        factura, origen = self._factura()
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado="cerrado")
        _, pago = self._pago(factura, "115", Decimal("26"))
        self.assertTrue(pago.esta_balanceado)
        origen.refresh_from_db()
        self.assertEqual(origen.total_debe, Decimal("2875"))

    def test_reversion_conserva_original_y_tasa_sin_reconvertir(self):
        factura, origen = self._factura()
        reversion = registrar_reversion_documento(
            empresa=self.empresa, documento_tipo="factura", documento_id=factura.pk,
            evento_origen="emision", evento_reversion="anulacion", fecha=date(2026, 5, 6),
            descripcion="Anulación documentada", referencia="REV", origen_modulo="facturacion",
        )
        self.assertEqual(reversion.total_debe, origen.total_haber)
        self.assertEqual(reversion.moneda_origen, "USD")
        self.assertEqual(reversion.lineas.get(detalle__startswith="Reversion Cobro").haber_origen, Decimal("115"))

    def test_cuentas_diferencias_cambiarias_y_asientos_pertenecen_solo_a_empresa(self):
        otra = Empresa.objects.create(nombre="Otra empresa", slug="otra-empresa-moneda", rtn="08011999222222")
        factura, _ = self._factura()
        _, cobro = self._pago(factura, "115", Decimal("26"))
        self.assertFalse(AsientoContable.objects.filter(empresa=otra).exists())
        self.assertTrue(all(linea.cuenta.empresa_id == self.empresa.pk for linea in cobro.lineas.select_related("cuenta")))

    def test_cobro_registra_fuente_faltante_en_mes_abierto(self):
        factura, asiento = self._factura()
        asiento.delete()
        _, cobro = self._pago(factura, "115")
        fuente = AsientoContable.objects.get(empresa=self.empresa, documento_tipo="factura",
                                             documento_id=factura.pk, evento="emision")
        self.assertEqual(fuente.total_debe, Decimal("2875"))
        self.assertEqual(cobro.lineas.get(detalle__startswith="Aplicacion").haber, Decimal("2875"))

    def test_cobro_no_reconstruye_fuente_faltante_en_mes_cerrado(self):
        factura, asiento = self._factura()
        asiento.delete()
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado="cerrado")
        pago = PagoFactura.objects.create(factura=factura, monto=115, fecha=date(2026, 5, 6))
        with self.assertRaises(ValidationError):
            registrar_asiento_pago_cliente(pago)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="pago_factura", documento_id=pago.pk).exists())

    def _movimiento_manual(self, tipo="ajuste_entrada", valor="100", **kwargs):
        return MovimientoInventario.objects.create(
            empresa=self.empresa, producto=self.producto, tipo=tipo, cantidad=1,
            valor_contable=None if valor is None else Decimal(valor),
            fecha=timezone.make_aware(datetime(2026, 5, 5, 9, 0)), usuario=self.usuario, **kwargs,
        )

    def test_ajustes_y_regalias_enlazan_inventario_y_resultados_en_hnl(self):
        for tipo, codigo, debe, haber in [
            ("ajuste_entrada", "420102", Decimal("0"), Decimal("100")),
            ("ajuste_salida", "610114", Decimal("100"), Decimal("0")),
            ("regalia", "610114", Decimal("100"), Decimal("0")),
        ]:
            with self.subTest(tipo=tipo):
                asiento = registrar_asiento_movimiento_inventario_manual(self._movimiento_manual(tipo))
                contrapartida = asiento.lineas.get(cuenta__codigo=codigo)
                self.assertEqual((contrapartida.debe, contrapartida.haber), (debe, haber))
                self.assertEqual(asiento.moneda_origen, "HNL")
                self.assertEqual(asiento.tipo_cambio, Decimal("1"))
                self.assertTrue(asiento.esta_balanceado)

    def test_entrada_generica_exige_contrapartida_explicita(self):
        movimiento = self._movimiento_manual("entrada")
        with self.assertRaisesMessage(ValidationError, "Configura la contrapartida"):
            registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="movimiento_inventario", documento_id=movimiento.pk).exists())
        patrimonio = CuentaContable.objects.create(empresa=self.empresa, codigo="3101", nombre="Aporte de inventario", tipo="patrimonio")
        ConfiguracionContableEmpresa.objects.create(empresa=self.empresa, cuenta_entradas_inventario=patrimonio)
        asiento = registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertEqual(asiento.lineas.get(cuenta=patrimonio).haber, Decimal("100"))
        self.assertFalse(asiento.lineas.filter(cuenta__tipo="ingreso").exists())

    def test_ajuste_inventario_idempotente_conserva_valor_en_mes_cerrado(self):
        movimiento = self._movimiento_manual()
        asiento = registrar_asiento_movimiento_inventario_manual(movimiento)
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=5, estado="cerrado")
        movimiento.valor_contable = Decimal("200")
        repetido = registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertEqual(repetido.pk, asiento.pk)
        self.assertEqual(repetido.total_debe, Decimal("100"))
        self.assertEqual(AsientoContable.objects.filter(documento_tipo="movimiento_inventario").count(), 1)

    def test_nuevo_ajuste_no_se_contabiliza_en_mes_cerrado(self):
        movimiento = self._movimiento_manual()
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=5, estado="cerrado")
        with self.assertRaises(ValidationError):
            registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="movimiento_inventario").exists())

    def test_ajuste_inventario_rechaza_producto_de_otra_empresa(self):
        movimiento = self._movimiento_manual()
        otra = Empresa.objects.create(nombre="Producto ajeno", slug="producto-ajeno-moneda", rtn="08011999223344")
        self.producto.empresa = otra
        self.producto.save(update_fields=["empresa"])
        with self.assertRaisesMessage(ValidationError, "misma empresa"):
            registrar_asiento_movimiento_inventario_manual(movimiento)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="movimiento_inventario").exists())

    def test_ajuste_sin_valor_requiere_captura_y_valor_cero_no_crea_partida(self):
        sin_valor = self._movimiento_manual(valor=None)
        with self.assertRaisesMessage(ValidationError, "valoración"):
            registrar_asiento_movimiento_inventario_manual(sin_valor)
        self.assertIsNone(registrar_asiento_movimiento_inventario_manual(self._movimiento_manual(valor="0")))
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="movimiento_inventario").exists())

    def test_movimiento_de_venta_no_duplica_asiento_como_ajuste(self):
        venta = self._movimiento_manual("salida_factura")
        self.assertIsNone(registrar_asiento_movimiento_inventario_manual(venta))
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="movimiento_inventario").exists())

    def _comprobar_devolucion_factura_pagada(self, moneda):
        self.producto.controla_inventario = True
        self.producto.tipo_item = "producto"
        self.producto.costo_promedio = Decimal("40")
        self.producto.save()
        factura, venta = self._factura(moneda=moneda, cantidad=2)
        self._pago(factura, "230", Decimal("26") if moneda == "USD" else None)
        cuenta_cliente = venta.lineas.get(detalle__startswith="Cobro a cliente").cuenta
        credito_esperado = Decimal("2875") if moneda == "USD" else Decimal("115")
        for _ in range(2):
            _, asiento_nota = self._nota(factura, cantidad="1")
            self.assertEqual(asiento_nota.lineas.get(cuenta=cuenta_cliente).haber, credito_esperado)
            self.assertEqual(asiento_nota.lineas.get(cuenta__codigo="112001").debe, Decimal("40"))
            self.assertEqual(asiento_nota.lineas.get(cuenta__codigo="5101").haber, Decimal("40"))
            self.assertTrue(asiento_nota.esta_balanceado)
        saldo_cliente = sum(
            (linea.debe - linea.haber for linea in cuenta_cliente.lineas_asiento.filter(asiento__empresa=self.empresa)),
            Decimal("0"),
        )
        self.assertEqual(saldo_cliente, -credito_esperado * 2)
        self.assertEqual(factura.notas_credito.filter(estado="emitida").count(), 2)
        factura.refresh_from_db()
        self.assertEqual(factura.total_documento_ajustado, Decimal("0"))

    def test_devolucion_parcial_y_total_factura_hnl_pagada_conserva_credito_cliente_y_costo(self):
        self._comprobar_devolucion_factura_pagada("HNL")

    def test_devolucion_parcial_y_total_factura_usd_pagada_conserva_credito_cliente_y_costo(self):
        self._comprobar_devolucion_factura_pagada("USD")
