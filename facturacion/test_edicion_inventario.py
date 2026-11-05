"""Una factura emitida ajusta inventario exclusivamente por la cantidad neta editada."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import AsientoContable
from contabilidad.services import registrar_asiento_factura_emitida
from core.models import (
    ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo,
    RegistroAuditoria, RolSistema, Usuario,
)
from .models import (
    CAI, BodegaInventario, Cliente, ExistenciaLoteBodega, Factura,
    InventarioProducto, LineaFactura, LoteInventario,
    MovimientoInventario, MovimientoLoteBodega, Producto, TipoImpuesto,
)
from .views import _registrar_salida_factura


class EdicionInventarioMixin:
    modo = "simple"

    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = Empresa.objects.create(
            nombre="Edición inventario", slug="hospital_mia" if self.modo == "fefo" else "edicion_inventario",
            rtn="08011999003342", estado_licencia="activa",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo)
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.ventas_solo_desde_vitrina = self.modo == "vitrina"
        configuracion.save(update_fields=["permite_cai_historico", "ventas_solo_desde_vitrina"])
        rol = RolSistema.objects.create(
            nombre="Editor de inventario", codigo="editor-inventario",
            puede_facturas=True, puede_ver_facturas=True, puede_editar_facturas=True,
            puede_cambiar_fecha_factura=True, puede_anular_facturas=True,
            puede_inventario=True,
        )
        self.usuario = Usuario.objects.create_user(username="editor-inventario", empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(self.usuario)
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente factura 3342", rtn="0801199912345")
        self.exonerado = TipoImpuesto.objects.create(nombre="Exonerado edición", porcentaje=0)
        self.exento = TipoImpuesto.objects.create(nombre="Exento edición", porcentaje=0)
        CAI.objects.create(
            empresa=self.empresa, numero_cai="CAI-EDICION-INVENTARIO", uso_documento="factura",
            establecimiento="000", punto_emision="001", tipo_documento="01",
            rango_inicial=1, rango_final=9999, correlativo_actual=3341,
            fecha_activacion=self.hoy - timedelta(days=30), fecha_limite=self.hoy + timedelta(days=365),
        )
        self.bodega = None
        if self.modo != "simple":
            self.bodega = BodegaInventario.objects.create(
                empresa=self.empresa, nombre="Vitrina" if self.modo == "vitrina" else "Bodega General",
                tipo="vitrina" if self.modo == "vitrina" else "principal",
            )
        self.producto = self.producto_con_stock("A", 5)
        self.otro = self.producto_con_stock("B", 4)
        self.factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, estado="emitida", fecha_emision=self.hoy,
            numero_factura="000-001-01-00003342", vendedor=self.usuario,
        )
        self.linea = LineaFactura.objects.create(
            factura=self.factura, producto=self.producto, cantidad=5,
            precio_unitario=10, impuesto=self.exonerado,
        )
        self.factura.calcular_totales()
        self.factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        _registrar_salida_factura(self.factura, usuario=self.usuario)
        self.linea.refresh_from_db()

    def producto_con_stock(self, nombre, cantidad):
        producto = Producto.objects.create(
            empresa=self.empresa, nombre=f"Producto {nombre}", codigo=nombre,
            controla_inventario=True, precio=10, costo_promedio=2,
            impuesto_predeterminado=self.exonerado,
        )
        InventarioProducto.objects.create(
            empresa=self.empresa, producto=producto, existencias=cantidad,
            valor_contable=Decimal(cantidad) * 2,
        )
        if self.bodega:
            for indice, unidades in enumerate((3, cantidad - 3), 1):
                if unidades <= 0:
                    continue
                lote = LoteInventario.objects.create(
                    empresa=self.empresa, producto=producto, numero_lote=f"{nombre}-{indice}",
                    fecha_vencimiento=self.hoy + timedelta(days=60 * indice),
                )
                ExistenciaLoteBodega.objects.create(
                    empresa=self.empresa, bodega=self.bodega, lote=lote, cantidad=unidades,
                )
        return producto

    def reponer(self, cantidad=2):
        inventario = InventarioProducto.objects.get(producto=self.producto)
        inventario.existencias += Decimal(cantidad)
        inventario.valor_contable = (inventario.valor_contable or Decimal(0)) + Decimal(cantidad) * 2
        inventario.save(update_fields=["existencias", "valor_contable"])
        Producto.objects.filter(pk=self.producto.pk).update(costo_promedio=2)
        if self.bodega:
            lote = LoteInventario.objects.create(
                empresa=self.empresa, producto=self.producto, numero_lote="A-ADICIONAL",
                fecha_vencimiento=self.hoy + timedelta(days=5),
            )
            ExistenciaLoteBodega.objects.create(
                empresa=self.empresa, bodega=self.bodega, lote=lote, cantidad=cantidad,
            )
            return lote

    def datos(self, **cambios):
        lineas = list(self.factura.lineas.order_by("pk"))
        datos = {
            "cliente": str(self.cliente.pk), "fecha_emision": str(self.hoy), "fecha_vencimiento": "",
            "vendedor": str(self.usuario.pk), "tipo_cambio": "1.0000", "moneda": "HNL", "estado": "emitida",
            "numero_factura": self.factura.numero_factura, "numero_factura_sufijo": "342",
            "orden_compra_exenta": "", "registro_exonerado": "", "registro_sag": "",
            "motivo_auditoria": "Corrección comprobada de la factura de inventario",
            "lineas-TOTAL_FORMS": str(len(lineas)), "lineas-INITIAL_FORMS": str(len(lineas)),
            "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000",
        }
        for indice, linea in enumerate(lineas):
            datos.update(self.datos_linea(indice, linea.producto, linea.cantidad, linea=linea))
        datos.update(cambios)
        return datos

    def datos_linea(self, indice, producto, cantidad, linea=None):
        campos = {
            "id": str(linea.pk) if linea else "", "producto": str(producto.pk), "descripcion_manual": "",
            "cantidad": str(cantidad), "precio_unitario": str(linea.precio_unitario if linea else 10),
            "descuento_porcentaje": str(linea.descuento_porcentaje if linea else 0),
            "comentario": "", "impuesto": str(linea.impuesto_id if linea else self.exento.pk),
        }
        return {f"lineas-{indice}-{campo}": valor for campo, valor in campos.items()}

    def editar(self, datos=None, app=False, **cambios):
        url = reverse("editar_factura", args=[self.empresa.slug, self.factura.pk])
        datos = datos if datos is not None else self.datos(**cambios)
        if app:
            url += "?app=1"
            datos["app"] = "1"
        response = self.client.post(url, datos)
        errores = ""
        if response.status_code == 200:
            errores = f"{response.context['form'].errors} / {response.context['formset'].errors}"
        self.assertEqual(response.status_code, 302, errores)
        self.factura.refresh_from_db()
        return response

    def marca(self):
        return (
            list(MovimientoInventario.objects.filter(factura=self.factura).order_by("pk").values()),
            list(MovimientoLoteBodega.objects.filter(factura=self.factura).order_by("pk").values()),
            list(InventarioProducto.objects.filter(empresa=self.empresa).order_by("pk").values()),
            list(ExistenciaLoteBodega.objects.filter(empresa=self.empresa).order_by("pk").values()),
        )

    def saldo(self, cantidad, producto=None):
        inventario = InventarioProducto.objects.get(producto=producto or self.producto)
        self.assertEqual(inventario.existencias, Decimal(str(cantidad)))
        if self.bodega:
            self.assertEqual(
                sum(ExistenciaLoteBodega.objects.filter(lote__producto=producto or self.producto).values_list("cantidad", flat=True)),
                Decimal(str(cantidad)),
            )

    def nuevos(self, antes, tipo, cantidad):
        ids = [movimiento["id"] for movimiento in antes[0]]
        movimientos = list(MovimientoInventario.objects.filter(factura=self.factura).exclude(pk__in=ids))
        self.assertEqual([(m.tipo, m.cantidad) for m in movimientos], [(tipo, Decimal(str(cantidad)))])
        self.assertEqual(movimientos[0].usuario_id, self.usuario.pk)
        self.assertEqual(movimientos[0].empresa_id, self.empresa.pk)
        self.assertEqual(movimientos[0].referencia, "000-001-01-00003342")
        if self.bodega:
            ids_lote = [movimiento["id"] for movimiento in antes[1]]
            lotes = list(MovimientoLoteBodega.objects.filter(factura=self.factura).exclude(pk__in=ids_lote))
            self.assertEqual(sum(m.cantidad for m in lotes), Decimal(str(cantidad)))
            self.assertTrue(all(m.usuario_id == self.usuario.pk and m.bodega_id == self.bodega.pk for m in lotes))
        return movimientos

    def test_exonerado_a_exento_sin_stock_no_cambia_inventario_ni_kardex(self):
        self.saldo(0)
        antes = self.marca()
        self.editar(**{"lineas-0-impuesto": str(self.exento.pk), "orden_compra_exenta": "OC-3342"})
        self.assertEqual(self.marca(), antes)
        self.assertEqual(self.factura.lineas.get().impuesto_id, self.exento.pk)
        auditoria = RegistroAuditoria.objects.filter(
            empresa=self.empresa, modelo="factura", objeto_id=str(self.factura.pk),
            cambios__accion_factura__nuevo="editar",
        ).latest("fecha")
        self.assertEqual(auditoria.usuario_id, self.usuario.pk)
        self.assertEqual(auditoria.cambios["factura"]["anterior"]["lineas"][0]["cantidad"], "5.00")
        self.assertEqual(auditoria.cambios["factura"]["nuevo"]["lineas"][0]["cantidad"], "5.00")
        kardex = self.client.get(
            reverse("kardex_inventario", args=[self.empresa.slug]),
            {"producto": str(self.producto.pk)},
        )
        self.assertEqual(kardex.status_code, 200)
        self.assertEqual(kardex.context["resumen"]["salidas"], 1)
        self.assertEqual(kardex.context["resumen"]["entradas"], 0)
        self.assertEqual(len(kardex.context["movimientos"]), 1)

    def test_precio_descuento_datos_fiscales_y_app_no_generan_movimientos(self):
        antes = self.marca()
        self.editar(app=True, **{
            "lineas-0-precio_unitario": "15", "lineas-0-descuento_porcentaje": "10",
            "registro_exonerado": "REG-3342", "registro_sag": "SAG-3342",
        })
        self.assertEqual(self.factura.total, Decimal("67.50"))
        self.assertEqual(self.marca(), antes)

    def test_aumentar_dos_valida_y_descuenta_solo_dos_y_reintento_no_duplica(self):
        lote_nuevo = self.reponer()
        antes = self.marca()
        self.editar(**{"lineas-0-cantidad": "7"})
        self.saldo(0)
        self.nuevos(antes, "salida_factura", 2)
        if lote_nuevo:
            self.assertEqual(MovimientoLoteBodega.objects.filter(factura=self.factura).latest("pk").lote_id, lote_nuevo.pk)
        despues = self.marca()
        self.editar()
        self.assertEqual(self.marca(), despues)

    def test_disminuir_dos_devuelve_unicamente_dos_a_lotes_originales(self):
        antes = self.marca()
        self.editar(**{"lineas-0-cantidad": "3"})
        self.saldo(2)
        self.nuevos(antes, "reversion_factura", 2)
        if self.bodega:
            devolucion = MovimientoLoteBodega.objects.filter(factura=self.factura, tipo="reversion").get()
            self.assertEqual(devolucion.lote.numero_lote, "A-2")

    def test_eliminar_linea_devuelve_toda_la_cantidad_original(self):
        servicio = Producto.objects.create(
            empresa=self.empresa, nombre="Servicio conservado", controla_inventario=False,
            tipo_item="servicio", precio=10, impuesto_predeterminado=self.exento,
        )
        LineaFactura.objects.create(
            factura=self.factura, producto=servicio, cantidad=1, precio_unitario=10, impuesto=self.exento,
        )
        self.factura.calcular_totales()
        self.factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        antes = self.marca()
        self.editar(**{"lineas-0-DELETE": "on"})
        self.assertEqual(self.factura.lineas.get().producto_id, servicio.pk)
        self.saldo(5)
        self.nuevos(antes, "reversion_factura", 5)
        if self.bodega:
            self.assertEqual(
                list(MovimientoLoteBodega.objects.filter(factura=self.factura, tipo="reversion").order_by("lote__numero_lote").values_list("lote__numero_lote", "cantidad")),
                [("A-1", Decimal(3)), ("A-2", Decimal(2))],
            )

    def test_agregar_linea_valida_y_descontar_solo_producto_nuevo(self):
        datos = self.datos(**{"lineas-TOTAL_FORMS": "2"})
        datos.update(self.datos_linea(1, self.otro, 2))
        antes = self.marca()
        self.editar(datos)
        self.saldo(0)
        self.saldo(2, self.otro)
        self.assertEqual(self.nuevos(antes, "salida_factura", 2)[0].producto_id, self.otro.pk)

    def test_eliminar_unica_linea_devuelve_stock_y_repetir_no_crea_movimientos_ni_asiento(self):
        registrar_asiento_factura_emitida(self.factura)
        asientos = AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="factura", documento_id=self.factura.pk, evento="emision",
        )
        self.assertTrue(asientos.exists())
        antes = self.marca()
        self.editar(**{"lineas-0-DELETE": "on"})
        self.saldo(5)
        self.nuevos(antes, "reversion_factura", 5)
        self.assertFalse(self.factura.lineas.exists())
        self.assertEqual(self.factura.total, Decimal(0))
        self.assertFalse(asientos.exists())
        self.assertEqual(MovimientoInventario.objects.filter(factura=self.factura, tipo="salida_factura").count(), 1)
        despues = self.marca()
        self.editar()
        self.assertEqual(self.marca(), despues)
        self.assertFalse(asientos.exists())

    def test_reemplazar_producto_devuelve_anterior_y_descontar_nuevo(self):
        antes = self.marca()
        self.editar(**{"lineas-0-producto": str(self.otro.pk), "lineas-0-cantidad": "4"})
        self.saldo(5)
        self.saldo(0, self.otro)
        movimientos = list(MovimientoInventario.objects.filter(factura=self.factura).exclude(pk__in=[m["id"] for m in antes[0]]))
        self.assertEqual(
            {(m.producto_id, m.tipo, m.cantidad) for m in movimientos},
            {(self.producto.pk, "reversion_factura", Decimal(5)), (self.otro.pk, "salida_factura", Decimal(4))},
        )
        self.assertTrue(all(m.usuario_id == self.usuario.pk for m in movimientos))

    def test_aumento_sin_stock_rechaza_y_revierte_toda_la_edicion(self):
        antes = self.marca()
        response = self.client.post(
            reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]),
            self.datos(**{"lineas-0-cantidad": "7", "registro_sag": "NO-GUARDAR"}),
        )
        self.assertEqual(response.status_code, 200)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.lineas.get().cantidad, Decimal(5))
        self.assertNotEqual(self.factura.registro_sag, "NO-GUARDAR")
        self.assertEqual(self.marca(), antes)

    def test_reemplazo_sin_stock_no_devuelve_el_original_ni_guarda_producto(self):
        antes = self.marca()
        response = self.client.post(
            reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]),
            self.datos(**{"lineas-0-producto": str(self.otro.pk), "lineas-0-cantidad": "5"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.factura.lineas.get().producto_id, self.producto.pk)
        self.assertEqual(self.marca(), antes)

    def test_dividir_producto_en_dos_lineas_no_genera_salida(self):
        antes = self.marca()
        datos = self.datos(**{"lineas-0-cantidad": "3", "lineas-TOTAL_FORMS": "2"})
        datos.update(self.datos_linea(1, self.producto, 2))
        self.editar(datos)
        self.assertEqual(self.factura.lineas.count(), 2)
        self.assertEqual(self.marca(), antes)

    def test_anular_despues_de_aumento_y_disminucion_devuelve_solo_saldo_neto(self):
        self.reponer()
        self.editar(**{"lineas-0-cantidad": "7"})
        self.editar(**{"lineas-0-cantidad": "3"})
        self.saldo(4)
        response = self.client.post(
            reverse("anular_factura", args=[self.empresa.slug, self.factura.pk]),
            {"motivo": "Anulación comprobada después de ajustar cantidades"},
        )
        self.assertEqual(response.status_code, 302)
        self.saldo(7)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "anulada")
        despues = self.marca()
        self.client.post(
            reverse("anular_factura", args=[self.empresa.slug, self.factura.pk]),
            {"motivo": "Anulación comprobada después de ajustar cantidades"},
        )
        self.assertEqual(self.marca(), despues)

    def test_nuevo_lote_fefo_no_cambia_asignacion_en_edicion_sin_cantidad(self):
        lote_nuevo = self.reponer()
        antes = self.marca()
        self.editar(**{"lineas-0-impuesto": str(self.exento.pk)})
        self.assertEqual(self.marca(), antes)
        if lote_nuevo:
            self.assertFalse(MovimientoLoteBodega.objects.filter(factura=self.factura, lote=lote_nuevo).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionInventarioSimpleTests(EdicionInventarioMixin, TestCase):
    pass


class EdicionInventarioLotesMixin:
    def test_lote_original_vencido_inactivo_conserva_asignacion_y_admite_devolucion(self):
        LoteInventario.objects.filter(producto=self.producto).update(
            activo=False, fecha_vencimiento=self.hoy - timedelta(days=1),
        )
        antes = self.marca()
        with patch("facturacion.views._validar_stock_disponible_para_lineas") as validar_general, patch(
            "facturacion.views._validar_stock_vitrina_para_lineas",
        ) as validar_lotes:
            self.editar(**{"lineas-0-impuesto": str(self.exento.pk)})
            self.assertEqual(self.marca(), antes)
            self.editar(**{"lineas-0-precio_unitario": "12"})
            self.assertEqual(self.marca(), antes)
            self.editar(**{"lineas-0-cantidad": "3"})
            validar_general.assert_not_called()
            validar_lotes.assert_not_called()
        self.saldo(2)
        self.nuevos(antes, "reversion_factura", 2)
        devolucion = MovimientoLoteBodega.objects.get(factura=self.factura, tipo="reversion")
        self.assertEqual(devolucion.lote.numero_lote, "A-2")
        self.assertFalse(devolucion.lote.activo)
        self.assertLess(devolucion.lote.fecha_vencimiento, self.hoy)

    def test_sin_traza_lotes_permite_edicion_fiscal_pero_rechaza_devolucion_incompleta(self):
        MovimientoLoteBodega.objects.filter(factura=self.factura).delete()
        self.assertTrue(MovimientoInventario.objects.filter(factura=self.factura, tipo="salida_factura").exists())
        antes = self.marca()
        with patch("facturacion.views._validar_stock_disponible_para_lineas") as validar_general, patch(
            "facturacion.views._validar_stock_vitrina_para_lineas",
        ) as validar_lotes:
            self.editar(**{"lineas-0-impuesto": str(self.exento.pk), "orden_compra_exenta": "OC-SIN-TRAZA"})
            self.assertEqual(self.marca(), antes)
            self.editar(**{"lineas-0-precio_unitario": "12"})
            self.assertEqual(self.marca(), antes)
            validar_general.assert_not_called()
            validar_lotes.assert_not_called()
        response = self.client.post(
            reverse("editar_factura", args=[self.empresa.slug, self.factura.pk]),
            self.datos(**{"lineas-0-cantidad": "3", "registro_sag": "NO-GUARDAR-SIN-TRAZA"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lotes originales")
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.lineas.get().cantidad, Decimal(5))
        self.assertEqual(self.factura.lineas.get().precio_unitario, Decimal(12))
        self.assertNotEqual(self.factura.registro_sag, "NO-GUARDAR-SIN-TRAZA")
        self.assertEqual(self.marca(), antes)
        self.saldo(0)
        self.assertFalse(MovimientoInventario.objects.filter(factura=self.factura, tipo="reversion_factura").exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionInventarioFefoTests(EdicionInventarioLotesMixin, EdicionInventarioMixin, TestCase):
    modo = "fefo"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EdicionInventarioVitrinaTests(EdicionInventarioLotesMixin, EdicionInventarioMixin, TestCase):
    modo = "vitrina"
