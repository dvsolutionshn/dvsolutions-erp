"""Regresión del POST real: última unidad vendida y corrección fiscal posterior."""

from contextlib import ExitStack
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from .models import (
    CAI, BodegaInventario, Cliente, ExistenciaLoteBodega, Factura,
    InventarioProducto, LoteInventario, MovimientoInventario,
    MovimientoLoteBodega, Producto, TipoImpuesto,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionUltimaUnidadTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia", slug="hospital_mia", rtn="08011999003342",
            estado_licencia="activa", tipo_solucion="clinica",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo)
        config = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        config.permite_cai_historico = True
        config.ventas_solo_desde_vitrina = True
        config.save(update_fields=["permite_cai_historico", "ventas_solo_desde_vitrina"])
        rol = RolSistema.objects.create(
            nombre="Editor última unidad", codigo="editor-ultima-unidad",
            puede_facturas=True, puede_ver_facturas=True, puede_crear_facturas=True,
            puede_editar_facturas=True, puede_cambiar_fecha_factura=True,
            puede_inventario=True,
        )
        self.usuario = Usuario.objects.create_user(username="editor-ultima-unidad", empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(self.usuario)
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre="Servicios Médicos de prueba", rtn="0801199912345")
        self.exonerado = TipoImpuesto.objects.create(nombre="Exonerado", porcentaje=0)
        self.exento = TipoImpuesto.objects.create(nombre="Exento", porcentaje=0)
        CAI.objects.create(
            empresa=self.empresa, numero_cai="CAI-ULTIMA-UNIDAD", uso_documento="factura",
            establecimiento="000", punto_emision="001", tipo_documento="01",
            rango_inicial=1, rango_final=9999, correlativo_actual=3341,
            fecha_activacion=self.hoy - timedelta(days=30), fecha_limite=self.hoy + timedelta(days=365),
        )
        self.bodega = BodegaInventario.objects.create(empresa=self.empresa, nombre="Vitrina", tipo="vitrina")
        self.producto = Producto.objects.create(
            empresa=self.empresa, nombre="ESOMEPRAZOL 40 MG", codigo="ESOME40",
            controla_inventario=True, precio=100, costo_promedio=2,
            impuesto_predeterminado=self.exonerado,
        )
        InventarioProducto.objects.create(empresa=self.empresa, producto=self.producto, existencias=1, valor_contable=2)
        self.lote = LoteInventario.objects.create(
            empresa=self.empresa, producto=self.producto, numero_lote="ESOME-ULTIMO",
            fecha_vencimiento=self.hoy + timedelta(days=90),
        )
        ExistenciaLoteBodega.objects.create(empresa=self.empresa, bodega=self.bodega, lote=self.lote, cantidad=1)

    def cabecera(self):
        return {
            "cliente": str(self.cliente.pk), "fecha_emision": str(self.hoy), "fecha_vencimiento": "",
            "vendedor": str(self.usuario.pk), "tipo_cambio": "1.0000", "moneda": "HNL", "estado": "emitida",
            "orden_compra_exenta": "", "registro_exonerado": "", "registro_sag": "",
            "motivo_auditoria": "Corrección de Exonerado a Exento en última unidad vendida",
            "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000",
        }

    def fila(self, indice, cantidad="1.00", linea=None, impuesto=None):
        valores = {
            "id": str(linea.pk) if linea else "", "producto": str(self.producto.pk),
            "descripcion_manual": "", "cantidad": str(cantidad), "precio_unitario": "100.00",
            "descuento_porcentaje": "0", "comentario": "", "impuesto": str((impuesto or self.exonerado).pk),
        }
        return {f"lineas-{indice}-{campo}": valor for campo, valor in valores.items()}

    def emitir(self, cantidades=("1.00",)):
        datos = self.cabecera()
        datos.update({"lineas-TOTAL_FORMS": str(len(cantidades)), "lineas-INITIAL_FORMS": "0"})
        for indice, cantidad in enumerate(cantidades):
            datos.update(self.fila(indice, cantidad))
        response = self.client.post(reverse("crear_factura", args=[self.empresa.slug]), datos)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.factura = Factura.objects.get(empresa=self.empresa)
        self.assertEqual(self.factura.numero_factura, "000-001-01-00003342")
        self.assertEqual(self.factura.estado, "emitida")
        self.assert_cero()
        self.assertEqual(MovimientoInventario.objects.filter(factura=self.factura, tipo="salida_factura").count(), len(cantidades))
        self.assertEqual(MovimientoLoteBodega.objects.filter(factura=self.factura, tipo="salida_factura").get().cantidad, Decimal("1"))

    def datos_edicion(self):
        lineas = list(self.factura.lineas.order_by("pk"))
        datos = self.cabecera()
        datos.update({
            "numero_factura": self.factura.numero_factura, "numero_factura_sufijo": "342",
            "lineas-TOTAL_FORMS": str(len(lineas)), "lineas-INITIAL_FORMS": str(len(lineas)),
        })
        for indice, linea in enumerate(lineas):
            datos.update(self.fila(indice, linea.cantidad, linea, self.exento))
        return datos

    def assert_cero(self):
        self.assertEqual(InventarioProducto.objects.get(producto=self.producto).existencias, Decimal("0"))
        self.assertEqual(ExistenciaLoteBodega.objects.get(lote=self.lote, bodega=self.bodega).cantidad, Decimal("0"))

    def marca(self):
        return (
            list(InventarioProducto.objects.filter(producto=self.producto).values()),
            list(ExistenciaLoteBodega.objects.filter(lote=self.lote).values()),
            list(MovimientoInventario.objects.filter(factura=self.factura).order_by("pk").values()),
            list(MovimientoLoteBodega.objects.filter(factura=self.factura).order_by("pk").values()),
        )

    def no_tocar_inventario(self):
        stack = ExitStack()
        for nombre in (
            "_validar_stock_disponible_para_lineas", "_validar_stock_vitrina_para_lineas",
            "_registrar_salida_factura", "_registrar_salida_fefo_factura",
            "_registrar_salida_vitrina_factura", "_revertir_salida_factura",
            "_registrar_movimiento_inventario", "_registrar_movimiento_lote_bodega",
        ):
            stack.enter_context(patch(f"facturacion.views.{nombre}", side_effect=AssertionError(f"Edición fiscal llamó {nombre}")))
        return stack

    def editar(self, datos):
        response = self.client.post(reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]), datos)
        errores = ""
        if response.status_code == 200:
            errores = {
                "form": response.context["form"].errors,
                "formset": response.context["formset"].errors,
                "mensajes": [str(mensaje) for mensaje in response.context["messages"]],
            }
        self.assertEqual(response.status_code, 302, errores)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "emitida")
        self.assertEqual(Factura.objects.filter(empresa=self.empresa).count(), 1)
        self.assert_cero()

    def test_ultima_unidad_correccion_fiscal_reenvio_y_aumento_validan_solo_diferencia(self):
        self.emitir()
        antes = self.marca()
        datos = self.datos_edicion()
        with self.no_tocar_inventario():
            self.editar(datos)
            self.editar(datos)
        self.assertEqual(self.factura.lineas.get().impuesto_id, self.exento.pk)
        self.assertEqual(self.marca(), antes)
        kardex = self.client.get(reverse("kardex_inventario", args=[self.empresa.slug]), {"producto": str(self.producto.pk)})
        self.assertEqual(kardex.status_code, 200)
        self.assertEqual((kardex.context["resumen"]["salidas"], kardex.context["resumen"]["entradas"]), (1, 0))
        datos["lineas-0-cantidad"] = "3.00"
        response = self.client.post(reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]), datos)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solicitado 2.00")
        self.assertNotContains(response, "solicitado 3.00")
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "emitida")
        self.assertEqual(self.factura.lineas.get().cantidad, Decimal("1"))
        self.assertEqual(self.marca(), antes)

    def test_recrear_identidad_de_linea_con_producto_y_cantidad_iguales_no_es_venta_nueva(self):
        self.emitir()
        original = self.factura.lineas.get()
        antes = self.marca()
        datos = self.datos_edicion()
        datos.update({"lineas-TOTAL_FORMS": "2", "lineas-0-DELETE": "on"})
        datos.update(self.fila(1, "1.00", impuesto=self.exento))
        with self.no_tocar_inventario():
            self.editar(datos)
            self.assertNotEqual(self.factura.lineas.get().pk, original.pk)
            self.editar(self.datos_edicion())
        self.assertEqual(self.factura.lineas.get().costo_contable_total, Decimal("2"))
        self.assertEqual(self.marca(), antes)

    def test_producto_repetido_en_lineas_compara_cantidad_total_al_corregir_impuestos(self):
        self.emitir(("0.40", "0.60"))
        antes = self.marca()
        datos = self.datos_edicion()
        datos.update({"lineas-0-cantidad": "0.20", "lineas-1-cantidad": "0.80"})
        with self.no_tocar_inventario():
            self.editar(datos)
        self.assertEqual(sum(self.factura.lineas.values_list("cantidad", flat=True)), Decimal("1"))
        self.assertEqual(self.marca(), antes)

    def test_nueva_factura_sin_existencia_sigue_rechazada(self):
        self.emitir()
        antes = self.marca()
        datos = self.cabecera()
        datos.update({"lineas-TOTAL_FORMS": "1", "lineas-INITIAL_FORMS": "0"})
        datos.update(self.fila(0))
        response = self.client.post(reverse("crear_factura", args=[self.empresa.slug]), datos)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuficiente en Vitrina")
        self.assertContains(response, "solicitado 1.00")
        self.assertEqual(Factura.objects.filter(empresa=self.empresa).count(), 1)
        self.assertEqual(self.marca(), antes)

    def test_payload_sin_ids_de_linea_no_agrega_otra_venta_de_la_ultima_unidad(self):
        self.emitir()
        linea_original = self.factura.lineas.get()
        antes = self.marca()
        datos = self.datos_edicion()
        datos.update({"lineas-INITIAL_FORMS": "0", "lineas-0-id": ""})
        self.editar(datos)
        with self.no_tocar_inventario():
            self.editar(datos)
        self.assertEqual(self.factura.lineas.count(), 1)
        self.assertEqual(self.factura.lineas.get().pk, linea_original.pk)
        self.assertEqual(self.factura.lineas.get().impuesto_id, self.exento.pk)
        self.assertEqual(self.marca(), antes)

    def test_productos_repetidos_sin_ids_se_comparan_como_documento_editado_completo(self):
        self.emitir(("0.50", "0.50"))
        ids_originales = list(self.factura.lineas.order_by("pk").values_list("pk", flat=True))
        antes = self.marca()
        datos = self.datos_edicion()
        datos.update({"lineas-INITIAL_FORMS": "0", "lineas-0-id": "", "lineas-1-id": ""})
        with self.no_tocar_inventario():
            self.editar(datos)
            self.editar(datos)
        self.assertEqual(list(self.factura.lineas.order_by("pk").values_list("pk", flat=True)), ids_originales)
        self.assertEqual(sum(self.factura.lineas.values_list("cantidad", flat=True)), Decimal("1"))
        self.assertEqual(self.marca(), antes)

    def test_reenvio_identico_al_agregar_producto_descuenta_una_sola_vez(self):
        self.emitir()
        nuevo = Producto.objects.create(
            empresa=self.empresa, nombre="Producto añadido una vez", codigo="NUEVO-UNA-VEZ",
            controla_inventario=True, precio=100, costo_promedio=2,
            impuesto_predeterminado=self.exento,
        )
        inventario = InventarioProducto.objects.create(empresa=self.empresa, producto=nuevo, existencias=2, valor_contable=4)
        lote = LoteInventario.objects.create(
            empresa=self.empresa, producto=nuevo, numero_lote="NUEVO-UNA-VEZ",
            fecha_vencimiento=self.hoy + timedelta(days=90),
        )
        existencia = ExistenciaLoteBodega.objects.create(empresa=self.empresa, bodega=self.bodega, lote=lote, cantidad=2)
        datos = self.datos_edicion()
        datos.update({"lineas-TOTAL_FORMS": "2"})
        datos.update(self.fila(1, impuesto=self.exento))
        datos["lineas-1-producto"] = str(nuevo.pk)
        self.editar(datos)
        inventario.refresh_from_db()
        existencia.refresh_from_db()
        self.assertEqual((inventario.existencias, existencia.cantidad), (Decimal("1"), Decimal("1")))
        despues = self.marca()
        with self.no_tocar_inventario():
            self.editar(datos)
        inventario.refresh_from_db()
        existencia.refresh_from_db()
        self.assertEqual((inventario.existencias, existencia.cantidad), (Decimal("1"), Decimal("1")))
        self.assertEqual(self.factura.lineas.filter(producto=nuevo).count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(factura=self.factura, producto=nuevo, tipo="salida_factura").count(), 1)
        self.assertEqual(self.marca(), despues)
