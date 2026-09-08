from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from contabilidad.views import _reporte_impuestos_data
from .captura_rapida import CapturaForm
from .models import CompraInventario, Proveedor, RegistroCompraFiscal


class CapturaRapidaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre='Piloto', slug='demo_1', rtn='00000000000001')
        cls.otra = Empresa.objects.create(nombre='Otra', slug='otra', rtn='00000000000002')
        modulo, _ = Modulo.objects.get_or_create(codigo='facturacion', defaults={'nombre': 'Facturación'})
        for empresa in (cls.empresa, cls.otra):
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
        cls.user = get_user_model().objects.create_user(username='capturista', is_superuser=True)
        cls.proveedor = Proveedor.objects.create(empresa=cls.empresa, nombre='Larach', rtn='08011999123456')
        cls.proveedor2 = Proveedor.objects.create(empresa=cls.empresa, nombre='Otro proveedor', rtn='08011999123457')
        cls.ajeno = Proveedor.objects.create(empresa=cls.otra, nombre='Larach ajeno')

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('captura_rapida_compras', args=['demo_1'])
        self.data = dict(fecha_documento='070926', proveedor=self.proveedor.pk,
                         numero_factura='000001010000000085', exento='', base_15='100.10', base_18='200.25')

    def registro_antiguo(self, **kwargs):
        datos = dict(empresa=self.empresa, proveedor=self.proveedor, proveedor_nombre=self.proveedor.nombre,
                     proveedor_rtn=self.proveedor.rtn, numero_factura='000-001-01-0000000085',
                     fecha_documento=date(2024, 1, 2), total=Decimal('123'))
        datos.update(kwargs)
        return RegistroCompraFiscal.objects.create(**datos)

    def duplicado(self, **kwargs):
        datos = dict(accion='duplicado', proveedor=self.proveedor.pk, numero_factura=self.data['numero_factura'])
        datos.update(kwargs)
        return self.client.get(self.url, datos).json()['duplicada']

    def test_guardar_periodo_trazabilidad_libro_impuestos_y_reenvio(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 201, response.content)
        registro = RegistroCompraFiscal.objects.get()
        self.assertEqual(registro.numero_factura, '000-001-01-0000000085')
        self.assertEqual(registro.numero_factura_normalizado, '000001010000000085')
        self.assertEqual(registro.creado_por, self.user)
        self.assertIsNotNone(registro.fecha_creacion)
        self.assertEqual(registro.fecha_documento, date(2026, 9, 7))
        self.assertEqual((registro.periodo_anio, registro.periodo_mes), (2026, 9))
        self.assertEqual(registro.isv_15, Decimal('15.02'))
        self.assertEqual(registro.isv_18, Decimal('36.04'))
        self.assertEqual(registro.total, Decimal('351.41'))
        libro = self.client.get(reverse('libro_compras_fiscal', args=['demo_1']))
        self.assertEqual(libro.context['resumen']['total'], registro.total)
        impuestos = _reporte_impuestos_data(self.empresa, '2026-09-01', '2026-09-30')
        self.assertEqual(impuestos['compras_resumen']['total'], registro.total)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)
        self.assertEqual(RegistroCompraFiscal.objects.count(), 1)
        self.assertEqual(CompraInventario.objects.count(), 0)

    def test_fechas_explicitas_y_meses_anteriores(self):
        for valor, esperada in [('070926', date(2026,9,7)), ('250826', date(2026,8,25)),
                                ('180726', date(2026,7,18)), ('2024-02-29', date(2024,2,29)),
                                ('25/08/2026', date(2026,8,25)), ('18-07-2026', date(2026,7,18))]:
            with self.subTest(valor=valor):
                form = CapturaForm({**self.data, 'fecha_documento':valor}, empresa=self.empresa)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['fecha_documento'], esperada)
        for valor in ['310226', '290225', '000926', '071326', '07/09', '']:
            self.assertEqual(self.client.post(self.url, {**self.data, 'fecha_documento':valor}).status_code, 400)

    def test_correlativo_variable_sin_rellenar(self):
        for numero, esperado in [('0040120158956348','004-012-01-58956348'), ('000001011','000-001-01-1')]:
            response = self.client.post(self.url, {**self.data,'numero_factura':numero})
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()['registro']['numero'], esperado)

    def test_duplicado_historico_guiones_rtn_y_anulacion(self):
        antiguo = self.registro_antiguo(estado='anulada')
        self.assertEqual(self.duplicado()['fecha'], '02/01/2024')
        self.assertEqual(self.duplicado()['estado'], 'Anulada')
        alias = Proveedor.objects.create(empresa=self.empresa, nombre='Larach alias', rtn=self.proveedor.rtn)
        self.assertIsNotNone(self.duplicado(proveedor=alias.pk))
        self.assertIsNone(self.duplicado(proveedor=self.proveedor2.pk))
        antiguo.refresh_from_db()
        self.assertIsNone(antiguo.numero_factura_normalizado)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)

    def test_historico_sin_vinculo_por_rtn_o_nombre(self):
        registro = self.registro_antiguo(proveedor=None, proveedor_rtn='0801-1999-123456')
        self.assertIsNotNone(self.duplicado())
        RegistroCompraFiscal.objects.filter(pk=registro.pk).update(proveedor_rtn=None)
        self.assertIsNotNone(self.duplicado())
        self.assertIsNone(self.duplicado(proveedor=self.proveedor2.pk))

    def test_detecta_compra_inventario(self):
        CompraInventario.objects.create(empresa=self.empresa, proveedor=self.proveedor,
            proveedor_nombre=self.proveedor.nombre, referencia_documento='000-001-01-0000000085',
            fecha_documento=date(2023,1,2))
        self.assertEqual(self.duplicado()['estado'], 'Borrador')
        self.assertEqual(self.client.post(self.url, self.data).status_code, 409)

    def test_mismo_numero_distinto_proveedor_y_empresa(self):
        self.registro_antiguo(empresa=self.otra, proveedor=self.ajeno)
        self.assertIsNone(self.duplicado())
        self.assertEqual(self.client.post(self.url, self.data).status_code, 201)
        self.assertEqual(self.client.post(self.url, {**self.data,'proveedor':self.proveedor2.pk}).status_code, 201)

    def test_proveedores_frecuentes_y_aislamiento(self):
        self.registro_antiguo()
        response = self.client.get(self.url, {'accion':'proveedores','q':'lar'}).json()
        self.assertEqual([p['id'] for p in response['proveedores']], [self.proveedor.pk])
        response = self.client.get(self.url, {'accion':'proveedores'}).json()
        self.assertEqual(response['proveedores'][0]['id'], self.proveedor.pk)
        self.assertEqual(self.client.post(self.url, {**self.data,'proveedor':self.ajeno.pk}).status_code, 400)

    def test_validacion_decimal_no_finite_negativos_limites_cero(self):
        for importe in ['NaN', 'Infinity', '-1', 'abc', '0.001', '99999999999999']:
            self.assertEqual(self.client.post(self.url, {**self.data,'base_15':importe}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {**self.data,'base_15':'','base_18':''}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {**self.data,'base_15':'999999999999.99','base_18':''}).status_code, 400)

    def test_restriccion_base_datos_rechaza_inserciones_simultaneas(self):
        self.client.post(self.url, self.data)
        registro = RegistroCompraFiscal.objects.get()
        registro.pk = None
        # bulk_create omite full_clean; comprueba la garantía real de la BD.
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroCompraFiscal.objects.bulk_create([registro])

    def test_acceso_empresa_permiso_autenticacion_csrf(self):
        otra_url = reverse('captura_rapida_compras', args=['otra'])
        for accion in ['', 'proveedores', 'duplicado']:
            self.assertEqual(self.client.get(otra_url, {'accion':accion}).status_code, 404)
        self.assertEqual(self.client.post(otra_url, self.data).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertIn(client.post(self.url, self.data).status_code, (302, 403))
        self.assertFalse(RegistroCompraFiscal.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        rol = RolSistema.objects.create(nombre='Solo lectura', codigo='lector', puede_compras=True)
        usuario = get_user_model().objects.create_user(username='lector', empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(usuario)
        self.assertIn(self.client.post(self.url, self.data).status_code, (302,403))
        usuario.empresa = self.otra
        usuario.save()
        self.assertIn(self.client.get(self.url, {'accion':'proveedores'}).status_code, (302,403))
        self.assertFalse(RegistroCompraFiscal.objects.exists())

    def test_enlaces_solo_piloto_y_pantalla_tradicional(self):
        for nombre in ['compras_dashboard','libro_compras_fiscal']:
            self.assertContains(self.client.get(reverse(nombre,args=['demo_1'])), self.url)
            self.assertNotContains(self.client.get(reverse(nombre,args=['otra'])), 'Captura Rápida')
        self.assertContains(self.client.get(self.url), 'capture-form')
        self.assertEqual(self.client.get(reverse('crear_compra',args=['demo_1'])).status_code, 200)

    def test_permiso_operativo_sin_ser_administrador(self):
        self.empresa.estado_licencia = 'activa'
        self.empresa.save()
        rol = RolSistema.objects.create(nombre='Capturista', codigo='capturista', puede_compras=True,
                                        puede_crear_compras=True)
        usuario = get_user_model().objects.create_user(username='operador', empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 201)
        self.assertEqual(RegistroCompraFiscal.objects.get().creado_por, usuario)
        rol.puede_crear_compras = False
        rol.save()
        self.assertIn(self.client.get(self.url, {'accion':'proveedores'}).status_code, (302,403))

    def test_crear_proveedor_nombre_y_rtn_sin_duplicar(self):
        datos = {'accion':'crear_proveedor', 'nombre':'Papelería nueva', 'rtn':'0801-2020-123456'}
        response = self.client.post(self.url, datos)
        self.assertEqual(response.status_code, 201, response.content)
        proveedor = Proveedor.objects.get(pk=response.json()['proveedor']['id'])
        self.assertEqual(proveedor.empresa, self.empresa)
        self.assertEqual(proveedor.rtn, '08012020123456')
        repetido = self.client.post(self.url, {**datos,'nombre':'Otro nombre'})
        self.assertEqual(repetido.status_code, 200)
        self.assertEqual(repetido.json()['proveedor']['id'], proveedor.pk)
        proveedor.refresh_from_db()
        self.assertEqual(proveedor.nombre, 'Papelería nueva')
        self.assertEqual(Proveedor.objects.filter(empresa=self.empresa, rtn=proveedor.rtn).count(), 1)
        self.assertFalse(RegistroCompraFiscal.objects.exists())

    def test_rtn_obligatorio_proveedor_inactivo_y_aislamiento(self):
        datos = {'accion':'crear_proveedor','nombre':'Nuevo','rtn':'08012020123456'}
        for cambios in [{'rtn':''},{'rtn':'abc'},{'nombre':''}]:
            self.assertEqual(self.client.post(self.url,{**datos,**cambios}).status_code,400)
        Proveedor.objects.create(empresa=self.otra,nombre='Ajeno mismo RTN',rtn=datos['rtn'])
        self.assertEqual(self.client.post(self.url,datos).status_code,201)
        Proveedor.objects.filter(empresa=self.empresa,rtn=datos['rtn']).update(activo=False)
        self.assertEqual(self.client.post(self.url,datos).status_code,409)
        self.assertEqual(self.client.post(reverse('captura_rapida_compras',args=['otra']),datos).status_code,404)

    def test_crear_proveedor_requiere_permiso_proveedores(self):
        self.empresa.estado_licencia = 'activa'
        self.empresa.save()
        rol = RolSistema.objects.create(nombre='Compras',codigo='compras',puede_compras=True,puede_crear_compras=True)
        usuario = get_user_model().objects.create_user(username='comprador',empresa=self.empresa,rol_sistema=rol)
        self.client.force_login(usuario)
        datos = {'accion':'crear_proveedor','nombre':'Nuevo','rtn':'08012020123456'}
        self.assertEqual(self.client.post(self.url,datos).status_code,403)
        self.assertNotContains(self.client.get(self.url),'id="supplier-dialog"')
        rol.puede_crear_proveedores = True
        rol.save()
        self.assertEqual(self.client.post(self.url,datos).status_code,201)
        self.assertContains(self.client.get(self.url),'id="supplier-dialog"')


@skipUnless(os.environ.get('PLAYWRIGHT_MODULE'), 'Configura PLAYWRIGHT_MODULE para probar navegador real.')
class CapturaRapidaBrowserTests(StaticLiveServerTestCase):
    def test_flujo_teclado_navegador_real(self):
        CapturaRapidaTests.setUpTestData.__func__(self.__class__)
        self.client.force_login(self.user)
        RegistroCompraFiscal.objects.create(empresa=self.empresa, proveedor=self.proveedor,
            proveedor_nombre='Larach', proveedor_rtn=self.proveedor.rtn,
            numero_factura='000-001-01-0000000085', fecha_documento=date(2024,1,2), total=123)
        env = {**os.environ, 'CAPTURA_TEST_URL':self.live_server_url,
               'CAPTURA_TEST_PROVEEDOR':str(self.proveedor.pk),
               'CAPTURA_TEST_SESSION':self.client.cookies['sessionid'].value}
        result = subprocess.run(['node', str(Path(__file__).parent / 'browser_tests/captura_rapida.cjs')],
                                env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(RegistroCompraFiscal.objects.count(), 5)
        nuevo = Proveedor.objects.get(empresa=self.empresa, rtn='08012020123456')
        self.assertEqual(nuevo.nombre, 'Papelería nueva')
        self.assertEqual(RegistroCompraFiscal.objects.filter(proveedor=nuevo).count(), 1)
        self.assertEqual(RegistroCompraFiscal.objects.filter(periodo_mes=8, periodo_anio=2026).count(), 1)
        self.assertEqual(RegistroCompraFiscal.objects.filter(periodo_mes=7, periodo_anio=2026).count(), 1)
