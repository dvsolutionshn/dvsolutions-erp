from datetime import date
from decimal import Decimal
from importlib import import_module
import os
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from . import test_clientes_contables as fixtures
from .acumulado_compras import construir_acumulado
from .captura_rapida import serializar
from .models import CuentaAcumuladoCompra, RegistroCompraFiscal


def seed():
    import_module('facturacion.migrations.0078_cuentas_referencia_nordic').preparar_nordic(
        apps, SimpleNamespace(connection=connection))


def compra(cliente, proveedor, numero, mes=8, base='1000', cuenta=None):
    neto = Decimal(base)
    return RegistroCompraFiscal.objects.create(empresa=cliente.empresa, cliente_contable=cliente,
        proveedor=proveedor, proveedor_nombre=proveedor.nombre, proveedor_rtn=proveedor.rtn,
        numero_factura=numero, fecha_documento=date(2025, 12, 25), periodo_anio=2026, periodo_mes=mes,
        base_15=neto, subtotal=neto, isv_15=neto*Decimal('.15'), total=neto*Decimal('1.15'), cuenta_acumulado=cuenta)


class AcumuladoComprasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.ClientesContablesTests.setUpTestData.__func__(cls)
        seed()

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('acumulado_cliente_contable', args=[self.empresa.slug, self.nordic.pk]) + '?anio=2026'
        self.cuenta = CuentaAcumuladoCompra.objects.get(cliente_contable=self.nordic, nombre='Compras en PriceSmart')

    def asignar(self, filas, cuenta=None, **extra):
        response = self.client.post(self.url, {'registros':[str(c.pk) for c in filas],
            'cuenta_destino':cuenta if cuenta is not None else self.cuenta.pk,
            **{f'version_{c.pk}':serializar(c)['version'] for c in filas}, **extra})

        if response.status_code == 200 and response.context and response.context.get('token'):
            return self.client.post(self.url, {'accion':'confirmar_asignacion','token':response.context['token'],
                'seleccion':json.dumps([c.pk for c in filas])})
        return response

    def test_referencia_solo_nordic_idempotente_sin_modificar_compras(self):
        c = compra(self.nordic, self.proveedor, '123')
        antes = serializar(c)
        seed()
        self.assertEqual(CuentaAcumuladoCompra.objects.filter(cliente_contable=self.nordic).count(), 66)
        self.assertFalse(CuentaAcumuladoCompra.objects.exclude(cliente_contable=self.nordic).exists())
        c.refresh_from_db()
        self.assertEqual(serializar(c), antes)

    def test_suma_por_periodo_sin_impuestos_y_sin_duplicar(self):
        c = compra(self.nordic, self.proveedor, '100', cuenta=self.cuenta)
        compra(self.nordic, self.proveedor, '101', mes=1, base='200')
        anulada = compra(self.nordic, self.proveedor, '102', base='900')
        anulada.estado='anulada'; anulada.save()
        compra(self.molac, self.proveedor_molac, '100', base='500')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rows = response.context['filas']
        fila = next(r for r in rows if r.get('cuenta') == self.cuenta)
        self.assertEqual(fila['importes'][7], Decimal('1000'))
        self.assertEqual(fila['importes'][11], Decimal('0'))
        self.assertEqual(response.context['pendientes'], 1)
        self.assertEqual(response.context['documentos'], 2)
        self.assertEqual(next(r for r in rows if r['nombre']=='Total compras sin ISV')['total'], Decimal('1200'))
        self.assertEqual(next(r for r in rows if r['nombre']=='Total de los libros')['total'], Decimal('1380'))
        self.assertContains(response, '1,380.00')
        self.assertFalse(response.context['diferencia'])
        c.isv_15 += Decimal('1'); c.save()
        self.assertTrue(self.client.get(self.url).context['diferencia'])

    def test_clasificar_reclasificar_y_anular_actualiza(self):
        c = compra(self.nordic, self.proveedor, '100')
        self.assertEqual(self.asignar([c]).status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.cuenta_acumulado, self.cuenta)
        self.assertEqual(c.clasificado_por, self.usuario)
        self.assertIsNotNone(c.clasificado_en)
        self.assertEqual(c.total, Decimal('1150'))
        self.assertEqual(RegistroCompraFiscal.objects.count(), 1)
        self.assertEqual(self.client.get(self.url).context['pendientes'], 0)
        self.assertEqual(self.asignar([c], cuenta='pendiente').status_code, 302)
        c.refresh_from_db(); self.assertIsNone(c.cuenta_acumulado)
        self.assertEqual(self.asignar([c]).status_code, 302)
        c.refresh_from_db()
        url = reverse('captura_cliente_contable', args=[self.empresa.slug, self.nordic.pk, 2026, 8])
        response = self.client.post(url, {'accion':'editar','registro_id':c.pk,'version':serializar(c)['version'],
            'proveedor':self.proveedor.pk,'fecha_documento':'25/12/25','numero_factura':c.numero_factura,'base_15':'2000'})
        self.assertEqual(response.status_code, 200, response.content)
        c.refresh_from_db(); self.assertEqual(c.cuenta_acumulado, self.cuenta)
        self.assertEqual(next(r for r in self.client.get(self.url).context['filas'] if r.get('cuenta')==self.cuenta)['total'], Decimal('2000'))
        response = self.client.post(url, {'accion':'anular','registro_id':c.pk,'version':serializar(c)['version']})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.client.get(self.url).context['documentos'], 0)

    def test_lote_ajeno_o_version_cambiada_no_guarda_nada(self):
        c = compra(self.nordic, self.proveedor, '100')
        ajena = compra(self.molac, self.proveedor_molac, '101')
        self.assertContains(self.asignar([c, ajena]), 'ajenas')
        c.refresh_from_db(); self.assertIsNone(c.cuenta_acumulado)
        segunda = compra(self.nordic, self.proveedor, '102')
        self.assertContains(self.asignar([c, segunda], **{f'version_{segunda.pk}':'vieja'}), 'cambió')
        c.refresh_from_db(); self.assertIsNone(c.cuenta_acumulado)
        self.assertFalse(RegistroCompraFiscal.objects.filter(clasificado_en__isnull=False).exists())

    def test_cuentas_ajenas_inactivas_y_modelo(self):
        c = compra(self.nordic, self.proveedor, '100')
        otra = CuentaAcumuladoCompra.objects.create(cliente_contable=self.molac, nombre='Alquiler', grupo='gasto')
        self.assertEqual(self.asignar([c], cuenta=otra.pk).status_code, 404)
        c.cuenta_acumulado=otra
        with self.assertRaises(ValidationError): c.full_clean()
        c.cuenta_acumulado=None
        self.cuenta.activa=False; self.cuenta.save()
        self.assertEqual(self.asignar([c]).status_code, 404)

    def test_permisos_y_cliente_inactivo(self):
        c = compra(self.nordic, self.proveedor, '100')
        self.rol.puede_editar_compras=False; self.rol.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.asignar([c]).status_code, 403)
        self.client.force_login(self.ajeno)
        self.assertIn(self.client.get(self.url).status_code, (302,403))
        self.client.force_login(self.usuario)
        self.nordic.usuarios.clear()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.admin)
        self.nordic.activo=False; self.nordic.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.asignar([c]).status_code, 403)

    def test_cuentas_edicion_y_filtros(self):
        url = reverse('cuentas_acumulado_cliente', args=[self.empresa.slug, self.nordic.pk])
        response=self.client.post(url, {'nombre':'Cuenta nueva','grupo':'gasto','orden':200,'activa':'on'})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.post(url, {'nombre':'CUENTA NUEVA','grupo':'gasto','orden':201}), 'Ya existe')
        compra(self.nordic, self.proveedor, '100', cuenta=self.cuenta)
        response=self.client.get(self.url+'&mes=8&cuenta='+str(self.cuenta.pk)+'&proveedor=Larach')
        self.assertEqual(response.context['pagina'].paginator.count, 1)
        self.assertEqual(self.client.get(self.url+'&mes=1&cuenta=todas').context['pagina'].paginator.count, 0)
        self.assertEqual(self.client.get(self.url+'&mes=13').status_code, 404)
        self.assertEqual(self.client.get(self.url+'&cuenta=999999').status_code, 404)


@skipUnless(os.environ.get('PLAYWRIGHT_MODULE'), 'Navegador opcional.')
class AcumuladoBrowserTests(StaticLiveServerTestCase):
    def test_clasificar_en_navegador(self):
        fixtures.ClientesContablesTests.setUpTestData.__func__(self.__class__)
        seed()
        c = compra(self.nordic, self.proveedor, 'PRUEBA-100')
        compra(self.nordic, self.proveedor, 'PRUEBA-200', mes=1, base='200')
        cuenta = CuentaAcumuladoCompra.objects.get(cliente_contable=self.nordic, nombre='Compras en PriceSmart')
        self.client.force_login(self.usuario)
        env={**os.environ, 'ACC_URL':self.live_server_url+reverse('acumulado_cliente_contable',args=[self.empresa.slug,self.nordic.pk])+'?anio=2026',
            'ACC_SESSION':self.client.cookies['sessionid'].value, 'ACC_ACCOUNT':str(cuenta.pk), 'ACC_RECORD':str(c.pk)}
        result=subprocess.run(['node', str(Path(__file__).parent/'browser_tests/acumulado_compras.cjs')],env=env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        c.refresh_from_db(); self.assertEqual(c.cuenta_acumulado,cuenta)
        self.assertEqual(RegistroCompraFiscal.objects.count(),2)
