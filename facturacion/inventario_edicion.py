"""Ajustes de facturas emitidas sin reconstruir sus salidas originales."""

from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction

from contabilidad.models import bloquear_empresas_contables, validar_fecha_contable_abierta
from core.models import ConfiguracionAvanzadaEmpresa

from .costos_inventario import valor_actual_inventario, valor_contable_linea_inventario
from .models import LineaFactura, MovimientoInventario, MovimientoLoteBodega, Producto


CERO = Decimal("0.00")
CENTAVO = Decimal("0.01")


def _agrupar_lineas(lineas, *, validar_cantidades=False):
    grupos = {}
    for linea in lineas:
        if not linea.producto_id or not linea.producto.controla_inventario:
            continue
        if validar_cantidades and linea.cantidad <= 0:
            raise ValidationError(f"La cantidad de {linea.producto.nombre} debe ser mayor que cero; elimina la linea para devolverla completamente.")
        grupo = grupos.setdefault(linea.producto_id, {
            "producto": linea.producto, "cantidad": CERO, "valor": CERO, "lineas": [],
        })
        grupo["cantidad"] += linea.cantidad
        grupo["valor"] += valor_contable_linea_inventario(linea)
        grupo["lineas"].append(linea)
    return grupos


def _salidas_activas(movimientos, *, clave, tipo_salida):
    """Reconstruye asignaciones pendientes, descontando cada reversión previa.

    Se libera primero la última asignación: las unidades que permanecen vendidas
    conservan sus lotes originales, incluso después de varias ediciones.
    """
    pilas = {}
    for movimiento in movimientos:
        pila = pilas.setdefault(clave(movimiento), [])
        if movimiento.tipo == tipo_salida:
            valor = getattr(movimiento, "valor_contable", None)
            pila.append({"movimiento": movimiento, "cantidad": movimiento.cantidad, "valor": valor})
            continue
        pendiente = movimiento.cantidad
        while pendiente > 0 and pila:
            asignacion = pila[-1]
            cantidad = min(pendiente, asignacion["cantidad"])
            if asignacion["valor"] is not None:
                valor = (asignacion["valor"] * cantidad / asignacion["cantidad"]).quantize(CENTAVO, rounding=ROUND_HALF_UP)
                asignacion["valor"] -= valor
            asignacion["cantidad"] -= cantidad
            pendiente -= cantidad
            if asignacion["cantidad"] == 0:
                pila.pop()
    return sorted(
        (asignacion for pila in pilas.values() for asignacion in pila),
        key=lambda asignacion: asignacion["movimiento"].pk,
        reverse=True,
    )


def _planificar_devolucion(factura, grupo, cantidad):
    from . import views

    producto = grupo["producto"]
    salidas = _salidas_activas(
        MovimientoInventario.objects.filter(
            empresa_id=factura.empresa_id, factura=factura, producto=producto,
            tipo__in=["salida_factura", "reversion_factura"],
        ).order_by("pk"),
        clave=lambda movimiento: movimiento.producto_id,
        tipo_salida="salida_factura",
    )
    # Los movimientos anteriores a la captura monetaria tienen valor nulo.
    # Su costo pendiente se obtiene del costo congelado de la factura, restando
    # las salidas recientes valoradas; no se usa el promedio mezclado actual.
    historicas = [asignacion for asignacion in salidas if asignacion["valor"] is None]
    if historicas:
        valor_conocido = sum((asignacion["valor"] for asignacion in salidas if asignacion["valor"] is not None), CERO)
        valor_historico = grupo["valor"] - valor_conocido
        if valor_historico < 0:
            raise ValidationError(f"El costo original de {producto.nombre} no coincide con sus salidas; revisa su valoracion historica.")
        cantidad_historica = sum((asignacion["cantidad"] for asignacion in historicas), CERO)
        acumulado, repartido = CERO, CERO
        for asignacion in historicas:
            acumulado += asignacion["cantidad"]
            objetivo = (valor_historico * acumulado / cantidad_historica).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            asignacion["valor"] = objetivo - repartido
            repartido = objetivo
    pendiente, valor = cantidad, CERO
    generales = []
    for asignacion in salidas:
        if pendiente <= 0:
            break
        devolver = min(pendiente, asignacion["cantidad"])
        importe = (asignacion["valor"] * devolver / asignacion["cantidad"]).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        generales.append((asignacion["movimiento"], devolver, importe))
        valor += importe
        pendiente -= devolver
    if pendiente > 0:
        raise ValidationError(f"No se identifica la salida original suficiente de {producto.nombre} para devolver {cantidad} unidades.")

    movimientos_lote = list(MovimientoLoteBodega.objects.filter(
        empresa_id=factura.empresa_id, factura=factura, lote__producto=producto,
        tipo__in=["salida_factura", "reversion"],
    ).select_related("bodega", "lote").order_by("pk"))
    lotes = []
    requiere_lotes = views._empresa_usa_control_lotes_fefo(factura.empresa) or ConfiguracionAvanzadaEmpresa.para_empresa(factura.empresa).ventas_solo_desde_vitrina
    if movimientos_lote or requiere_lotes:
        pendiente = cantidad
        for asignacion in _salidas_activas(
            movimientos_lote, clave=lambda movimiento: (movimiento.bodega_id, movimiento.lote_id),
            tipo_salida="salida_factura",
        ):
            if pendiente <= 0:
                break
            devolver = min(pendiente, asignacion["cantidad"])
            lotes.append((asignacion["movimiento"], devolver))
            pendiente -= devolver
        if pendiente > 0:
            raise ValidationError(f"No se identifican los lotes originales suficientes de {producto.nombre} para la devolución.")
    return {"producto": producto, "cantidad": cantidad, "valor": valor, "generales": generales, "lotes": lotes}


def _repartir_valor_lineas(grupo, valor):
    """Conserva el costo vendido, incluido el remanente exacto al centavo."""
    acumulado, repartido = CERO, CERO
    for linea in grupo["lineas"]:
        acumulado += linea.cantidad
        objetivo = (valor * acumulado / grupo["cantidad"]).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        linea.costo_contable_total = objetivo - repartido
        linea.costo_unitario = (valor / grupo["cantidad"]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        LineaFactura.objects.filter(pk=linea.pk, factura_id=linea.factura_id).update(
            costo_contable_total=linea.costo_contable_total, costo_unitario=linea.costo_unitario,
        )
        repartido = objetivo


def planificar_diferencias_inventario(lineas_originales, lineas_editadas):
    """Compara las dos colecciones sin escribir líneas ni inventario."""
    originales = _agrupar_lineas(lineas_originales)
    actuales = _agrupar_lineas(lineas_editadas, validar_cantidades=True)
    diferencias = {
        producto_id: actuales.get(producto_id, {}).get("cantidad", CERO) - originales.get(producto_id, {}).get("cantidad", CERO)
        for producto_id in originales.keys() | actuales.keys()
    }
    cambios = {producto_id: cantidad for producto_id, cantidad in diferencias.items() if cantidad != 0}
    return {"originales": originales, "actuales": actuales, "cambios": cambios}


@transaction.atomic
def ajustar_inventario_factura_editada(factura, lineas_originales, *, usuario=None, plan=None):
    # Importación diferida: utiliza las mismas operaciones que emisión/anulación.
    from . import views

    if plan is None:
        plan = planificar_diferencias_inventario(
            lineas_originales, factura.lineas.select_related("producto").order_by("pk"),
        )
    originales, actuales, cambios = (plan[campo] for campo in ("originales", "actuales", "cambios"))
    if cambios:
        bloquear_empresas_contables([factura.empresa_id])
        validar_fecha_contable_abierta(factura.empresa_id, factura.fecha_emision)
        # La misma cerradura de producto utilizada por las demás operaciones.
        list(Producto.objects.select_for_update().filter(
            empresa_id=factura.empresa_id, pk__in=cambios,
        ).order_by("pk"))

    adicionales = {
        producto_id: {"producto": actuales[producto_id]["producto"], "cantidad": cantidad}
        for producto_id, cantidad in cambios.items() if cantidad > 0
    }
    # Sólo los incrementos participan en las validaciones general y por lotes.
    if adicionales:
        views._validar_stock_disponible_para_lineas([
            SimpleNamespace(**item) for item in adicionales.values()
        ])

    devoluciones = {
        producto_id: _planificar_devolucion(factura, originales[producto_id], -cantidad)
        for producto_id, cantidad in cambios.items() if cantidad < 0
    }
    referencia = factura.numero_factura or f"Factura {factura.pk}"
    valores = {producto_id: grupo["valor"] for producto_id, grupo in originales.items()}
    for producto_id, plan in devoluciones.items():
        observacion = "Devolucion de diferencia por edicion de factura emitida."
        for original, cantidad in plan["lotes"]:
            views._registrar_movimiento_lote_bodega(
                empresa=factura.empresa, bodega=original.bodega, lote=original.lote,
                tipo="reversion", cantidad=cantidad, referencia=referencia,
                observacion=f"{observacion} Salida original #{original.pk}.", factura=factura, usuario=usuario,
            )
        for original, cantidad, valor in plan["generales"]:
            views._registrar_movimiento_inventario(
                empresa=factura.empresa, producto=plan["producto"], bodega=original.bodega,
                tipo="reversion_factura", cantidad=cantidad, valor_contable=valor,
                referencia=referencia, observacion=f"{observacion} Salida original #{original.pk}.",
                factura=factura, usuario=usuario,
            )
        valores[producto_id] -= plan["valor"]

    if adicionales:
        observacion = "Salida adicional por diferencia en edicion de factura emitida."
        if not views._registrar_salida_fefo_factura(
            factura, forzar=True, observacion=observacion, usuario=usuario, cantidades_por_producto=adicionales,
        ):
            views._registrar_salida_vitrina_factura(
                factura, forzar=True, observacion=observacion, usuario=usuario, cantidades_por_producto=adicionales,
            )
        for producto_id, item in adicionales.items():
            producto = item["producto"]
            producto.refresh_from_db(fields=["costo_promedio"])
            inventario = views._obtener_inventario_producto(producto)
            costo = valor_actual_inventario(inventario, producto) / inventario.existencias
            valor = (costo * item["cantidad"]).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            views._registrar_movimiento_inventario(
                empresa=factura.empresa, producto=producto, tipo="salida_factura",
                cantidad=item["cantidad"], valor_contable=valor, referencia=referencia,
                observacion=observacion, factura=factura, usuario=usuario,
            )
            valores[producto_id] = valores.get(producto_id, CERO) + valor

    for producto_id, grupo in actuales.items():
        firma_original = {linea.pk: linea.cantidad for linea in originales.get(producto_id, {}).get("lineas", [])}
        firma_actual = {linea.pk: linea.cantidad for linea in grupo["lineas"]}
        if firma_original != firma_actual:
            _repartir_valor_lineas(grupo, valores.get(producto_id, CERO))
