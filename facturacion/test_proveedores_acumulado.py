from datetime import datetime
import json
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from . import test_clientes_contables as fixtures
from .test_acumulado_compras import compra, seed
from .test_importacion_clientes import excel
from .captura_rapida import serializar, buscar_duplicada
from .models import Proveedor, RegistroCompraFiscal, CuentaAcumuladoCompra
from .proveedores_compras import buscar_proveedor, resolver_proveedor, nombre_normalizado


class ProveedoresAcumuladoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.ClientesContablesTests.setUpTestData.__func__(cls)
        seed()

    def setUp(self):
        self.client.force_login(self.usuario)
        self.cuenta=CuentaAcumuladoCompra.objects.filter(cliente_contable=self.nordic).first()
        self.url=reverse('acumulado_cliente_contable',args=[self.empresa.slug,self.nordic.pk])+'?anio=2026'

    def test_normalizacion_y_rtn_prioritario_aislado(self):
        self.assertEqual(nombre_normalizado(' Príce Smart, S.A. '),nombre_normalizado('PRICE SMART SA'))
        with transaction.atomic():
            p,creado=resolver_proveedor(self.empresa,self.nordic,' lÁrach-nordic ','',self.usuario)
            self.assertEqual(p,self.proveedor);self.assertFalse(creado)
            p,creado=resolver_proveedor(self.empresa,self.nordic,'Otro nombre',self.proveedor.rtn,self.usuario)
            self.assertEqual(p,self.proveedor);self.assertFalse(creado)
            p,creado=resolver_proveedor(self.empresa,self.gecko,'Larach Nordic',self.proveedor.rtn,self.usuario)
            self.assertTrue(creado);self.assertEqual(p.cliente_contable,self.gecko)
        Proveedor.objects.create(empresa=self.empresa,cliente_contable=self.nordic,nombre='Larach duplicado',rtn=self.proveedor.rtn)
        with self.assertRaises(ValidationError):
            buscar_proveedor(list(Proveedor.objects.filter(cliente_contable=self.nordic)),'Larach',self.proveedor.rtn)

    def test_importacion_reutiliza_variaciones_y_no_asigna_cuenta(self):
        self.proveedor.cuenta_habitual=self.cuenta;self.proveedor.save()
        url=reverse('importar_compras_cliente',args=[self.empresa.slug,self.nordic.pk,2026,8])
        archivo=SimpleUploadedFile('Prueba.xlsx',excel([(datetime(2026,8,1),' LÁRACH-NORDIC ','123',100,15,0,115)]))
        response=self.client.post(url,{'archivo':archivo})
        self.assertContains(response,'Cuenta sugerida')
        self.assertFalse(RegistroCompraFiscal.objects.exists())
        saved=self.client.post(url,{'token':response.context['token'],'accion':'confirmar','revisado':'on','filas':['11']})
        self.assertEqual(saved.status_code,302,saved.content)
        c=RegistroCompraFiscal.objects.get();self.assertEqual(c.proveedor,self.proveedor);self.assertIsNone(c.cuenta_acumulado)
        self.assertEqual(Proveedor.objects.filter(cliente_contable=self.nordic).count(),1)
        c.proveedor=None;c.proveedor_rtn='';c.proveedor_nombre=' LÁRACH-NORDIC ';c.save()
        self.assertEqual(buscar_duplicada(self.empresa,self.proveedor,'123',cliente_contable=self.nordic)['id'],c.pk)

    def test_vincular_historial_conserva_originales_y_reenvio(self):
        c=compra(self.nordic,self.proveedor,'100');c.proveedor=None;c.proveedor_nombre=' LÁRACH  NORDIC ';c.save()
        original={k:v for k,v in c.__dict__.items() if not k.startswith('_')}
        url=reverse('vincular_proveedores_libro',args=[self.empresa.slug,self.nordic.pk,2026,8])
        preview=self.client.get(url);self.assertEqual(preview.status_code,200)
        c.refresh_from_db();self.assertIsNone(c.proveedor_id)
        response=self.client.post(url,{'token':preview.context['token'],'grupos':['0']})
        self.assertEqual(response.status_code,302,response.content)
        c.refresh_from_db();self.assertEqual(c.proveedor,self.proveedor)
        for k,v in original.items():
            if k not in ('proveedor_id','proveedor_vinculado_por_id','proveedor_vinculado_en'):
                self.assertEqual(getattr(c,k),v,k)
        self.assertEqual(c.proveedor_vinculado_por,self.usuario)
        self.assertContains(self.client.post(url,{'token':preview.context['token'],'grupos':['0']}),'cambió')
        self.assertEqual(Proveedor.objects.filter(cliente_contable=self.nordic).count(),1)

    def test_vincular_crea_unico_y_bloquea_otros_clientes(self):
        for n,name in enumerate(['Café Nuevo',' CAFE-NUEVO ']):
            c=compra(self.nordic,self.proveedor,str(100+n));c.proveedor=None;c.proveedor_nombre=name;c.proveedor_rtn='';c.save()
        url=reverse('vincular_proveedores_libro',args=[self.empresa.slug,self.nordic.pk,2026,8])
        preview=self.client.get(url);self.assertEqual(len(preview.context['grupos']),1)
        self.assertEqual(self.client.post(url,{'token':preview.context['token'],'grupos':['0']}).status_code,302)
        self.assertEqual(RegistroCompraFiscal.objects.values('proveedor').distinct().count(),1)
        self.assertEqual(Proveedor.objects.filter(cliente_contable=self.nordic).count(),2)
        self.client.force_login(self.admin)
        url=reverse('vincular_proveedores_libro',args=[self.empresa.slug,self.molac.pk,2026,8])
        self.assertEqual(self.client.get(url).status_code,404)

    def test_cuenta_habitual_ficha_y_sugerencia_captura(self):
        url=reverse('editar_proveedor_cliente_contable',args=[self.empresa.slug,self.nordic.pk,self.proveedor.pk])
        response=self.client.post(url,{'nombre':self.proveedor.nombre,'rtn':self.proveedor.rtn,'activo':'on','cuenta_habitual':self.cuenta.pk})
        self.assertEqual(response.status_code,302,response.content)
        self.proveedor.refresh_from_db();self.assertEqual(self.proveedor.cuenta_habitual_por,self.usuario)
        url=reverse('captura_cliente_contable',args=[self.empresa.slug,self.nordic.pk,2026,8])
        response=self.client.get(url,{'accion':'proveedores','q':'LÁRACH-NORDIC'})
        self.assertEqual(response.json()['proveedores'][0]['sugerencia'],self.cuenta.nombre)
        ajena=CuentaAcumuladoCompra.objects.create(cliente_contable=self.molac,nombre='Ajena',grupo='gasto')
        self.proveedor.cuenta_habitual=ajena
        with self.assertRaises(ValidationError):self.proveedor.full_clean()

    def test_revision_masiva_sugerida_excepciones_e_importes(self):
        self.proveedor.cuenta_habitual=self.cuenta;self.proveedor.save()
        a=compra(self.nordic,self.proveedor,'100');b=compra(self.nordic,self.proveedor,'101',base='200')
        compra(self.molac,self.proveedor_molac,'100',base='8000')
        url=self.url+'&proveedor_id='+str(self.proveedor.pk)
        preview=self.client.post(url,{'alcance':'filtradas','modo':'sugeridas'})
        self.assertEqual(preview.status_code,200,preview.content);self.assertContains(preview,'1,380.00')
        self.assertIsNone(RegistroCompraFiscal.objects.get(pk=a.pk).cuenta_acumulado)
        response=self.client.post(url,{'accion':'confirmar_asignacion','token':preview.context['token'],'seleccion':json.dumps([a.pk])})
        self.assertEqual(response.status_code,302,response.content)
        a.refresh_from_db();b.refresh_from_db()
        self.assertEqual(a.cuenta_acumulado,self.cuenta);self.assertIsNone(b.cuenta_acumulado)
        self.assertEqual(a.total,Decimal('1150'));self.assertEqual(a.clasificado_por,self.usuario)
        groups=self.client.get(self.url+'&cuenta=todas').context['grupos_proveedores']
        self.assertEqual(groups[0]['cantidad'],2);self.assertEqual(groups[0]['subtotal'],Decimal('1200'))

    def test_cambio_sugerencia_permiso_y_token_bloquean_lote(self):
        self.proveedor.cuenta_habitual=self.cuenta;self.proveedor.save()
        a=compra(self.nordic,self.proveedor,'100')
        preview=self.client.post(self.url,{'alcance':'filtradas','modo':'sugeridas'})
        self.proveedor.cuenta_habitual=None;self.proveedor.save()
        post={'accion':'confirmar_asignacion','token':preview.context['token'],'seleccion':json.dumps([a.pk])}
        self.assertContains(self.client.post(self.url,post),'sugerencia')
        self.assertContains(self.client.post(self.url,{**post,'token':'alterado'}),'no es válida')
        self.rol.puede_editar_compras=False;self.rol.save()
        self.assertEqual(self.client.post(self.url,post).status_code,403)
        a.refresh_from_db();self.assertIsNone(a.cuenta_acumulado)


import os
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('PLAYWRIGHT_MODULE'), 'Navegador opcional.')
class ProveedoresFlujoBrowserTests(StaticLiveServerTestCase):
    def test_vincular_sugerir_revisar_excepciones(self):
        fixtures.ClientesContablesTests.setUpTestData.__func__(self.__class__)
        seed()
        a=compra(self.nordic,self.proveedor,'100')
        b=compra(self.nordic,self.proveedor,'101',base='200')
        for c,nombre in [(a,'Café Prueba'),(b,' CAFE-PRUEBA ')]:
            c.proveedor=None;c.proveedor_nombre=nombre;c.proveedor_rtn='';c.save()
        cuenta=CuentaAcumuladoCompra.objects.filter(cliente_contable=self.nordic).first()
        self.client.force_login(self.usuario)
        env={**os.environ,'P_SESSION':self.client.cookies['sessionid'].value,
            'P_BOOK':self.live_server_url+reverse('captura_cliente_contable',args=[self.empresa.slug,self.nordic.pk,2026,8]),
            'P_CATALOG':self.live_server_url+reverse('proveedores_cliente_contable',args=[self.empresa.slug,self.nordic.pk]),
            'P_ACC':self.live_server_url+reverse('acumulado_cliente_contable',args=[self.empresa.slug,self.nordic.pk])+'?anio=2026',
            'P_ACCOUNT':str(cuenta.pk),'P_EXCLUDE':str(b.pk)}
        result=subprocess.run(['node',str(Path(__file__).parent/'browser_tests/proveedores_acumulado.cjs')],env=env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        a.refresh_from_db();b.refresh_from_db()
        self.assertEqual(a.proveedor_id,b.proveedor_id)
        self.assertEqual(a.cuenta_acumulado,cuenta);self.assertIsNone(b.cuenta_acumulado)
        self.assertEqual(a.proveedor_nombre,'Café Prueba');self.assertEqual(b.proveedor_nombre,' CAFE-PRUEBA ')
        self.assertEqual(a.total,Decimal('1150'));self.assertEqual(b.total,Decimal('230'))
