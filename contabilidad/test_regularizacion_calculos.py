from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from facturacion.models import PagoComisionProveedor, PagoFactura

from . import test_moneda_contable as moneda_base
from .models import AsientoContable, CuentaFinanciera, PeriodoContable
from .regularizacion_moneda import regularizar_factura_historica, vista_previa_regularizacion
from .services import obtener_cuenta_operativa, registrar_asiento_documento, registrar_asiento_pago_cliente, registrar_asiento_pago_comision_proveedor


class RegularizacionCalculosTests(TestCase):
    setUp = moneda_base.MonedaContableTests.setUp
    _factura = moneda_base.MonedaContableTests._factura
    _nota = moneda_base.MonedaContableTests._nota

    def legado(self, pago_previo=True):
        factura, asiento = self._factura()
        asiento.lineas.filter(detalle__startswith='Cobro a cliente').update(debe=Decimal('115'), moneda_origen='HNL', tipo_cambio=1)
        asiento.lineas.filter(cuenta__tipo='ingreso').update(haber=Decimal('100'), moneda_origen='HNL', tipo_cambio=1)
        asiento.lineas.filter(cuenta__tipo='pasivo').update(haber=Decimal('15'), moneda_origen='HNL', tipo_cambio=1)
        asiento.conversion_verificada = False
        asiento.save(update_fields=['conversion_verificada'])
        self.cuenta_cliente = asiento.lineas.get(detalle__startswith='Cobro a cliente').cuenta
        self.banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre='Banco de revisión', tipo='banco',
            cuenta_contable=obtener_cuenta_operativa(self.empresa, 'bancos'),
        )
        pago = None
        if pago_previo:
            pago = PagoFactura.objects.create(factura=factura, monto=50, fecha=date(2026, 4, 15), cuenta_financiera=self.banco)
            registrar_asiento_documento(
                empresa=self.empresa, documento_tipo='pago_factura', documento_id=pago.pk, evento='cobro',
                fecha=pago.fecha, descripcion='Cobro del legado', conversion_verificada=False,
                referencia='Pago del legado', origen_modulo='facturacion',
                lineas=[{'cuenta': self.banco.cuenta_contable, 'debe': 50}, {'cuenta': self.cuenta_cliente, 'haber': 50}],
            )
        return factura, asiento, pago

    def regularizar(self, asiento, **cambios):
        datos = {'asiento': asiento, 'fecha': date(2026, 5, 1), 'tipo_cambio': Decimal('25'),
                 'cuenta_contrapartida': obtener_cuenta_operativa(self.empresa, 'ganancia_cambiaria'),
                 'motivo': 'Revisión documentada del saldo original en dólares', 'usuario': self.usuario}
        datos.update(cambios)
        return regularizar_factura_historica(**datos)

    def saldo_cliente(self):
        filas = self.cuenta_cliente.lineas_asiento.filter(asiento__estado='contabilizado')
        total = filas.aggregate(debe=Sum('debe'), haber=Sum('haber'))
        return (total['debe'] or Decimal('0')) - (total['haber'] or Decimal('0'))

    def test_regularizar_en_mes_abierto_no_modifica_asientos_del_mes_cerrado(self):
        factura, asiento, _pago = self.legado()
        anteriores = list(AsientoContable.objects.filter(empresa=self.empresa).values_list('pk', 'fecha', 'estado'))
        lineas = list(asiento.lineas.values_list('pk', 'debe', 'haber'))
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado='cerrado')
        revision = self.regularizar(asiento)
        self.assertEqual(revision.saldo_original, Decimal('65'))
        self.assertEqual(revision.saldo_hnl_anterior, Decimal('65'))
        self.assertEqual(revision.saldo_hnl_corregido, Decimal('1625'))
        self.assertEqual(revision.asiento_ajuste.total_debe, Decimal('1560'))
        self.assertEqual(self.saldo_cliente(), Decimal('1625'))
        self.assertEqual(list(asiento.lineas.values_list('pk', 'debe', 'haber')), lineas)
        self.assertEqual(list(AsientoContable.objects.filter(pk__in=[item[0] for item in anteriores]).values_list('pk', 'fecha', 'estado')), anteriores)
        factura.refresh_from_db()
        self.assertEqual(factura.total, Decimal('115'))

    def test_cobros_parciales_tras_revision_cancelan_exactamente_saldo_corregido(self):
        factura, asiento, _viejo = self.legado()
        self.regularizar(asiento)
        for importe in ['20', '45']:
            pago = PagoFactura.objects.create(factura=factura, monto=importe, tipo_cambio=26, fecha=date(2026, 5, 5), cuenta_financiera=self.banco)
            cobro = registrar_asiento_pago_cliente(pago)
            self.assertTrue(cobro.esta_balanceado)
            self.assertEqual(cobro.lineas.get(cuenta=self.cuenta_cliente).haber, Decimal(importe) * 25)
        self.assertEqual(self.saldo_cliente(), Decimal('0'))

    def test_nota_credito_tras_revision_usa_base_historica_y_conserva_pagos(self):
        factura, asiento, _pago = self.legado()
        self.regularizar(asiento)
        _nota, partida = self._nota(factura, cantidad='.5')
        self.assertEqual(partida.lineas.get(cuenta=self.cuenta_cliente).haber, Decimal('1437.50'))
        self.assertEqual(self.saldo_cliente(), Decimal('187.50'))

    def test_regularizar_es_idempotente_y_no_admite_fecha_cerrada(self):
        _factura, asiento, _pago = self.legado()
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=5, estado='cerrado')
        with self.assertRaisesMessage(ValidationError, 'cerrado'):
            self.regularizar(asiento)
        revision = self.regularizar(asiento, fecha=date(2026, 6, 1))
        self.assertEqual(self.regularizar(asiento, fecha=date(2026, 6, 2)).pk, revision.pk)
        self.assertEqual(AsientoContable.objects.filter(empresa=self.empresa, evento='regularizacion_moneda').count(), 1)

    def test_revisiones_preservan_cobros_anteriores_al_editar_un_pago_nuevo(self):
        factura, asiento, viejo = self.legado()
        self.regularizar(asiento)
        anterior = AsientoContable.objects.get(documento_tipo='pago_factura', documento_id=viejo.pk)
        self.client.force_login(self.usuario)
        pago = PagoFactura.objects.create(factura=factura, monto=20, tipo_cambio=26, fecha=date(2026, 5, 5), cuenta_financiera=self.banco)
        registrar_asiento_pago_cliente(pago)
        url = reverse('editar_pago_factura', args=[self.empresa.slug, factura.pk, pago.pk])
        respuesta = self.client.post(url, {'monto': '20', 'tipo_cambio': '27', 'fecha': '2026-05-05',
            'metodo': 'transferencia', 'cuenta_financiera': self.banco.pk, 'retencion_isr': '0',
            'retencion_isv': '0', 'monto_comision': '0', 'porcentaje_comision': '0'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(AsientoContable.objects.filter(pk=anterior.pk).exists())
        self.assertEqual(self.saldo_cliente(), Decimal('1125'))
        viejo.referencia = 'Cambio posterior al cierre de revisión'
        with self.assertRaisesMessage(ValidationError, 'regularización'):
            viejo.save()

    def test_comision_pagada_impide_alterar_tasa_de_la_obligacion(self):
        factura, _asiento = self._factura()
        banco = CuentaFinanciera.objects.create(empresa=self.empresa, nombre='Banco comisión', tipo='banco', cuenta_contable=obtener_cuenta_operativa(self.empresa, 'bancos'))
        cobro = PagoFactura.objects.create(factura=factura, monto=115, tipo_cambio=25, proveedor_comision=self.proveedor, monto_comision=1)
        registrar_asiento_pago_cliente(cobro)
        pago = PagoComisionProveedor.objects.create(comision_origen=cobro, monto=1, tipo_cambio=25, cuenta_financiera=banco)
        registrar_asiento_pago_comision_proveedor(pago)
        cobro.tipo_cambio = Decimal('26')
        with self.assertRaisesMessage(ValidationError, 'pagos de comisión'):
            cobro.save()

    def test_aplicaciones_pendientes_no_crean_circulo_para_revisar_fuente(self):
        factura, asiento, _pago = self.legado(pago_previo=False)
        pendiente = PagoFactura.objects.create(factura=factura, monto=50, tipo_cambio=25, fecha=date(2026, 5, 5), cuenta_financiera=self.banco)
        self.assertEqual(vista_previa_regularizacion(asiento)['aplicaciones_pendientes'], 1)
        revision = self.regularizar(asiento)
        self.assertEqual(revision.aplicado_original, Decimal('0'))
        registrar_asiento_pago_cliente(pendiente)
        self.assertEqual(self.saldo_cliente(), Decimal('1625'))
