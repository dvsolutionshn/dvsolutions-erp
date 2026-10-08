"""Desglose informativo de GA calculado por el motor de líneas de Facturación.

El documento conserva sus importes históricos. Este adaptador solo consulta
sus líneas y calcula en memoria con los impuestos actuales de sus productos.
"""

from decimal import Decimal

from facturacion.models import LineaFactura


def desglose_gasto_adicional(gasto):
    """Devuelve líneas y resumen sin crear una factura ni modificar el GA.

    Un impuesto ausente no equivale a exento: se conserva el importe original
    de esa línea y se marca el resumen fiscal como incompleto.
    """
    detalles = []
    subtotal = Decimal("0.00")
    impuestos = Decimal("0.00")
    total = Decimal("0.00")
    configurados = True
    for linea in gasto.lineas.select_related("producto__impuesto_predeterminado").all():
        impuesto = linea.producto.impuesto_predeterminado
        impuesto_configurado = bool(impuesto and impuesto.activo and impuesto.porcentaje is not None)
        detalle = {
            "linea": linea,
            "descripcion": linea.descripcion,
            "cantidad": linea.cantidad,
            "precio_unitario": linea.precio_unitario,
            "subtotal": linea.subtotal,
            "impuesto_monto": None,
            "impuesto_tasa": None,
            "impuesto_nombre": None,
            "impuesto_configurado": impuesto_configurado,
            "total_linea": linea.subtotal,
        }
        if not impuesto_configurado:
            configurados = False
        else:
            fiscal = LineaFactura(
                producto=linea.producto,
                cantidad=linea.cantidad,
                precio_unitario=linea.precio_unitario,
                precio_incluye_impuesto=gasto.precio_incluye_impuesto,
                impuesto=impuesto,
            )
            fiscal.calcular_importes()
            detalle.update({
                "subtotal": fiscal.subtotal,
                "impuesto_monto": fiscal.impuesto_monto,
                "impuesto_tasa": impuesto.porcentaje,
                "impuesto_nombre": impuesto.nombre,
                "total_linea": fiscal.total_linea,
            })
            subtotal += fiscal.subtotal
            impuestos += fiscal.impuesto_monto
            total += fiscal.total_linea
        detalles.append(detalle)

    total_historico = gasto.total
    total_coincide = configurados and total == total_historico
    nota = ""
    if not configurados:
        nota = (
            "Hay productos sin impuesto configurado o con impuesto inactivo; no se puede completar el desglose fiscal. "
            "El gasto conserva su importe original."
        )
    elif not total_coincide:
        nota = (
            "El gasto conserva su importe original. "
            "El desglose de impuestos es estimado según la configuración actual de los productos."
        )
    return {
        "lineas": detalles,
        "subtotal": subtotal if configurados else None,
        "impuestos": impuestos if configurados else None,
        "total": total if configurados else None,
        "total_historico": total_historico,
        "total_coincide": total_coincide,
        "impuestos_configurados": configurados,
        "nota_impuestos": nota,
    }
