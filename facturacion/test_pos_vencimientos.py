import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from .models import (
    CAI, BodegaInventario, Cliente, ExistenciaLoteBodega, Factura, InventarioProducto,
    LoteInventario, MovimientoInventario, MovimientoLoteBodega, PagoFactura, Producto, TipoImpuesto,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class VentaPosLotesVencidosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        hoy = timezone.localdate()
        cls.empresa = Empresa.objects.create(
            nombre="Hospital Mía POS", slug="hospital_mia", rtn="08011999111111",
            tipo_solucion="clinica", estado_licencia="activa",
        )
        for codigo, nombre in (("facturacion", "Facturación"), ("punto_venta", "Punto de Venta")):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})
            EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=cls.empresa, ventas_solo_desde_vitrina=True)
        rol = RolSistema.objects.create(
            nombre="Operador POS sin impresión", codigo="pos-vencimientos", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True, puede_facturas=True, puede_ver_facturas=True,
            puede_crear_facturas=True, puede_punto_venta=True, puede_imprimir_facturas=False,
            puede_productos=True, puede_editar_productos=True,
        )
        cls.usuario = Usuario.objects.create_user(username="operador-pos-vencimientos", empresa=cls.empresa, rol_sistema=rol)
        rol_configurador = RolSistema.objects.create(
            nombre="Configurador de vencimientos", codigo="config-vencimientos",
            puede_facturas=True, puede_productos=True, puede_editar_productos=True,
            puede_configuracion_facturacion=True,
        )
        cls.configurador = Usuario.objects.create_user(
            username="configurador-vencimientos", empresa=cls.empresa, rol_sistema=rol_configurador,
        )
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Paciente POS", rtn="08011999121212")
        cls.bodega = BodegaInventario.objects.create(empresa=cls.empresa, nombre="Vitrina", tipo="vitrina")
        cls.impuesto = TipoImpuesto.objects.create(nombre="Exento POS vencimientos", porcentaje=0)
        CAI.objects.create(
            empresa=cls.empresa, numero_cai="CAI-POS-VENCIMIENTOS", uso_documento="factura",
            establecimiento="001", punto_emision="001", tipo_documento="01",
            rango_inicial=1, rango_final=100, correlativo_actual=0,
            fecha_activacion=hoy - timedelta(days=1), fecha_limite=hoy + timedelta(days=30),
        )
        cls.producto, cls.lote = cls.crear_producto_con_lote_vencido("A", hoy)
        cls.otro_producto, cls.otro_lote = cls.crear_producto_con_lote_vencido("B", hoy)

    @classmethod
    def crear_producto_con_lote_vencido(cls, codigo, hoy):
        producto = Producto.objects.create(
            empresa=cls.empresa, codigo=codigo, nombre=f"Producto {codigo}", precio=100,
            costo_promedio=20, controla_inventario=True, impuesto_predeterminado=cls.impuesto,
        )
        InventarioProducto.objects.create(empresa=cls.empresa, producto=producto, existencias=4, valor_contable=80)
        lote = LoteInventario.objects.create(
            empresa=cls.empresa, producto=producto, numero_lote=f"VENCIDO-{codigo}",
            fecha_vencimiento=hoy - timedelta(days=1), activo=True,
        )
        ExistenciaLoteBodega.objects.create(empresa=cls.empresa, bodega=cls.bodega, lote=lote, cantidad=4)
        return producto, lote

    def setUp(self):
        self.client.force_login(self.usuario)

    def vender(self, producto):
        return self.client.post(
            reverse("punto_venta", args=[self.empresa.slug]),
            {"payload": json.dumps({
                "cliente_id": self.cliente.pk, "metodo": "efectivo",
                "items": [{"producto_id": producto.pk, "cantidad": "2", "precio_unitario": "100.00"}],
            })},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def comprobar_saldo(self, producto, lote, cantidad):
        self.assertEqual(InventarioProducto.objects.get(producto=producto).existencias, Decimal(cantidad))
        self.assertEqual(ExistenciaLoteBodega.objects.get(lote=lote, bodega=self.bodega).cantidad, Decimal(cantidad))

    def comprobar_venta_rechazada(self, producto):
        response = self.vender(producto)
        self.assertEqual(response.status_code, 400)
        resultado = response.json()
        self.assertFalse(resultado["ok"])
        self.assertIn(f"{producto.nombre}: en Vitrina 0.00, solicitado 2.00", resultado["error"])

    def test_cambiar_excepcion_desde_editar_producto_requiere_permiso_de_configuracion(self):
        url = reverse("editar_producto_facturacion", args=[self.empresa.slug, self.producto.pk])
        self.assertEqual(self.client.post(url, {"venta_lotes_vencidos": "permitir"}).status_code, 403)
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.permitir_venta_lotes_vencidos)

        self.client.force_login(self.configurador)
        for accion, esperado in (("permitir", True), ("bloquear", False)):
            with self.subTest(accion=accion):
                response = self.client.post(url, {"venta_lotes_vencidos": accion})
                self.assertRedirects(response, url, fetch_redirect_response=False)
                self.producto.refresh_from_db()
                self.otro_producto.refresh_from_db()
                self.assertIs(self.producto.permitir_venta_lotes_vencidos, esperado)
                self.assertFalse(self.otro_producto.permitir_venta_lotes_vencidos)
                self.comprobar_saldo(self.producto, self.lote, 4)

    def test_pos_aplica_la_excepcion_de_vencimiento_solo_al_producto_habilitado(self):
        facturas = Factura.objects.filter(empresa=self.empresa)
        movimientos = MovimientoInventario.objects.filter(empresa=self.empresa)
        movimientos_lote = MovimientoLoteBodega.objects.filter(empresa=self.empresa)

        self.assertFalse(self.producto.permitir_venta_lotes_vencidos)
        self.comprobar_venta_rechazada(self.producto)
        self.comprobar_saldo(self.producto, self.lote, 4)
        self.assertFalse(facturas.exists())
        self.assertFalse(movimientos.exists())
        self.assertFalse(movimientos_lote.exists())

        self.producto.permitir_venta_lotes_vencidos = True
        self.producto.save(update_fields=["permitir_venta_lotes_vencidos"])
        self.comprobar_venta_rechazada(self.otro_producto)
        self.comprobar_saldo(self.otro_producto, self.otro_lote, 4)
        response = self.vender(self.producto)
        self.assertEqual(response.status_code, 200, response.content)
        resultado = response.json()
        self.assertTrue(resultado["ok"])
        factura = facturas.get(pk=resultado["factura_id"])
        self.assertEqual((factura.estado, factura.estado_pago), ("emitida", "pagado"))
        self.assertEqual(PagoFactura.objects.get(factura=factura).monto, Decimal("200.00"))
        self.comprobar_saldo(self.producto, self.lote, 2)
        movimiento = movimientos.get(factura=factura)
        self.assertEqual((movimiento.tipo, movimiento.producto_id, movimiento.cantidad), ("salida_factura", self.producto.pk, Decimal(2)))
        salida_lote = movimientos_lote.get(factura=factura)
        self.assertEqual(salida_lote.tipo, "salida_factura")
        self.assertEqual((salida_lote.lote_id, salida_lote.bodega_id), (self.lote.pk, self.bodega.pk))
        self.assertEqual((salida_lote.existencia_anterior, salida_lote.cantidad, salida_lote.existencia_resultante), (Decimal(4), Decimal(2), Decimal(2)))
        self.assertEqual(salida_lote.usuario_id, self.usuario.pk)

        self.producto.permitir_venta_lotes_vencidos = False
        self.producto.save(update_fields=["permitir_venta_lotes_vencidos"])
        self.comprobar_venta_rechazada(self.producto)
        self.comprobar_saldo(self.producto, self.lote, 2)
        self.comprobar_saldo(self.otro_producto, self.otro_lote, 4)
        self.otro_producto.refresh_from_db()
        self.assertFalse(self.otro_producto.permitir_venta_lotes_vencidos)
        self.assertEqual((facturas.count(), movimientos.count(), movimientos_lote.count()), (1, 1, 1))
