from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.db import IntegrityError, transaction

from core.models import RolSistema
from contabilidad.views import _reporte_impuestos_data
from .models import LibroCompraMensual, RegistroCompraFiscal
from .test_captura_rapida import CapturaRapidaTests


class LibrosMensualesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CapturaRapidaTests.setUpTestData.__func__(cls)

    def setUp(self):
        CapturaRapidaTests.setUp(self)
        self.url = reverse('captura_rapida_compras_periodo', args=['demo_1',2026,8])
        self.julio = reverse('captura_rapida_compras_periodo', args=['demo_1',2026,7])
        self.selector = reverse('libro_compras_fiscal', args=['demo_1'])
        self.data['fecha_documento'] = '25/7/26'

    def guardar(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['registro']

    def editar(self, registro, **cambios):
        return self.client.post(self.url, {**self.data,'accion':'editar','registro_id':registro['id'],
                                          'version':registro['version'],**cambios})

    def test_mes_del_libro_independiente_persistencia_totales_y_reportes(self):
        registro = self.guardar()
        compra = RegistroCompraFiscal.objects.get()
        self.assertEqual(compra.fecha_documento,date(2026,7,25))
        self.assertEqual((compra.periodo_anio,compra.periodo_mes),(2026,8))
        self.assertEqual(LibroCompraMensual.objects.get().registros.get(),compra)
        cuadro = self.client.get(self.url,{'accion':'cuadro'}).json()
        self.assertEqual(cuadro['registros'][0]['id'],registro['id'])
        self.assertEqual(cuadro['resumen']['total'],'351.41')
        self.assertEqual(self.client.get(self.julio,{'accion':'cuadro'}).json()['registros'],[])
        meses = self.client.get(self.selector,{'anio':2026}).context['meses']
        self.assertEqual(meses[7]['resumen']['documentos'],1)
        self.assertEqual(meses[6]['resumen']['documentos'],0)
        self.assertEqual(_reporte_impuestos_data(self.empresa,'2026-08-01','2026-08-31')['compras_resumen']['documentos'],1)
        self.assertEqual(_reporte_impuestos_data(self.empresa,'2026-07-01','2026-07-31')['compras_resumen']['documentos'],0)
        self.assertEqual(self.client.post(self.julio,self.data).status_code,409)

    def test_selector_doce_meses_y_periodo_obligatorio(self):
        response = self.client.get(self.selector,{'anio':2026})
        self.assertEqual(len(response.context['meses']),12)
        for nombre in ('Enero','Agosto','Diciembre'):
            self.assertContains(response,nombre)
        self.assertEqual(self.client.post(reverse('captura_rapida_compras',args=['demo_1']),self.data).status_code,400)
        self.assertEqual(self.client.get(self.selector,{'anio':'abc'}).status_code,400)
        self.assertEqual(self.client.get(reverse('captura_rapida_compras_periodo',args=['demo_1',2026,13])).status_code,404)

    def test_edicion_anulacion_y_totales_sin_mover_periodo(self):
        registro = self.guardar()
        response = self.editar(registro,fecha_documento='5/6/26',exento='10',base_15='200',base_18='')
        self.assertEqual(response.status_code,200,response.content)
        compra = RegistroCompraFiscal.objects.get()
        self.assertEqual(compra.total,Decimal('240'))
        self.assertEqual(compra.periodo_mes,8)
        self.assertEqual(compra.fecha_documento,date(2026,6,5))
        editada = response.json()['registro']
        response = self.client.post(self.url,{'accion':'anular','registro_id':compra.pk,'version':editada['version']})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['resumen']['documentos'],0)
        self.assertEqual(Decimal(response.json()['resumen']['total']),0)
        compra.refresh_from_db()
        self.assertEqual(compra.estado,'anulada')
        self.assertEqual(len(self.client.get(self.url,{'accion':'cuadro'}).json()['registros']),1)

    def test_conflicto_de_edicion_y_aislamiento_por_periodo(self):
        registro = self.guardar()
        datos = {**self.data,'accion':'editar','registro_id':registro['id'],'version':registro['version']}
        self.assertEqual(self.client.post(self.julio,datos).status_code,404)
        ajeno = reverse('captura_rapida_compras_periodo',args=['otra',2026,8])
        self.assertEqual(self.client.post(ajeno,datos).status_code,404)
        self.assertEqual(self.editar(registro,exento='1').status_code,200)
        self.assertEqual(self.editar(registro,exento='2').status_code,409)
        self.assertEqual(RegistroCompraFiscal.objects.get().exento,Decimal('1'))

    def test_finalizar_reabrir_y_editar_finalizado(self):
        registro = self.guardar()
        response = self.client.post(self.url,{'accion':'estado','estado':'finalizado'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.get(self.url,{'accion':'cuadro'}).json()['estado_libro'],'finalizado')
        self.assertEqual(self.client.post(self.url,{**self.data,'numero_factura':'000001011'}).status_code,409)
        self.assertEqual(self.editar(registro,exento='1').status_code,200)
        self.assertEqual(self.client.post(self.url,{'accion':'estado','estado':'en_proceso'}).status_code,200)
        self.assertEqual(self.client.post(self.url,{**self.data,'numero_factura':'000001011'}).status_code,201)
        self.assertEqual(LibroCompraMensual.objects.count(),1)

    def test_consulta_y_acciones_con_permisos_independientes(self):
        registro = self.guardar()
        rol = RolSistema.objects.create(nombre='Consulta libros',codigo='consulta-libros',puede_compras=True)
        usuario = get_user_model().objects.create_user(username='consulta',empresa=self.empresa,rol_sistema=rol)
        self.empresa.estado_licencia='activa'; self.empresa.save()
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,self.data).status_code,403)
        self.assertEqual(self.editar(registro).status_code,403)
        self.assertEqual(self.client.post(self.url,{'accion':'estado','estado':'finalizado'}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'accion':'anular','registro_id':registro['id'],'version':registro['version']}).status_code,403)
        rol.puede_editar_compras=True; rol.save()
        self.assertEqual(self.editar(registro,exento='5').status_code,200)
        self.assertEqual(self.client.post(self.url,{**self.data,'accion':'editar','registro_id':0}).status_code,400)
        self.assertEqual(RegistroCompraFiscal.objects.count(),1)

    def test_historico_sin_proveedor_no_se_mueve_y_puede_corregirse(self):
        compra = RegistroCompraFiscal.objects.create(empresa=self.empresa,proveedor_nombre='Histórico',
            numero_factura='ABC-1',fecha_documento=date(2025,12,25),periodo_anio=2026,periodo_mes=8,
            exento=10,exonerado=5,total=15)
        cuadro = self.client.get(self.url,{'accion':'cuadro'}).json()
        self.assertEqual(cuadro['registros'][0]['id'],compra.pk)
        self.assertFalse(LibroCompraMensual.objects.exists())  # Consultar no reescribe documentos.
        registro = cuadro['registros'][0]
        response = self.editar(registro,proveedor='',numero_factura='ABC-1',exento='20',base_15='',base_18='')
        self.assertEqual(response.status_code,200,response.content)
        compra.refresh_from_db()
        self.assertEqual(compra.total,Decimal('25'))
        self.assertEqual(compra.exonerado,Decimal('5'))
        self.assertIsNone(compra.proveedor)

    def test_cabecera_unica_y_otros_reportes_sin_cambios(self):
        self.guardar()
        with self.assertRaises(IntegrityError),transaction.atomic():
            LibroCompraMensual.objects.create(empresa=self.empresa,anio=2026,mes=8)
        RegistroCompraFiscal.objects.create(empresa=self.otra,proveedor_nombre='Otro',numero_factura='001',
            fecha_documento=date(2026,7,25),periodo_anio=2026,periodo_mes=8,total=10)
        self.assertEqual(_reporte_impuestos_data(self.otra,'2026-07-01','2026-07-31')['compras_resumen']['documentos'],1)
        self.assertEqual(self.client.get(reverse('libro_compras_fiscal',args=['otra'])).status_code,200)
