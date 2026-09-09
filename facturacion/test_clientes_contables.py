from datetime import date
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from contabilidad.views import _reporte_impuestos_data
from .models import ClienteContable, Proveedor, RegistroCompraFiscal, LibroCompraMensual


class ClientesContablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre='Dubón', slug='dubon_asociados', rtn='001', estado_licencia='activa')
        cls.otra = Empresa.objects.create(nombre='Demo', slug='demo_1', rtn='002', estado_licencia='activa')
        modulo, _ = Modulo.objects.get_or_create(codigo='facturacion', defaults={'nombre':'Facturación'})
        for empresa in (cls.empresa, cls.otra):
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
        cls.rol = RolSistema.objects.create(nombre='Captura clientes', codigo='captura-clientes',
            puede_compras=True, puede_crear_compras=True, puede_editar_compras=True,
            puede_anular_compras=True, puede_crear_proveedores=True, puede_editar_proveedores=True,
            puede_proveedores=True)
        cls.admin = get_user_model().objects.create_user(username='admin-clientes', empresa=cls.empresa, es_administrador_empresa=True)
        cls.usuario = get_user_model().objects.create_user(username='nordic', empresa=cls.empresa, rol_sistema=cls.rol)
        cls.ajeno = get_user_model().objects.create_user(username='ajeno', empresa=cls.otra, rol_sistema=cls.rol)
        cls.nordic = ClienteContable.objects.create(empresa=cls.empresa, nombre='Nordic')
        cls.molac = ClienteContable.objects.create(empresa=cls.empresa, nombre='Molac')
        cls.gecko = ClienteContable.objects.create(empresa=cls.empresa, nombre='Gecko')
        cls.nordic.usuarios.add(cls.usuario)
        cls.gecko.usuarios.add(cls.usuario)
        cls.proveedor = Proveedor.objects.create(empresa=cls.empresa, cliente_contable=cls.nordic, nombre='Larach Nordic', rtn='08011999123456')
        cls.proveedor_molac = Proveedor.objects.create(empresa=cls.empresa, cliente_contable=cls.molac, nombre='Larach Molac', rtn='08011999123456')
        cls.legacy = Proveedor.objects.create(empresa=cls.empresa, nombre='Propio Dubón', rtn='08011999123456')

    def ruta(self, name='captura_cliente_contable', cliente=None, *extra):
        return reverse(name, args=[self.empresa.slug, (cliente or self.nordic).pk, *extra])

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = self.ruta('captura_cliente_contable', None, 2026, 8)
        self.data = dict(proveedor=self.proveedor.pk, fecha_documento='25/7/26', numero_factura='0040120158956348', exento='10', base_15='100', base_18='200')

    def guardar(self, url=None, **datos):
        response = self.client.post(url or self.url, {**self.data, **datos})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['registro']

    def test_usuarios_asignados_y_urls_forzadas(self):
        lista = self.client.get(reverse('clientes_contables', args=[self.empresa.slug]))
        self.assertContains(lista, 'Nordic')
        self.assertContains(lista, 'Gecko')
        self.assertNotContains(lista, 'Molac')
        for name, extra in [('libros_cliente_contable', ()), ('acumulado_cliente_contable', ()),
                            ('proveedores_cliente_contable', ()), ('captura_cliente_contable', (2026,8))]:
            url = self.ruta(name, self.molac, *extra)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(url, {'accion':'proveedores'}).status_code, 404)
            self.assertEqual(self.client.post(url, self.data).status_code, 404)
        self.assertEqual(self.client.get(self.ruta('libros_cliente_contable', self.gecko)).status_code,200)
        self.client.force_login(self.ajeno)
        self.assertNotEqual(self.client.get(self.url).status_code,200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.ruta('libros_cliente_contable',self.molac)).status_code,200)

    def test_factura_unica_periodo_y_duplicados_por_cliente(self):
        registro = self.guardar()
        compra = RegistroCompraFiscal.objects.get(pk=registro['id'])
        self.assertEqual(compra.cliente_contable, self.nordic)
        self.assertEqual(compra.fecha_documento, date(2026,7,25))
        self.assertEqual((compra.periodo_anio,compra.periodo_mes),(2026,8))
        self.assertEqual(compra.total,Decimal('361'))
        self.assertEqual(compra.numero_factura,'004-012-01-58956348')
        self.assertEqual(LibroCompraMensual.objects.get().registros.get(),compra)
        julio = self.ruta('captura_cliente_contable',None,2025,1)
        self.assertEqual(self.client.post(julio,self.data).status_code,409)
        duplicado=self.client.get(julio,{'accion':'duplicado',**self.data}).json()['duplicada']
        self.assertEqual(duplicado['id'],compra.pk)
        self.client.force_login(self.admin)
        self.guardar(self.ruta('captura_cliente_contable',self.molac,2026,8),proveedor=self.proveedor_molac.pk)
        self.assertEqual(RegistroCompraFiscal.objects.count(),2)
        self.assertEqual(LibroCompraMensual.objects.count(),2)
        self.assertEqual(self.client.post(self.url,self.data).status_code,409)

    def test_busqueda_y_proveedor_forjado(self):
        proveedores = self.client.get(self.url,{'accion':'proveedores','q':'Larach'}).json()['proveedores']
        self.assertEqual([p['id'] for p in proveedores],[self.proveedor.pk])
        for proveedor in (self.proveedor_molac,self.legacy):
            self.assertEqual(self.client.post(self.url,{**self.data,'proveedor':proveedor.pk}).status_code,400)
            self.assertEqual(self.client.get(self.url,{'accion':'duplicado',**self.data,'proveedor':proveedor.pk}).status_code,400)
        response=self.client.post(self.url,{'accion':'crear_proveedor','nombre':'Nuevo','rtn':self.proveedor_molac.rtn})
        self.assertEqual(response.json()['proveedor']['id'],self.proveedor.pk)
        response=self.client.post(self.url,{'accion':'crear_proveedor','nombre':'Solo Nordic','rtn':'09999999999999'})
        self.assertEqual(response.status_code,201)
        self.assertEqual(Proveedor.objects.get(pk=response.json()['proveedor']['id']).cliente_contable,self.nordic)
        self.assertEqual(self.client.get(self.ruta('editar_proveedor_cliente_contable',None,self.proveedor_molac.pk)).status_code,404)

    def test_acumulado_cambia_al_editar_anular_y_no_mezcla(self):
        registro=self.guardar()
        annual=self.ruta('acumulado_cliente_contable')
        response=self.client.get(annual,{'anio':2026})
        self.assertContains(response,'TOTAL AÑO 2026')
        self.assertContains(response,'Cliente activo: Nordic')
        self.assertEqual(len(response.context['meses']),12)
        self.assertEqual(response.context['meses'][6]['resumen']['documentos'],0)
        self.assertEqual(response.context['meses'][7]['resumen']['documentos'],1)
        self.assertEqual(response.context['resumen']['total'],Decimal('361'))
        response=self.client.post(self.url,{**self.data,'accion':'editar','registro_id':registro['id'],'version':registro['version'],'exento':'20'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.get(annual,{'anio':2026}).context['resumen']['total'],Decimal('371'))
        registro=response.json()['registro']
        self.assertEqual(self.client.post(self.url,{'accion':'anular','registro_id':registro['id'],'version':registro['version']}).status_code,200)
        self.assertEqual(self.client.get(annual,{'anio':2026}).context['resumen']['total'],Decimal('0'))
        self.assertEqual(self.client.get(self.url,{'accion':'cuadro'}).json()['registros'][0]['estado_codigo'],'anulada')

    def test_edicion_anulacion_y_estado_aislados(self):
        registro=self.guardar()
        url=self.ruta('captura_cliente_contable',self.gecko,2026,8)
        for accion in ('editar','anular'):
            response=self.client.post(url,{**self.data,'accion':accion,'registro_id':registro['id'],'version':registro['version']})
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.post(self.url,{'accion':'estado','estado':'finalizado'}).status_code,200)
        self.assertEqual(self.client.get(url,{'accion':'cuadro'}).json()['estado_libro'],'en_proceso')
        self.assertEqual(self.client.post(self.url,{**self.data,'numero_factura':'00000101123456'}).status_code,409)

    def test_administracion_asignaciones_y_empresa_forjada(self):
        url=reverse('crear_cliente_contable',args=[self.empresa.slug])
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.admin)
        datos={'nombre':'Nuevo','razon_social':'Nuevo SA','rtn':'08019999888888','activo':'on','usuarios':[self.usuario.pk]}
        self.assertEqual(self.client.post(url,{**datos,'usuarios':[self.ajeno.pk]}).status_code,200)
        self.assertFalse(ClienteContable.objects.filter(nombre='Nuevo').exists())
        self.assertEqual(self.client.post(url,{**datos,'empresa':self.otra.pk}).status_code,302)
        nuevo=ClienteContable.objects.get(nombre='Nuevo')
        self.assertEqual(nuevo.empresa,self.empresa)
        self.assertEqual(list(nuevo.usuarios.all()),[self.usuario])
        self.assertEqual(self.client.post(url,datos).status_code,200)
        self.assertEqual(ClienteContable.objects.filter(nombre='Nuevo').count(),1)

    def test_cliente_inactivo_y_revocacion(self):
        self.nordic.usuarios.clear()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.nordic.activo=False; self.nordic.save()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,self.data).status_code,403)
        self.assertFalse(self.client.get(self.url).context['permisos']['crear'])

    def test_panel_cliente_navegacion_anio_y_aislamiento(self):
        self.guardar()
        panel = self.ruta('panel_cliente_contable')
        lista = self.client.get(reverse('clientes_contables', args=[self.empresa.slug]))
        self.assertContains(lista, panel)
        response = self.client.get(panel, {'anio':2026})
        self.assertContains(response, 'Cliente activo: Nordic')
        self.assertContains(response, self.ruta('libros_cliente_contable')+'?anio=2026')
        self.assertContains(response, self.ruta('acumulado_cliente_contable')+'?anio=2026')
        self.assertIn(2026, response.context['anios'])
        self.assertEqual(self.client.get(panel, {'anio':2024}).context['anio'],2024)
        self.assertEqual(self.client.get(panel, {'anio':'incorrecto'}).status_code,400)
        self.assertEqual(self.client.get(self.ruta('panel_cliente_contable',self.molac)).status_code,404)
        self.assertEqual(self.client.post(panel, {}).status_code,405)
        self.assertEqual(RegistroCompraFiscal.objects.count(),1)

    def test_editar_clientes_existentes_y_cambiar_asignaciones(self):
        registro = self.guardar()
        self.client.force_login(self.admin)
        lista = reverse('clientes_contables', args=[self.empresa.slug])
        for cliente in (self.nordic, self.molac, self.gecko):
            editar = self.ruta('editar_cliente_contable', cliente)
            self.assertContains(self.client.get(lista), editar)
            self.assertContains(self.client.get(self.ruta('libros_cliente_contable', cliente)), editar)
            pantalla = self.client.get(editar)
            self.assertEqual(pantalla.status_code, 200)
            self.assertEqual(set(pantalla.context['form']['usuarios'].value()), set(cliente.usuarios.values_list('pk', flat=True)))
            respuesta = self.client.post(editar, {'nombre':cliente.nombre, 'activo':'on', 'usuarios':[self.usuario.pk]}, follow=True)
            self.assertContains(respuesta, f'Cliente {cliente.nombre}: datos y usuarios asignados guardados correctamente.')
            self.assertEqual(list(cliente.usuarios.all()), [self.usuario])
        self.assertEqual(ClienteContable.objects.count(), 3)
        self.assertTrue(RegistroCompraFiscal.objects.filter(pk=registro['id'], cliente_contable=self.nordic).exists())
        self.client.post(self.ruta('editar_cliente_contable'), {'nombre':'Nordic', 'activo':'on'})
        self.assertFalse(self.nordic.usuarios.exists())
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.ruta('libros_cliente_contable', self.molac)).status_code, 200)
        self.assertNotContains(self.client.get(self.ruta('libros_cliente_contable', self.molac)), 'Editar / asignar usuarios')

    def test_permisos_de_rol_se_conservan(self):
        self.rol.puede_crear_compras=False;self.rol.puede_editar_compras=False;self.rol.save()
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,self.data).status_code,403)
        self.assertEqual(self.client.post(self.url,{'accion':'crear_proveedor','nombre':'Nuevo','rtn':'1'}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'accion':'estado','estado':'finalizado'}).status_code,403)

    def test_modelo_impide_relaciones_cruzadas_y_unicidad(self):
        registro=self.guardar()
        compra=RegistroCompraFiscal.objects.get(pk=registro['id'])
        compra.proveedor=self.proveedor_molac
        with self.assertRaises(ValidationError): compra.save()
        with self.assertRaises(ValidationError):
            ClienteContable.objects.create(empresa=self.otra,nombre='No permitido')
        with self.assertRaises(IntegrityError),transaction.atomic():
            LibroCompraMensual.objects.create(empresa=self.empresa,cliente_contable=self.nordic,anio=2026,mes=8)
        with self.assertRaises(IntegrityError),transaction.atomic():
            RegistroCompraFiscal.objects.bulk_create([RegistroCompraFiscal(empresa=self.empresa,cliente_contable=self.nordic,
                fecha_documento=date(2026,8,1),numero_factura=compra.numero_factura,
                identidad_captura=compra.identidad_captura,numero_factura_normalizado=compra.numero_factura_normalizado)])

    def test_legado_no_expone_clientes_y_demo_sigue_aislado(self):
        self.guardar()
        self.assertEqual(_reporte_impuestos_data(self.empresa,'2026-08-01','2026-08-31')['compras_resumen']['documentos'],0)
        self.assertEqual(self.client.get(reverse('ver_proveedor_facturacion',args=[self.empresa.slug,self.proveedor.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('editar_proveedor_facturacion',args=[self.empresa.slug,self.proveedor.pk])).status_code,404)
        proveedores=self.client.get(reverse('proveedores_facturacion',args=[self.empresa.slug]))
        self.assertContains(proveedores,'Propio Dubón')
        self.assertNotContains(proveedores,'Larach Nordic')
        RegistroCompraFiscal.objects.create(empresa=self.empresa,proveedor=self.legacy,proveedor_nombre='Propio Dubón',
            numero_factura='LEGADO-1',fecha_documento=date(2026,8,1),total=7)
        propios=self.client.get(reverse('libros_compras_propias',args=[self.empresa.slug]))
        self.assertEqual(propios.status_code,200)
        self.assertEqual(_reporte_impuestos_data(self.empresa,'2026-08-01','2026-08-31')['compras_resumen']['total'],Decimal('7'))
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse('libro_compras_fiscal',args=[self.empresa.slug])),reverse('clientes_contables',args=[self.empresa.slug]))
        self.assertEqual(self.client.post(reverse('captura_rapida_compras',args=[self.empresa.slug]),self.data).status_code,400)

    def test_catalogo_proveedores_crear_editar_sin_compartir(self):
        url=self.ruta('proveedores_cliente_contable')
        datos={'nombre':'Papelería','rtn':'12345678901234','activo':'on','cliente_contable':self.molac.pk}
        self.assertEqual(self.client.post(url,datos).status_code,302)
        proveedor=Proveedor.objects.get(nombre='Papelería')
        self.assertEqual(proveedor.cliente_contable,self.nordic)
        editar=self.ruta('editar_proveedor_cliente_contable',None,proveedor.pk)
        self.assertEqual(self.client.post(editar,{**datos,'nombre':'Papelería nueva'}).status_code,302)
        self.assertEqual(self.client.post(url,datos).status_code,200)
        self.assertEqual(Proveedor.objects.filter(cliente_contable=self.nordic,rtn=datos['rtn']).count(),1)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.ruta('proveedores_cliente_contable',self.molac),datos).status_code,302)
        self.assertEqual(Proveedor.objects.filter(rtn=datos['rtn']).count(),2)

    def test_clientes_iniciales_idempotentes_sin_empresas_nuevas(self):
        antes=Empresa.objects.count()
        funcion=import_module('facturacion.migrations.0075_clientes_iniciales_dubon').crear_clientes
        editor = SimpleNamespace(connection=connection)
        funcion(apps,editor);funcion(apps,editor)
        self.assertEqual(Empresa.objects.count(),antes)
        self.assertEqual(set(ClienteContable.objects.values_list('nombre',flat=True)),{'Nordic','Molac','Gecko'})


@skipUnless(os.environ.get('PLAYWRIGHT_MODULE'), 'Configura PLAYWRIGHT_MODULE para probar navegador real.')
class ClientesContablesBrowserTests(StaticLiveServerTestCase):
    def test_flujo_clientes_en_navegador(self):
        ClientesContablesTests.setUpTestData.__func__(self.__class__)
        # Sesiones independientes para verificar el cambio de usuario.
        from django.test import Client
        operador = Client(); operador.force_login(self.usuario)
        user_session = operador.cookies['sessionid'].value
        self.client.force_login(self.admin)
        env = {**os.environ, 'CAPTURA_TEST_URL':self.live_server_url,
               'CAPTURA_TEST_SESSION':self.client.cookies['sessionid'].value,
               'CLIENTES_USER_SESSION':user_session}
        result = subprocess.run(['node',str(Path(__file__).parent/'browser_tests/clientes_contables.cjs')],
                                env=env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(RegistroCompraFiscal.objects.count(),2)
        self.assertEqual(RegistroCompraFiscal.objects.get(cliente_contable=self.nordic).estado,'anulada')
        self.assertEqual(RegistroCompraFiscal.objects.get(cliente_contable=self.molac).total,Decimal('361'))
