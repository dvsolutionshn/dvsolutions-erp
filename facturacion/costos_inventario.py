"""Valoración común del inventario y sus asientos, en lempiras por empresa."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum


CENTAVO = Decimal("0.01")
PRECISION_COSTO = Decimal("0.0001")


def valor_contable_linea_inventario(linea):
    if getattr(linea, 'costo_contable_total', None) is not None:
        return Decimal(linea.costo_contable_total)
    return (Decimal(linea.cantidad or 0) * Decimal(linea.costo_unitario or 0)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def valor_actual_inventario(inventario, producto):
    """Usa el saldo exacto sólo mientras coincida con cantidades y promedio vigentes."""
    cantidad = Decimal(inventario.existencias or 0)
    costo = Decimal(producto.costo_promedio or 0)
    valor = inventario.valor_contable
    if valor is not None and (
        (cantidad == 0 and valor == 0)
        or (cantidad != 0 and (Decimal(valor) / cantidad).quantize(PRECISION_COSTO, rounding=ROUND_HALF_UP) == costo)
    ):
        return Decimal(valor)
    return (cantidad * costo).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def isv_recuperable_para_empresa(empresa_id):
    from contabilidad.models import ConfiguracionContableEmpresa

    return ConfiguracionContableEmpresa.objects.filter(
        empresa_id=empresa_id, isv_compras_recuperable=True
    ).exists()


def valorar_linea_compra(linea, *, capturar=False):
    """Congela el descuento y la política fiscal cuando se aplica la compra.

    Un cambio posterior en la configuración no altera una compra ya valorada.
    Los registros históricos conservan sus campos nulos hasta una operación nueva.
    """
    if linea.producto.empresa_id != linea.compra.empresa_id:
        raise ValidationError("El producto comprado debe pertenecer a la misma empresa de la compra.")
    cantidad = Decimal(linea.cantidad or 0)
    if cantidad <= 0:
        raise ValidationError("La cantidad comprada debe ser mayor que cero.")
    if linea.costo_contable_total is not None:
        recuperable = bool(linea.isv_recuperable_capturado)
        costo = Decimal(linea.costo_contable_total)
    else:
        recuperable = isv_recuperable_para_empresa(linea.compra.empresa_id)
        costo = Decimal(linea.subtotal or 0)
        if not recuperable:
            costo += Decimal(linea.impuesto_monto or 0)
        costo = costo.quantize(CENTAVO, rounding=ROUND_HALF_UP)
        if capturar:
            linea.costo_contable_total = costo
            linea.isv_recuperable_capturado = recuperable
            linea.save(update_fields=["costo_contable_total", "isv_recuperable_capturado"])
    return {
        "costo_total": costo,
        "costo_unitario": (costo / cantidad).quantize(PRECISION_COSTO, rounding=ROUND_HALF_UP),
        "isv_acreditable": Decimal(linea.impuesto_monto or 0) if recuperable else Decimal("0.00"),
    }


def costo_reversion_compra(linea):
    if linea.costo_contable_total is not None:
        return Decimal(linea.costo_contable_total)
    # Antes de la captura contable el promedio se actualizaba con el costo bruto,
    # sin descuentos ni impuesto. Se revierte ese criterio sin reescribir historia.
    return (Decimal(linea.cantidad or 0) * Decimal(linea.costo_unitario or 0)).quantize(CENTAVO)


def actualizar_valor_inventario(producto, cantidad_anterior, cantidad, valor, *, entrada, permitir_saldo_negativo=False, valor_anterior=None):
    """Aplica al promedio el mismo valor que la partida contable del movimiento."""
    from .models import Producto

    guardado = Producto.objects.select_for_update().get(pk=producto.pk, empresa_id=producto.empresa_id)
    cantidad_anterior = Decimal(cantidad_anterior or 0)
    cantidad = Decimal(cantidad or 0)
    valor = Decimal(valor or 0)
    signo = Decimal("1") if entrada else Decimal("-1")
    cantidad_nueva = cantidad_anterior + signo * cantidad
    if valor_anterior is None:
        valor_anterior = (cantidad_anterior * Decimal(guardado.costo_promedio or 0)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    valor_anterior = Decimal(valor_anterior)
    valor_nuevo = valor_anterior + signo * valor
    if permitir_saldo_negativo and cantidad_nueva <= 0:
        # Conserva la política operativa existente de ventas sin stock. El costo
        # queda capturado en la venta y no convierte el siguiente ingreso en cero.
        producto.costo_promedio = guardado.costo_promedio
        return valor_nuevo
    if cantidad_nueva < 0 or valor_nuevo < Decimal("-0.01"):
        raise ValidationError("La reversión dejaría inventario o valor negativo; registre un ajuste por la mercadería ya consumida.")
    promedio = (
        max(valor_nuevo, Decimal("0.00")) / cantidad_nueva
        if cantidad_nueva > 0 else Decimal("0.00")
    ).quantize(PRECISION_COSTO, rounding=ROUND_HALF_UP)
    Producto.objects.filter(pk=guardado.pk, empresa_id=guardado.empresa_id).update(costo_promedio=promedio)
    producto.costo_promedio = promedio
    return valor_nuevo


def resolver_linea_factura_origen(linea):
    """Encuentra un origen inequívoco; nunca sustituye el costo por el promedio actual."""
    if not linea.nota_credito_id or not linea.nota_credito.factura_origen_id:
        return None
    factura = linea.nota_credito.factura_origen
    if factura.empresa_id != linea.nota_credito.empresa_id:
        raise ValidationError("La factura origen debe pertenecer a la misma empresa de la nota de crédito.")
    if linea.linea_factura_origen_id:
        origen = linea.linea_factura_origen
        if origen.factura_id != factura.pk or origen.factura.empresa_id != linea.nota_credito.empresa_id:
            raise ValidationError({"linea_factura_origen": "La línea origen debe pertenecer a la factura y empresa de esta nota de crédito."})
        if origen.producto_id != linea.producto_id:
            raise ValidationError({"producto": "El producto devuelto debe coincidir con la línea original de la factura."})
        return origen
    candidatas = factura.lineas.filter(producto_id=linea.producto_id)
    if not linea.producto_id:
        candidatas = candidatas.filter(descripcion_manual=linea.descripcion_manual)
    if candidatas.count() > 1:
        exactas = candidatas.filter(precio_unitario=linea.precio_unitario, impuesto_id=linea.impuesto_id)
        if exactas.count() == 1:
            candidatas = exactas
    if candidatas.count() == 1:
        origen = candidatas.first()
        linea.linea_factura_origen = origen
        return origen
    if linea.producto_id and linea.producto.controla_inventario:
        if candidatas.exists():
            raise ValidationError({"linea_factura_origen": "Selecciona la línea original de la factura para devolver este producto; aparece en más de una línea."})
        raise ValidationError({"producto": "No se puede devolver inventario que no figura en la factura original."})
    return None


def validar_cantidad_devolucion(linea):
    from .models import LineaNotaCredito

    cantidad = Decimal(linea.cantidad or 0)
    if cantidad <= 0:
        raise ValidationError({"cantidad": "La cantidad acreditada debe ser mayor que cero."})
    origen = resolver_linea_factura_origen(linea)
    if origen is None:
        return
    otras = LineaNotaCredito.objects.filter(
        nota_credito__empresa_id=linea.nota_credito.empresa_id,
        nota_credito__factura_origen_id=origen.factura_id,
    ).filter(Q(nota_credito__estado="emitida") | Q(nota_credito_id=linea.nota_credito_id)).exclude(pk=linea.pk)
    devuelto_origen = otras.filter(linea_factura_origen_id=origen.pk).aggregate(total=Sum("cantidad"))["total"] or Decimal("0.00")
    # Las devoluciones anteriores a este vínculo sólo se asignan a una línea
    # concreta cuando el producto aparece una única vez en la factura original.
    origenes_producto = origen.factura.lineas.filter(producto_id=origen.producto_id)
    if origenes_producto.count() == 1:
        devuelto_origen += otras.filter(linea_factura_origen__isnull=True, producto_id=origen.producto_id).aggregate(total=Sum("cantidad"))["total"] or Decimal("0.00")
    if cantidad + devuelto_origen > origen.cantidad:
        disponible = max(origen.cantidad - devuelto_origen, Decimal("0.00"))
        raise ValidationError({"cantidad": f"La devolución excede la cantidad original disponible de esta línea ({disponible})."})
    if origen.producto_id:
        total_original = origenes_producto.aggregate(total=Sum("cantidad"))["total"] or Decimal("0.00")
        total_devuelto = otras.filter(producto_id=origen.producto_id).aggregate(total=Sum("cantidad"))["total"] or Decimal("0.00")
        if cantidad + total_devuelto > total_original:
            raise ValidationError({"cantidad": "Las devoluciones acumuladas exceden la cantidad facturada de este producto."})


def validar_devoluciones_nota(nota):
    """Revalida al emitir, incluso si otra nota consumió la cantidad del borrador."""
    if not nota.pk:
        return
    for linea in nota.lineas.select_related("producto", "linea_factura_origen__factura"):
        validar_cantidad_devolucion(linea)


def capturar_costos_devolucion(nota):
    """Reparte el costo histórico al centavo, incluido el remanente de la última devolución."""
    from .models import LineaNotaCredito

    grupos = {}
    for linea in nota.lineas.select_related('linea_factura_origen', 'producto'):
        if linea.producto_id and linea.producto.controla_inventario and linea.linea_factura_origen_id:
            grupos.setdefault(linea.linea_factura_origen_id, []).append(linea)
    for origen_id, lineas in grupos.items():
        origen = lineas[0].linea_factura_origen
        anteriores = LineaNotaCredito.objects.filter(
            linea_factura_origen_id=origen_id, nota_credito__estado='emitida',
            nota_credito__empresa_id=nota.empresa_id,
        ).exclude(nota_credito=nota)
        if origen.factura.lineas.filter(producto_id=origen.producto_id).count() == 1:
            anteriores = LineaNotaCredito.objects.filter(
                Q(linea_factura_origen_id=origen_id) | Q(linea_factura_origen__isnull=True, producto_id=origen.producto_id),
                nota_credito__factura_origen_id=origen.factura_id,
                nota_credito__estado='emitida', nota_credito__empresa_id=nota.empresa_id,
            ).exclude(nota_credito=nota)
        anteriores = list(anteriores)
        cantidad_anterior = sum((Decimal(linea.cantidad) for linea in anteriores), Decimal('0.00'))
        costo_anterior = sum((valor_contable_linea_inventario(linea) for linea in anteriores), Decimal('0.00'))
        cantidad_nota = sum((Decimal(linea.cantidad) for linea in lineas), Decimal('0.00'))
        costo_original = valor_contable_linea_inventario(origen)
        costo_acumulado = (costo_original * (cantidad_anterior + cantidad_nota) / Decimal(origen.cantidad)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        costo_nota = costo_acumulado - costo_anterior
        if costo_nota < 0:
            raise ValidationError('El costo de las devoluciones anteriores excede el costo original; revise su valoración histórica.')
        repartido = Decimal('0.00')
        cantidad_repartida = Decimal('0.00')
        for linea in lineas:
            cantidad_repartida += Decimal(linea.cantidad)
            objetivo = (costo_nota * cantidad_repartida / cantidad_nota).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            valor = objetivo - repartido
            linea.costo_contable_total = valor
            linea.save(update_fields=['costo_contable_total'])
            repartido += valor


def cantidad_disponible_para_linea(origen):
    from .models import LineaNotaCredito

    devueltas = LineaNotaCredito.objects.filter(
        nota_credito__empresa_id=origen.factura.empresa_id,
        nota_credito__factura_origen_id=origen.factura_id,
        nota_credito__estado="emitida",
    )
    filtro = Q(linea_factura_origen_id=origen.pk)
    if origen.factura.lineas.filter(producto_id=origen.producto_id).count() == 1:
        filtro |= Q(linea_factura_origen__isnull=True, producto_id=origen.producto_id)
    cantidad = devueltas.filter(filtro).aggregate(total=Sum("cantidad"))["total"] or Decimal("0.00")
    return max(Decimal(origen.cantidad) - cantidad, Decimal("0.00"))
