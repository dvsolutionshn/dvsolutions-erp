"""Presentación compartida de existencias móviles sobre el inventario actual."""

from datetime import date

from django.utils import timezone

from facturacion.models import ExistenciaLoteBodega, Producto


def productos_inventario_app(empresa):
    productos_inventario = list(
        Producto.objects.filter(
            empresa=empresa,
            activo=True,
            eliminado=False,
            controla_inventario=True,
            tipo_item="producto",
        )
        .select_related("inventario")
        .order_by("nombre")
    )
    producto_ids = [producto.id for producto in productos_inventario]
    existencias_lotes = list(
        ExistenciaLoteBodega.objects.filter(
            empresa=empresa,
            lote__empresa=empresa,
            lote__producto_id__in=producto_ids,
            lote__activo=True,
            bodega__empresa=empresa,
            bodega__activa=True,
        ).select_related("bodega", "lote")
    )
    bodegas_por_producto = {producto_id: {} for producto_id in producto_ids}
    lotes_por_producto = {producto_id: {} for producto_id in producto_ids}
    for existencia in existencias_lotes:
        producto_id = existencia.lote.producto_id
        bodegas_producto = bodegas_por_producto.setdefault(producto_id, {})
        bodegas_producto[existencia.bodega.nombre] = (
            bodegas_producto.get(existencia.bodega.nombre, 0) + existencia.cantidad
        )
        lotes_producto = lotes_por_producto.setdefault(producto_id, {})
        lote_payload = lotes_producto.setdefault(
            existencia.lote_id,
            {
                "numero": existencia.lote.numero_lote,
                "fecha_vencimiento": (
                    existencia.lote.fecha_vencimiento.isoformat()
                    if existencia.lote.fecha_vencimiento
                    else ""
                ),
                "fecha_vencimiento_label": (
                    existencia.lote.fecha_vencimiento.strftime("%d/%m/%Y")
                    if existencia.lote.fecha_vencimiento
                    else "Sin vencimiento"
                ),
                "cantidad": 0,
                "bodegas": [],
                "dias_para_vencer": existencia.lote.dias_para_vencer,
                "vencido": existencia.lote.vencido,
                "proximo_vencer": existencia.lote.por_vencer,
            },
        )
        lote_payload["cantidad"] += existencia.cantidad
        lote_payload["bodegas"].append(
            {"nombre": existencia.bodega.nombre, "cantidad": float(existencia.cantidad)}
        )

    hoy = timezone.localdate()
    inventario_payload = []
    for producto in productos_inventario:
        inventario = getattr(producto, "inventario", None)
        total = inventario.existencias if inventario else 0
        stock_minimo = inventario.stock_minimo if inventario else 0
        lotes = list(lotes_por_producto.get(producto.id, {}).values())
        lotes.sort(
            key=lambda lote: (
                date.fromisoformat(lote["fecha_vencimiento"])
                if lote["fecha_vencimiento"]
                else date.max,
                lote["numero"].casefold(),
            )
        )
        bodegas = [
            {"nombre": nombre, "cantidad": float(cantidad)}
            for nombre, cantidad in sorted(
                bodegas_por_producto.get(producto.id, {}).items(),
                key=lambda item: item[0].casefold(),
            )
            if cantidad != 0
        ]
        lotes_disponibles = [lote for lote in lotes if lote["cantidad"] > 0]
        lote_proximo = next(
            (lote for lote in lotes_disponibles if lote["fecha_vencimiento"]),
            None,
        )
        sin_stock = total <= 0
        stock_bajo = bool(not sin_stock and stock_minimo > 0 and total <= stock_minimo)
        vencido = any(lote["vencido"] for lote in lotes_disponibles)
        proximo_vencer = any(lote["proximo_vencer"] for lote in lotes_disponibles)
        inventario_payload.append(
            {
                "id": producto.id,
                "nombre": producto.nombre,
                "codigo": producto.codigo or "",
                "unidad": producto.get_unidad_medida_display(),
                "existencia": float(total),
                "stock_minimo": float(stock_minimo),
                "estado_stock": "sin_stock" if sin_stock else "stock_bajo" if stock_bajo else "normal",
                "stock_bajo": stock_bajo,
                "sin_stock": sin_stock,
                "proximo_vencer": proximo_vencer,
                "vencido": vencido,
                "bodegas": bodegas,
                "lotes": lotes,
                "maneja_lotes": bool(lotes),
                "proximo_vencimiento": lote_proximo["fecha_vencimiento"] if lote_proximo else "",
                "proximo_vencimiento_label": lote_proximo["fecha_vencimiento_label"] if lote_proximo else "",
                "dias_proximo_vencimiento": (
                    (date.fromisoformat(lote_proximo["fecha_vencimiento"]) - hoy).days
                    if lote_proximo
                    else None
                ),
            }
        )
    return inventario_payload
