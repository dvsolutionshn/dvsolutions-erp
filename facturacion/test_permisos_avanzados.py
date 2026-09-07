from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, RequestFactory
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from core.models import Empresa, RegistroAuditoria, RolSistema, UsuarioEmpresaPermiso
from facturacion.models import Factura, PagoFactura, Cliente
from facturacion import tests as fixtures
from facturacion import views


class PermisosAvanzadosTests(TestCase):
    setUp = fixtures.FacturacionTests.setUp
    crear_factura_con_linea = fixtures.FacturacionTests.crear_factura_con_linea

    def datos_edicion(self, factura):
        linea = factura.lineas.get()
        return {
            'cliente': self.cliente.pk, 'fecha_emision': str(factura.fecha_emision),
            'fecha_vencimiento': '', 'vendedor': '', 'tipo_cambio': '1',
            'moneda': 'HNL', 'estado': factura.estado, 'motivo_auditoria': 'Corrección solicitada por cliente',
            'lineas-TOTAL_FORMS': '1', 'lineas-INITIAL_FORMS': '1',
            'lineas-0-id': linea.pk, 'lineas-0-producto': self.producto.pk,
            'lineas-0-cantidad': '2', 'lineas-0-precio_unitario': '50',
            'lineas-0-descuento_porcentaje': '10', 'lineas-0-impuesto': self.impuesto.pk,
        }

    def habilitar_fecha(self):
        self.rol_total.puede_cambiar_fecha_factura = True
        self.rol_total.save()

    def test_editar_recalcula_y_registra_lineas(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        respuesta = self.client.post(reverse('editar_factura', args=[self.empresa.slug, factura.pk]), self.datos_edicion(factura))
        self.assertEqual(respuesta.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.subtotal, Decimal('90'))
        self.assertEqual(factura.impuesto, Decimal('13.50'))
        self.assertEqual(factura.total, Decimal('103.50'))
        registro = RegistroAuditoria.objects.filter(objeto_id=str(factura.pk), cambios__accion_factura__nuevo='editar').get()
        self.assertEqual(registro.usuario, self.user)
        self.assertEqual(registro.empresa, self.empresa)
        self.assertTrue(registro.fecha)
        self.assertTrue(registro.motivo)
        self.assertIn('lineas', registro.cambios['factura']['anterior'])

    def test_editar_no_autoriza_fecha_manipulada(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        datos = self.datos_edicion(factura)
        datos['fecha_emision'] = str(factura.fecha_emision - timedelta(days=1))
        respuesta = self.client.post(reverse('editar_factura', args=[self.empresa.slug, factura.pk]), datos)
        self.assertEqual(respuesta.status_code, 403)

    def test_fecha_independiente_ignora_otros_campos_y_audita(self):
        self.habilitar_fecha()
        self.rol_total.puede_editar_facturas = False
        self.rol_total.save()
        factura = self.crear_factura_con_linea()
        nueva = factura.fecha_emision + timedelta(days=1)
        respuesta = self.client.post(reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk]), {
            'fecha_emision': str(nueva), 'motivo_auditoria': 'Corrección de fecha solicitada',
            'estado': 'anulada', 'total': '0',
        })
        self.assertEqual(respuesta.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.fecha_emision, nueva)
        self.assertEqual(factura.estado, 'emitida')
        self.assertGreater(factura.total, 0)
        self.assertTrue(RegistroAuditoria.objects.filter(usuario=self.user, objeto_id=str(factura.pk), cambios__accion_factura__nuevo='cambiar_fecha').exists())

    def test_fecha_fuera_cai_y_motivo_obligatorio(self):
        self.habilitar_fecha()
        factura = self.crear_factura_con_linea()
        original = factura.fecha_emision
        url = reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk])
        for datos in ({'fecha_emision': str(original)}, {'fecha_emision': str(self.cai.fecha_limite + timedelta(days=1)), 'motivo_auditoria': 'Corregir fecha del documento'}):
            self.assertEqual(self.client.post(url, datos).status_code, 200)
            factura.refresh_from_db()
            self.assertEqual(factura.fecha_emision, original)

    def test_vistas_protegen_acceso_sin_middleware(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        sin_permiso = RolSistema.objects.create(nombre='Sin permisos', codigo='sin-permisos')
        UsuarioEmpresaPermiso.objects.create(usuario=self.user, empresa=self.empresa, rol_sistema=sin_permiso)
        for vista in (views.editar_factura, views.cambiar_fecha_factura, views.anular_factura):
            request = RequestFactory().post('/', {'motivo': 'Prueba de seguridad'})
            request.user = self.user
            with self.assertRaises(PermissionDenied):
                vista(request, self.empresa.slug, factura.pk)

    def test_otro_tenant_y_factura_ajena(self):
        otra = Empresa.objects.create(nombre='Otra', slug='otra-segura')
        request = RequestFactory().post('/')
        request.user = self.user
        for vista in (views.editar_factura, views.cambiar_fecha_factura, views.anular_factura):
            with self.assertRaises(PermissionDenied):
                vista(request, otra.slug, 1)
        self.habilitar_fecha()
        factura = self.crear_factura_con_linea(estado='borrador')
        Factura.objects.filter(pk=factura.pk).update(empresa=otra)
        self.assertEqual(self.client.get(reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk])).status_code, 404)

    def test_anular_conserva_factura_y_audita(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        respuesta = self.client.post(reverse('anular_factura', args=[self.empresa.slug, factura.pk]), {'motivo': 'Cancelación solicitada'})
        self.assertEqual(respuesta.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, 'anulada')
        self.assertTrue(factura.lineas.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(usuario=self.user, objeto_id=str(factura.pk), cambios__accion_factura__nuevo='anular').exists())

    def test_fallo_auditoria_revierte_operacion(self):
        self.habilitar_fecha()
        factura = self.crear_factura_con_linea(estado='borrador')
        with patch.object(views, '_auditar_cambio_factura', side_effect=RuntimeError('Auditoría no disponible')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk]), {'fecha_emision': str(factura.fecha_emision + timedelta(days=1)), 'motivo_auditoria': 'Corregir fecha del documento'})
        original = factura.fecha_emision
        factura.refresh_from_db()
        self.assertEqual(factura.fecha_emision, original)

    def test_asignacion_preserva_rol_compartido_y_es_revocable(self):
        call_command('habilitar_permisos_facturacion', usuario=self.user.username, empresa_ids=[self.empresa.pk])
        self.rol_total.refresh_from_db()
        self.assertFalse(self.rol_total.puede_cambiar_fecha_factura)
        self.assertTrue(self.user.tiene_permiso_erp('puede_cambiar_fecha_factura', self.empresa))
        personal = self.user.rol_para_empresa(self.empresa)
        self.assertTrue(personal.puede_crear_facturas)
        personal.puede_cambiar_fecha_factura = False
        personal.save()
        self.assertFalse(self.user.tiene_permiso_erp('puede_cambiar_fecha_factura', self.empresa))

    def test_asignacion_permite_localizar_usuario_por_email(self):
        self.user.email = 'dracandyluque@gmail.com'
        self.user.save(update_fields=['email'])

        call_command(
            'habilitar_permisos_facturacion',
            usuario_email='dracandyluque@gmail.com',
            empresa_ids=[self.empresa.pk],
        )

        self.assertTrue(self.user.tiene_permiso_erp('puede_editar_facturas', self.empresa))
        self.assertTrue(self.user.tiene_permiso_erp('puede_cambiar_fecha_factura', self.empresa))
        self.assertTrue(self.user.tiene_permiso_erp('puede_anular_facturas', self.empresa))

    def test_botones_ocultos_y_url_denegada_tras_revocacion(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        self.habilitar_fecha()
        url = reverse('ver_factura', args=[self.empresa.slug, factura.pk])
        respuesta = self.client.get(url)
        fecha_url = reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk])
        self.assertContains(respuesta, fecha_url)
        self.rol_total.puede_cambiar_fecha_factura = False
        self.rol_total.puede_editar_facturas = False
        self.rol_total.puede_anular_facturas = False
        self.rol_total.save()
        respuesta = self.client.get(url)
        for accion in ('editar_factura', 'anular_factura', 'cambiar_fecha_factura'):
            accion_url = reverse(accion, args=[self.empresa.slug, factura.pk])
            self.assertNotContains(respuesta, accion_url)
            self.assertEqual(self.client.post(accion_url, {'motivo': 'Intento directo'}).status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, 'borrador')

    def test_edicion_rechaza_cliente_de_otra_empresa(self):
        factura = self.crear_factura_con_linea(estado='borrador')
        otra = Empresa.objects.create(nombre='Otra', slug='otra-cliente')
        cliente = Cliente.objects.create(empresa=otra, nombre='Cliente ajeno')
        datos = self.datos_edicion(factura)
        datos['cliente'] = cliente.pk
        respuesta = self.client.post(reverse('editar_factura', args=[self.empresa.slug, factura.pk]), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('cliente', respuesta.context['form'].errors)
        factura.refresh_from_db()
        self.assertEqual(factura.cliente, self.cliente)

    def test_edicion_pagada_no_reduce_total_bajo_cobros(self):
        factura = self.crear_factura_con_linea()
        PagoFactura.objects.create(factura=factura, monto=Decimal('115'), metodo='efectivo', fecha=factura.fecha_emision)
        respuesta = self.client.post(reverse('editar_factura', args=[self.empresa.slug, factura.pk]), self.datos_edicion(factura))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'no puede ser menor que los pagos')
        factura.refresh_from_db()
        self.assertEqual(factura.total, Decimal('115'))
        self.assertEqual(factura.lineas.get().precio_unitario, Decimal('100'))

    def test_edicion_no_anula_ni_reabre_estado_emitido(self):
        factura = self.crear_factura_con_linea()
        datos = self.datos_edicion(factura)
        datos['estado'] = 'anulada'
        respuesta = self.client.post(reverse('editar_factura', args=[self.empresa.slug, factura.pk]), datos)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, 'emitida')

    def test_anulada_no_se_edita_ni_cambia_fecha(self):
        self.habilitar_fecha()
        factura = self.crear_factura_con_linea(estado='borrador')
        self.client.post(reverse('anular_factura', args=[self.empresa.slug, factura.pk]), {'motivo': 'Anulación solicitada'})
        self.assertEqual(self.client.get(reverse('editar_factura', args=[self.empresa.slug, factura.pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse('cambiar_fecha_factura', args=[self.empresa.slug, factura.pk]), {'fecha_emision': '2026-01-01', 'motivo_auditoria': 'Intento modificar anulada'}).status_code, 403)
