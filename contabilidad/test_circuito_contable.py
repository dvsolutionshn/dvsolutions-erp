from datetime import date
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa
from facturacion.models import PagoFactura

from . import test_moneda_contable as moneda_base
from . import tests as pruebas_base
from .cierre import revisar_cierre_periodo
from .models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, MovimientoBancario, PeriodoContable
from .services import obtener_cuenta_operativa
from .services import registrar_reversion_documento


class CircuitoContableTests(TestCase):
    setUp = moneda_base.MonedaContableTests.setUp
    _factura = moneda_base.MonedaContableTests._factura

    def cuenta_bancaria(self):
        return CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre='Banco del circuito', tipo='banco',
            cuenta_contable=obtener_cuenta_operativa(self.empresa, 'bancos'),
        )

    def datos_cobro(self, cuenta, **cambios):
        datos = {'monto': '115', 'tipo_cambio': '26', 'metodo': 'transferencia',
                 'cuenta_financiera': cuenta.pk, 'fecha': '2026-05-05',
                 'retencion_isr': '0', 'retencion_isv': '0', 'monto_comision': '0',
                 'porcentaje_comision': '0', 'referencia': 'Cobro en dólares'}
        datos.update(cambios)
        return datos

    def test_interfaz_cobro_usd_captura_tasa_y_contabiliza_ganancia(self):
        factura, _origen = self._factura()
        banco = self.cuenta_bancaria()
        self.client.force_login(self.usuario)
        url = reverse('registrar_pago', args=[self.empresa.slug, factura.pk])
        pantalla = self.client.get(url)
        self.assertContains(pantalla, 'Tipo de cambio del cobro')
        self.assertContains(pantalla, 'USD')
        respuesta = self.client.post(url, self.datos_cobro(banco))
        self.assertEqual(respuesta.status_code, 302)
        pago = PagoFactura.objects.get(factura=factura)
        self.assertEqual(pago.tipo_cambio, Decimal('26'))
        asiento = AsientoContable.objects.get(empresa=self.empresa, documento_tipo='pago_factura', documento_id=pago.pk)
        self.assertEqual(asiento.lineas.get(cuenta=banco.cuenta_contable).debe, Decimal('2990'))
        self.assertEqual(asiento.lineas.get(cuenta__codigo='420101').haber, Decimal('115'))
        self.assertTrue(asiento.esta_balanceado)

    def test_cobro_con_tasa_invalida_no_crea_pago_recibo_ni_asiento(self):
        factura, _origen = self._factura()
        banco = self.cuenta_bancaria()
        self.client.force_login(self.usuario)
        url = reverse('registrar_pago', args=[self.empresa.slug, factura.pk])
        antes = AsientoContable.objects.count()
        respuesta = self.client.post(url, self.datos_cobro(banco, tipo_cambio='0'))
        self.assertContains(respuesta, 'El tipo de cambio debe ser mayor que cero')
        self.assertFalse(PagoFactura.objects.filter(factura=factura).exists())
        self.assertEqual(AsientoContable.objects.count(), antes)

    def test_editar_cobro_conserva_tasa_capturada_y_permite_corregirla(self):
        factura, _origen = self._factura()
        banco = self.cuenta_bancaria()
        self.client.force_login(self.usuario)
        url = reverse('registrar_pago', args=[self.empresa.slug, factura.pk])
        self.client.post(url, self.datos_cobro(banco, monto='50'))
        pago = PagoFactura.objects.get(factura=factura)
        editar = reverse('editar_pago_factura', args=[self.empresa.slug, factura.pk, pago.pk])
        self.assertEqual(self.client.get(editar).status_code, 200)
        datos = self.datos_cobro(banco, monto='50')
        datos.pop('tipo_cambio')
        self.assertEqual(self.client.post(editar, datos).status_code, 302)
        pago.refresh_from_db()
        self.assertEqual(pago.tipo_cambio, Decimal('26'))
        datos['tipo_cambio'] = '27'
        self.assertEqual(self.client.post(editar, datos).status_code, 302)
        pago.refresh_from_db()
        self.assertEqual(pago.tipo_cambio, Decimal('27'))
        asiento = AsientoContable.objects.get(documento_tipo='pago_factura', documento_id=pago.pk)
        self.assertEqual(asiento.lineas.get(cuenta=banco.cuenta_contable).debe, Decimal('1350'))
        self.assertTrue(asiento.esta_balanceado)

    def test_configuracion_no_permite_cuentas_de_otra_empresa(self):
        otra = Empresa.objects.create(nombre='Empresa independiente', slug='independiente-circuito', rtn='08011999117771')
        cuenta = CuentaContable.objects.create(empresa=otra, codigo='1130', nombre='Crédito fiscal ajeno', tipo='activo')
        configuracion = ConfiguracionContableEmpresa(empresa=self.empresa, cuenta_isv_acreditable=cuenta)
        with self.assertRaises(ValidationError):
            configuracion.save()
        self.assertFalse(ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).exists())

    def test_deposito_hnl_enlaza_factura_usd_sin_repetir_el_ingreso(self):
        factura, _origen = self._factura()
        banco = self.cuenta_bancaria()
        movimiento = MovimientoBancario.objects.create(
            empresa=self.empresa, cuenta_financiera=banco, fecha=date(2026, 5, 5),
            descripcion='Depósito convertido', credito=Decimal('2990'),
        )
        self.client.force_login(self.usuario)
        url = reverse('enlazar_movimiento_factura', args=[self.empresa.slug, movimiento.pk])
        respuesta = self.client.post(url, {'factura': factura.pk, 'tipo_cambio_cobro': '26'})
        self.assertEqual(respuesta.status_code, 302)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.pago_factura.monto, Decimal('115'))
        self.assertEqual(movimiento.pago_factura.tipo_cambio, Decimal('26'))
        self.assertEqual(movimiento.asiento.lineas.get(cuenta=banco.cuenta_contable).debe, Decimal('2990'))
        antes = AsientoContable.objects.count()
        self.client.post(url, {'factura': factura.pk, 'tipo_cambio_cobro': '26'})
        self.assertEqual(AsientoContable.objects.count(), antes)
        self.assertEqual(PagoFactura.objects.filter(factura=factura).count(), 1)

    def test_deposito_con_redondeo_incompatible_no_cancela_factura(self):
        factura, _origen = self._factura()
        banco = self.cuenta_bancaria()
        movimiento = MovimientoBancario.objects.create(
            empresa=self.empresa, cuenta_financiera=banco, fecha=date(2026, 5, 5),
            descripcion='Depósito por comprobar', credito=Decimal('100'),
        )
        self.client.force_login(self.usuario)
        url = reverse('enlazar_movimiento_factura', args=[self.empresa.slug, movimiento.pk])
        respuesta = self.client.post(url, {'factura': factura.pk, 'tipo_cambio_cobro': '26'})
        self.assertContains(respuesta, 'La conversión no coincide con el depósito')
        self.assertFalse(PagoFactura.objects.filter(factura=factura).exists())

    def test_conversion_pendiente_afecta_solamente_cierre_empresa_del_asiento(self):
        _factura, asiento = self._factura()
        asiento.conversion_verificada = False
        asiento.save(update_fields=['conversion_verificada'])
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4)
        self.assertEqual(revisar_cierre_periodo(self.empresa, periodo)['control']['conversiones_pendientes'], 1)
        otra = Empresa.objects.create(nombre='Empresa sin pendientes', slug='sin-pendientes-circuito', rtn='08011999117772')
        otro_periodo = PeriodoContable.objects.create(empresa=otra, anio=2026, mes=4)
        self.assertEqual(revisar_cierre_periodo(otra, otro_periodo)['control']['total'], 0)

    def test_migracion_documenta_usd_sin_cambiar_importes_en_periodo_cerrado(self):
        _factura, asiento = self._factura()
        antes = list(asiento.lineas.values_list('pk', 'debe', 'haber'))
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado='cerrado')
        apps_historicas = MigrationExecutor(connection).loader.project_state().apps
        migracion = import_module('contabilidad.migrations.0019_revisar_conversiones_historicas')
        migracion.documentar_moneda_historica(apps_historicas, SimpleNamespace(connection=connection))
        asiento.refresh_from_db()
        self.assertFalse(asiento.conversion_verificada)
        self.assertEqual(asiento.moneda_origen, 'USD')
        self.assertEqual(asiento.tipo_cambio, Decimal('25'))
        self.assertEqual(list(asiento.lineas.values_list('pk', 'debe', 'haber')), antes)

    def test_conversion_legada_anulada_en_mismo_mes_no_bloquea_cierre_sin_saldo(self):
        factura, asiento = self._factura()
        asiento.conversion_verificada = False
        asiento.save(update_fields=['conversion_verificada'])
        registrar_reversion_documento(
            empresa=self.empresa, documento_tipo='factura', documento_id=factura.pk,
            evento_origen='emision', evento_reversion='anulacion', fecha=date(2026, 4, 20),
            descripcion='Anulación documentada', referencia=factura.numero_factura,
            origen_modulo='facturacion', creado_por=self.usuario,
        )
        factura.estado = 'anulada'
        factura.save(update_fields=['estado'])
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4)
        self.assertEqual(revisar_cierre_periodo(self.empresa, periodo)['control']['conversiones_pendientes'], 0)
