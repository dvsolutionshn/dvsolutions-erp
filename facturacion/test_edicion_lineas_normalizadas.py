"""Colecciones recreadas y reenvíos conservan el inventario de la factura."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Factura, LineaFactura, MovimientoInventario
from .test_edicion_inventario import EdicionInventarioMixin
from .views import _precios_incluyen_impuesto


class EdicionLineasNormalizadasMixin:
    setUp = EdicionInventarioMixin.setUp
    producto_con_stock = EdicionInventarioMixin.producto_con_stock
    reponer = EdicionInventarioMixin.reponer
    datos = EdicionInventarioMixin.datos
    datos_linea = EdicionInventarioMixin.datos_linea
    editar = EdicionInventarioMixin.editar
    marca = EdicionInventarioMixin.marca
    saldo = EdicionInventarioMixin.saldo

    def recreadas(self, filas):
        datos = {key: value for key, value in self.datos().items() if not key.startswith("lineas-")}
        datos.update({
            "lineas-TOTAL_FORMS": str(len(filas)), "lineas-INITIAL_FORMS": "0",
            "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000",
        })
        for indice, (producto, cantidad) in enumerate(filas):
            datos.update(self.datos_linea(indice, producto, cantidad))
            datos.pop(f"lineas-{indice}-id", None)
        return datos

    def agregar_b(self):
        datos = self.datos(**{"lineas-TOTAL_FORMS": "2"})
        datos.update(self.datos_linea(1, self.otro, 2))
        self.editar(datos)
        return datos

    def movimientos_nuevos(self, antes):
        return list(MovimientoInventario.objects.filter(factura=self.factura).exclude(
            pk__in=[movimiento["id"] for movimiento in antes[0]],
        ))

    def test_recrear_y_dividir_producto_conserva_salida_original(self):
        antes = self.marca()
        id_original = self.linea.pk
        self.editar(self.recreadas([(self.producto, 2), (self.producto, 3)]))
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.count(), 2)
        self.assertTrue(self.factura.lineas.filter(pk=id_original).exists())
        self.assertEqual(sum(self.factura.lineas.values_list("cantidad", flat=True)), Decimal(5))
        self.assertEqual(sum(self.factura.lineas.values_list("costo_contable_total", flat=True)), Decimal(10))

    def test_recrear_y_unificar_producto_repetido_no_genera_movimientos(self):
        # La salida original de cinco unidades respalda estas dos líneas.
        LineaFactura.objects.filter(pk=self.linea.pk).update(cantidad=3, costo_contable_total=6)
        segunda = LineaFactura.objects.create(
            factura=self.factura, producto=self.producto, cantidad=2,
            precio_unitario=10, impuesto=self.exonerado, costo_contable_total=4,
        )
        ids_originales = {self.linea.pk, segunda.pk}
        antes = self.marca()
        self.editar(self.recreadas([(self.producto, 5)]))
        self.assertEqual(self.marca(), antes)
        linea = self.factura.lineas.get()
        self.assertIn(linea.pk, ids_originales)
        self.assertEqual((linea.cantidad, linea.costo_contable_total), (Decimal(5), Decimal(10)))

    def test_recrear_reemplazando_producto_devuelve_origen_y_despacha_destino(self):
        antes = self.marca()
        self.editar(self.recreadas([(self.otro, 4)]))
        self.saldo(5)
        self.saldo(0, self.otro)
        self.assertEqual(list(self.factura.lineas.values_list("producto_id", "cantidad")), [(self.otro.pk, Decimal(4))])
        nuevos = self.movimientos_nuevos(antes)
        self.assertEqual(
            {(m.producto_id, m.tipo, m.cantidad) for m in nuevos},
            {(self.producto.pk, "reversion_factura", Decimal(5)), (self.otro.pk, "salida_factura", Decimal(4))},
        )
        self.assertTrue(all(m.usuario_id == self.usuario.pk for m in nuevos))

    def test_producto_nuevo_primero_no_roba_identidad_de_producto_original_posterior(self):
        incluye_nuevo = _precios_incluyen_impuesto(self.empresa)
        incluye_original = not incluye_nuevo
        LineaFactura.objects.filter(pk=self.linea.pk).update(precio_incluye_impuesto=incluye_original)
        self.editar(self.recreadas([(self.otro, 2), (self.producto, 5)]))
        original = self.factura.lineas.get(producto=self.producto)
        agregado = self.factura.lineas.get(producto=self.otro)
        self.assertEqual(original.pk, self.linea.pk)
        self.assertEqual(original.precio_incluye_impuesto, incluye_original)
        self.assertNotEqual(agregado.pk, self.linea.pk)
        self.assertEqual(agregado.precio_incluye_impuesto, incluye_nuevo)
        self.saldo(0)
        self.saldo(2, self.otro)

    def test_recrear_omitiendo_producto_lo_devuelve_y_conserva_linea_restante(self):
        self.agregar_b()
        id_b = self.factura.lineas.get(producto=self.otro).pk
        antes = self.marca()
        self.editar(self.recreadas([(self.otro, 2)]))
        self.saldo(5)
        self.saldo(2, self.otro)
        self.assertEqual(self.factura.lineas.get().pk, id_b)
        self.assertEqual(
            [(m.producto_id, m.tipo, m.cantidad) for m in self.movimientos_nuevos(antes)],
            [(self.producto.pk, "reversion_factura", Decimal(5))],
        )

    def test_post_normal_que_omite_linea_existente_no_la_elimina(self):
        self.agregar_b()
        antes = self.marca()
        datos = self.datos(**{"lineas-TOTAL_FORMS": "1", "lineas-INITIAL_FORMS": "1"})
        datos = {key: value for key, value in datos.items() if not key.startswith("lineas-1-")}
        self.editar(datos)
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.count(), 2)
        self.saldo(2, self.otro)

    def test_formulario_actualizado_permite_agregar_producto_identico_de_nuevo(self):
        self.agregar_b()
        antes = self.marca()
        datos = self.datos(**{"lineas-TOTAL_FORMS": "3"})
        datos.update(self.datos_linea(2, self.otro, 2))
        self.editar(datos)
        self.saldo(0, self.otro)
        self.assertEqual(self.factura.lineas.filter(producto=self.otro).count(), 2)
        self.assertEqual(
            [(m.producto_id, m.tipo, m.cantidad) for m in self.movimientos_nuevos(antes)],
            [(self.otro.pk, "salida_factura", Decimal(2))],
        )

    def test_reintento_de_alta_con_precio_corregido_no_duplica_producto(self):
        datos = self.agregar_b()
        antes = self.marca()
        id_b = self.factura.lineas.get(producto=self.otro).pk
        datos["lineas-1-precio_unitario"] = "15"
        self.editar(datos)
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.count(), 2)
        linea = self.factura.lineas.get(producto=self.otro)
        self.assertEqual((linea.pk, linea.precio_unitario), (id_b, Decimal(15)))

    def test_id_de_otra_factura_no_puede_reutilizarse_en_filas_recreadas(self):
        ajena = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, estado="borrador", fecha_emision=self.hoy,
        )
        linea_ajena = LineaFactura.objects.create(
            factura=ajena, producto=self.otro, cantidad=1, precio_unitario=10, impuesto=self.exonerado,
        )
        datos = self.recreadas([(self.otro, 1)])
        datos["lineas-0-id"] = str(linea_ajena.pk)
        antes = self.marca()
        response = self.client.post(reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]), datos)
        self.assertIn(response.status_code, (200, 400, 403))
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.get().pk, self.linea.pk)
        linea_ajena.refresh_from_db()
        self.assertEqual((linea_ajena.factura_id, linea_ajena.cantidad), (ajena.pk, Decimal(1)))

    def test_id_repetido_no_desencadena_cambios_parciales(self):
        self.reponer(2)
        datos = self.recreadas([(self.producto, 1), (self.producto, 1)])
        datos.update({"lineas-0-id": str(self.linea.pk), "lineas-1-id": str(self.linea.pk)})
        antes = self.marca()
        response = self.client.post(reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]), datos)
        self.assertIn(response.status_code, (200, 400, 403))
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.get().cantidad, Decimal(5))

    def test_id_invalido_marcado_delete_no_se_convierte_en_borrado_de_otra_linea(self):
        for identificador in ("999999999", "identificador-invalido"):
            with self.subTest(identificador=identificador):
                datos = self.recreadas([(self.producto, 5)])
                datos.update({"lineas-0-id": identificador, "lineas-0-DELETE": "on"})
                antes = self.marca()
                response = self.client.post(
                    reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]), datos,
                )
                self.assertIn(response.status_code, (200, 400, 403))
                self.assertEqual(self.marca(), antes)
                self.assertEqual(self.factura.lineas.get().pk, self.linea.pk)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionLineasNormalizadasSimpleTests(EdicionLineasNormalizadasMixin, TestCase):
    modo = "simple"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionLineasNormalizadasFefoTests(EdicionLineasNormalizadasMixin, TestCase):
    modo = "fefo"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionLineasNormalizadasVitrinaTests(EdicionLineasNormalizadasMixin, TestCase):
    modo = "vitrina"
