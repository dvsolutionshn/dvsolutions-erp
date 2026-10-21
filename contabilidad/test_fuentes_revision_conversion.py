"""Las aplicaciones nuevas utilizan el respaldo revisado, conservando el legado."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

from facturacion.models import LineaNotaCredito, NotaCredito, PagoComisionProveedor, PagoFactura

from . import test_moneda_contable as moneda_base
from .models import AsientoContable, CuentaFinanciera
from .regularizacion_moneda import regularizar_factura_historica
from .revision_conversion import registrar_revision_conversion
from .services import (
    _aplicaciones_historicas_factura,
    _fuente_historica_factura,
    _revision_moneda_factura,
    asegurar_cuenta_contable_proveedor,
    obtener_cuenta_operativa,
    registrar_asiento_documento,
    registrar_asiento_pago_cliente,
    registrar_asiento_pago_comision_proveedor,
    registrar_reversion_documento,
)


class FuentesRevisionConversionTests(TestCase):
    setUp = moneda_base.MonedaContableTests.setUp
    _factura = moneda_base.MonedaContableTests._factura

    def legado_factura(self):
        factura, asiento = self._factura()
        for linea in asiento.lineas.all():
            linea.debe = linea.debe_origen
            linea.haber = linea.haber_origen
            linea.moneda_origen = "HNL"
            linea.tipo_cambio = 1
            linea.save()
        asiento.conversion_verificada = False
        asiento.save(update_fields=["conversion_verificada"])
        self.cuenta_cliente = asiento.lineas.get(detalle__startswith="Cobro a cliente").cuenta
        self.banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco de revisión integral", tipo="banco",
            cuenta_contable=obtener_cuenta_operativa(self.empresa, "bancos"),
        )
        return factura, asiento

    def revisar(self, asiento, *, tasas=None, importes=None):
        partidas = []
        for linea in asiento.lineas.all():
            importe = (importes or {}).get(linea.pk, linea.debe_origen or linea.haber_origen)
            partidas.append({
                "linea_id": linea.pk, "moneda": "USD", "importe_original": importe,
                "tipo_cambio": (tasas or {}).get(linea.pk, Decimal("25")),
            })
        return registrar_revision_conversion(
            asiento=asiento, partidas=partidas, fecha=date(2026, 6, 1),
            cuenta_contrapartida=obtener_cuenta_operativa(self.empresa, "perdida_cambiaria"),
            motivo="Verificación del documento original en dólares y de su respaldo cambiario",
            usuario=self.usuario,
        )

    def cobro_legado(self, factura, monto="50", comision="0", *, dividir=False, evento="cobro", documento=None):
        pago = documento or PagoFactura.objects.create(
            factura=factura, monto=Decimal(monto), fecha=date(2026, 4, 15),
            cuenta_financiera=self.banco, proveedor_comision=self.proveedor if Decimal(comision) else None,
            monto_comision=Decimal(comision),
        )
        lineas = [{"cuenta": self.banco.cuenta_contable, "debe": Decimal(monto)}]
        if dividir:
            lineas.extend([
                {"cuenta": self.cuenta_cliente, "haber": 20},
                {"cuenta": self.cuenta_cliente, "haber": Decimal(monto) - 20},
            ])
        else:
            lineas.append({"cuenta": self.cuenta_cliente, "haber": Decimal(monto)})
        if Decimal(comision):
            lineas.extend([
                {"cuenta": "gasto_comisiones_ventas", "debe": Decimal(comision)},
                {"cuenta": asegurar_cuenta_contable_proveedor(self.proveedor), "haber": Decimal(comision)},
            ])
        if evento == "reversion":
            lineas = [{**linea, "debe": linea.get("haber", 0), "haber": linea.get("debe", 0)} for linea in lineas]
        asiento = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk,
            evento=evento, fecha=pago.fecha, descripcion="Cobro histórico pendiente de revisar",
            moneda_origen="USD", tipo_cambio=25, conversion_verificada=False,
            referencia="Histórico USD", origen_modulo="facturacion", lineas=lineas,
        )
        return pago, asiento

    def pago_nuevo(self, factura, monto="65"):
        pago = PagoFactura.objects.create(
            factura=factura, monto=Decimal(monto), fecha=date(2026, 6, 2),
            tipo_cambio=26, cuenta_financiera=self.banco,
        )
        return registrar_asiento_pago_cliente(pago)

    def test_fuente_revisada_usa_dolares_y_tasa_de_cxc_sin_modificar_original(self):
        factura, asiento = self.legado_factura()
        self.revisar(asiento)
        self.assertEqual(_fuente_historica_factura(factura), ("USD", Decimal("25"), self.cuenta_cliente, Decimal("115"), Decimal("2875")))
        asiento.refresh_from_db()
        self.assertFalse(asiento.conversion_verificada)
        self.assertEqual(asiento.total_debe, Decimal("115"))
        nuevo = self.pago_nuevo(factura, "115")
        self.assertEqual(nuevo.lineas.get(cuenta=self.cuenta_cliente).haber, Decimal("2875"))
        self.assertEqual(nuevo.lineas.get(cuenta=obtener_cuenta_operativa(self.empresa, "ganancia_cambiaria")).haber, Decimal("115"))

    def test_aplicacion_antigua_pendiente_bloquea_y_revisada_permite_cancelar(self):
        factura, origen = self.legado_factura()
        _pago, cobro = self.cobro_legado(factura)
        self.revisar(origen)
        with self.assertRaisesMessage(ValidationError, "pendiente de revisión integral"):
            self.pago_nuevo(factura)
        self.revisar(cobro)
        pendiente = factura.pagos_facturacion.filter(fecha=date(2026, 6, 2)).get()
        nuevo = registrar_asiento_pago_cliente(pendiente)
        self.assertEqual(nuevo.lineas.get(cuenta=self.cuenta_cliente).haber, Decimal("1625"))
        self.assertEqual(_aplicaciones_historicas_factura(factura, self.cuenta_cliente), (Decimal("115"), Decimal("2875")))

    def test_partidas_divididas_no_duplican_dolares_aplicados(self):
        factura, origen = self.legado_factura()
        _pago, cobro = self.cobro_legado(factura, dividir=True)
        self.revisar(origen)
        self.revisar(cobro)
        self.assertEqual(_aplicaciones_historicas_factura(factura, self.cuenta_cliente), (Decimal("50"), Decimal("1250")))
        nuevo = self.pago_nuevo(factura)
        self.assertEqual(nuevo.lineas.get(cuenta=self.cuenta_cliente).haber, Decimal("1625"))

    def test_reversion_revisada_resta_en_vez_de_duplicar_aplicacion(self):
        factura, origen = self.legado_factura()
        pago, cobro = self.cobro_legado(factura)
        _pago, reversion = self.cobro_legado(factura, documento=pago, evento="reversion")
        self.revisar(origen)
        self.revisar(cobro)
        self.revisar(reversion)
        self.assertEqual(_aplicaciones_historicas_factura(factura, self.cuenta_cliente), (Decimal("0"), Decimal("0")))

    def test_revision_integral_compensa_parcial_sin_sumar_dos_veces_offset(self):
        factura, origen = self.legado_factura()
        _pago, cobro = self.cobro_legado(factura)
        regularizar_factura_historica(
            asiento=origen, fecha=date(2026, 5, 1), tipo_cambio=25,
            cuenta_contrapartida=obtener_cuenta_operativa(self.empresa, "ganancia_cambiaria"),
            motivo="Revisión previa del saldo pendiente de esta factura histórica", usuario=self.usuario,
        )
        self.revisar(origen)
        self.revisar(cobro)
        self.assertIsNone(_revision_moneda_factura(factura))
        self.assertEqual(_aplicaciones_historicas_factura(factura, self.cuenta_cliente), (Decimal("50"), Decimal("1250")))
        saldo = self.cuenta_cliente.lineas_asiento.filter(asiento__estado="contabilizado").aggregate(debe=Sum("debe"), haber=Sum("haber"))
        self.assertEqual(saldo["debe"] - saldo["haber"], Decimal("1625"))

    def test_comision_revisada_y_pago_revisado_respetan_saldo_y_diferencia_cambiaria(self):
        factura, _origen = self.legado_factura()
        cobro, obligacion = self.cobro_legado(factura, comision="10")
        proveedor = asegurar_cuenta_contable_proveedor(self.proveedor)
        previo = PagoComisionProveedor.objects.create(
            comision_origen=cobro, fecha=date(2026, 4, 16), monto=4,
            cuenta_financiera=self.banco, tipo_cambio=26,
        )
        egreso = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="pago_comision_proveedor", documento_id=previo.pk,
            evento="egreso", fecha=previo.fecha, descripcion="Pago histórico de comisión",
            moneda_origen="USD", tipo_cambio=26, conversion_verificada=False,
            referencia="Histórico", origen_modulo="cuentas_por_pagar",
            lineas=[{"cuenta": proveedor, "debe": 4}, {"cuenta": self.banco.cuenta_contable, "haber": 4}],
        )
        self.revisar(obligacion)
        nuevo = PagoComisionProveedor.objects.create(
            comision_origen=cobro, fecha=date(2026, 6, 2), monto=6,
            cuenta_financiera=self.banco, tipo_cambio=27,
        )
        with self.assertRaisesMessage(ValidationError, "pendiente de revisión integral"):
            registrar_asiento_pago_comision_proveedor(nuevo)
        self.revisar(egreso, tasas={egreso.lineas.get(cuenta=self.banco.cuenta_contable).pk: Decimal("26")})
        asiento = registrar_asiento_pago_comision_proveedor(nuevo)
        self.assertEqual(asiento.lineas.get(cuenta=proveedor).debe, Decimal("150"))
        self.assertEqual(asiento.lineas.get(cuenta=self.banco.cuenta_contable).haber, Decimal("162"))
        self.assertEqual(asiento.lineas.get(cuenta=obtener_cuenta_operativa(self.empresa, "perdida_cambiaria")).debe, Decimal("12"))
        self.assertTrue(asiento.esta_balanceado)

    def test_cobro_sin_asiento_impide_ocultar_saldo_aplicado(self):
        factura, origen = self.legado_factura()
        PagoFactura.objects.create(factura=factura, monto=50, fecha=date(2026, 4, 15))
        self.revisar(origen)
        with self.assertRaisesMessage(ValidationError, "sin asiento contabilizado"):
            self.pago_nuevo(factura)

    def test_comision_previa_sin_asiento_impide_pagar_otro_saldo(self):
        factura, _origen = self.legado_factura()
        cobro, obligacion = self.cobro_legado(factura, comision="10")
        PagoComisionProveedor.objects.create(comision_origen=cobro, monto=4, fecha=date(2026, 4, 16), cuenta_financiera=self.banco)
        self.revisar(obligacion)
        nuevo = PagoComisionProveedor.objects.create(comision_origen=cobro, monto=6, fecha=date(2026, 6, 2), cuenta_financiera=self.banco)
        with self.assertRaisesMessage(ValidationError, "sin asiento contabilizado"):
            registrar_asiento_pago_comision_proveedor(nuevo)

    def test_cxc_con_original_incorrecto_no_se_confirma_para_cobrar(self):
        factura, origen = self.legado_factura()
        cliente = origen.lineas.get(cuenta=self.cuenta_cliente)
        with self.assertRaises(ValidationError):
            self.revisar(origen, importes={cliente.pk: Decimal("114")})
        with self.assertRaises(ValidationError):
            _fuente_historica_factura(factura)

    def test_importes_ya_convertidos_no_reciben_otro_ajuste(self):
        factura, origen = self._factura()
        origen.conversion_verificada = False
        origen.save(update_fields=["conversion_verificada"])
        revision = self.revisar(origen)
        self.assertIsNone(revision.ajuste_id)
        self.assertEqual(_fuente_historica_factura(factura)[4], Decimal("2875"))
        origen.refresh_from_db()
        self.assertEqual(origen.total_debe, Decimal("2875"))

    def test_aplicacion_nueva_no_puede_anticipar_fecha_de_revision(self):
        factura, origen = self.legado_factura()
        self.revisar(origen)
        pago = PagoFactura.objects.create(factura=factura, monto=115, fecha=date(2026, 5, 20), cuenta_financiera=self.banco)
        with self.assertRaisesMessage(ValidationError, "igual o posterior a la revisión integral"):
            registrar_asiento_pago_cliente(pago)
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="pago_factura", documento_id=pago.pk).exists())

    def test_nota_credito_tambien_exige_revisar_los_cobros_anteriores(self):
        factura, origen = self.legado_factura()
        self.cobro_legado(factura)
        self.revisar(origen)
        with self.assertRaisesMessage(ValidationError, "pendiente de revisión integral"):
            _aplicaciones_historicas_factura(factura, self.cuenta_cliente, incluir_pagos=False)

    def test_reversion_de_fuente_o_ajuste_revisado_conserva_el_respaldo(self):
        _factura, origen = self.legado_factura()
        revision = self.revisar(origen)
        cantidad = AsientoContable.objects.count()
        for asiento in (origen, revision.ajuste):
            with self.subTest(asiento=asiento.pk), self.assertRaisesMessage(ValidationError, "revisión integral documentada"):
                registrar_reversion_documento(
                    empresa=self.empresa, documento_tipo=asiento.documento_tipo,
                    documento_id=asiento.documento_id, evento_origen=asiento.evento,
                    evento_reversion="reversion", fecha=date(2026, 6, 2),
                    descripcion="Reversión de documento revisado", referencia="Revisión",
                    origen_modulo="contabilidad", creado_por=self.usuario,
                )
        self.assertEqual(AsientoContable.objects.count(), cantidad)

    def test_nota_credito_hnl_de_factura_usd_no_se_interpreta_como_dolares(self):
        factura, origen = self.legado_factura()
        nota = NotaCredito.objects.create(
            empresa=self.empresa, factura_origen=factura, cliente=self.cliente,
            vendedor=self.usuario, moneda="USD", tipo_cambio=25,
            fecha_emision=date(2026, 4, 16), motivo="Nota histórica de moneda distinta", estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota, producto=self.producto, cantidad=Decimal(".5"),
            precio_unitario=100, impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save()
        # Reproduce los metadatos del legado importado, sin aplicar conversión.
        NotaCredito.objects.filter(pk=nota.pk).update(moneda="HNL", tipo_cambio=1)
        self.revisar(origen)
        for incluir_pagos in (True, False):
            with self.subTest(incluir_pagos=incluir_pagos), self.assertRaisesMessage(ValidationError, "moneda no coincide"):
                _aplicaciones_historicas_factura(factura, self.cuenta_cliente, incluir_pagos=incluir_pagos)
