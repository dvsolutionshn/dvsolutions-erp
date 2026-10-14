"""Controles del cierre mensual, siempre dentro de una sola empresa."""

from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from facturacion.models import CompraInventario, Factura, MovimientoInventario, NotaCredito, PagoCompra, PagoComisionProveedor, PagoFactura
from rrhh.models import PeriodoPlanilla

from .models import AsientoContable, LineaAsientoContable, MovimientoBancario


def _documentos_sin_asiento(empresa, periodo):
    """No dar por terminado un documento cuyo registro automático quedó pendiente."""
    rango = (periodo.fecha_inicio, periodo.fecha_fin)
    registrados = set(
        AsientoContable.objects.filter(empresa=empresa, estado="contabilizado", fecha__range=rango)
        .values_list("documento_tipo", "documento_id", "evento")
    )
    pendientes = []

    def agregar(documento, tipo, evento, fecha, referencia, vista, objeto_id=None):
        if (tipo, documento.pk, evento) in registrados:
            return
        pendientes.append({
            "fecha": fecha,
            "tipo": {"factura": "Factura", "pago_factura": "Cobro", "compra": "Compra",
                     "pago_compra": "Pago a proveedor", "nota_credito": "Nota de crédito",
                     "pago_comision_proveedor": "Pago de comisión", "planilla": "Planilla"}[tipo],
            "referencia": referencia or str(documento.pk),
            "descripcion": "Devengo sin asiento" if evento == "cierre" else "Pago sin asiento" if evento == "pago" else "Documento sin asiento",
            "url": reverse(vista, args=[empresa.slug, objeto_id or documento.pk]),
        })

    for factura in Factura.objects.filter(empresa=empresa, estado="emitida", total__gt=0, fecha_emision__range=rango):
        agregar(factura, "factura", "emision", factura.fecha_emision, factura.numero_factura, "ver_factura")
    for nota in NotaCredito.objects.filter(empresa=empresa, estado="emitida", total__gt=0, fecha_emision__range=rango):
        agregar(nota, "nota_credito", "emision", nota.fecha_emision, nota.numero_nota, "ver_nota_credito")
    for pago in PagoFactura.objects.filter(factura__empresa=empresa, fecha__range=rango).filter(
        Q(monto__gt=0) | Q(retencion_isr__gt=0) | Q(retencion_isv__gt=0)
    ).select_related("factura"):
        agregar(pago, "pago_factura", "cobro", pago.fecha, pago.factura.numero_factura, "ver_factura", pago.factura_id)
    for compra in CompraInventario.objects.filter(empresa=empresa, estado="aplicada", fecha_documento__range=rango).prefetch_related("lineas"):
        if compra.total_documento > 0:
            agregar(compra, "compra", "aplicacion", compra.fecha_documento, compra.numero_compra, "ver_compra")
    movimientos_manuales = MovimientoInventario.objects.filter(
        empresa=empresa, tipo__in=["entrada", "ajuste_entrada", "ajuste_salida", "regalia"], valor_contable__gt=0,
    ).filter(
        Q(entrada_documento__fecha_documento__range=rango)
        | Q(entrada_documento__isnull=True, fecha__date__range=rango)
    ).select_related("entrada_documento", "producto")
    for movimiento in movimientos_manuales:
        if ("movimiento_inventario", movimiento.pk, "ajuste") not in registrados:
            pendientes.append({
                "fecha": movimiento.entrada_documento.fecha_documento if movimiento.entrada_documento_id else timezone.localdate(movimiento.fecha),
                "tipo": "Ajuste de inventario", "referencia": movimiento.referencia or str(movimiento.pk),
                "descripcion": f"{movimiento.get_tipo_display()}: {movimiento.producto.nombre}",
                "url": reverse("kardex_inventario", args=[empresa.slug]),
            })
    for pago in PagoCompra.objects.filter(compra__empresa=empresa, monto__gt=0, fecha__range=rango).select_related("compra"):
        agregar(pago, "pago_compra", "egreso", pago.fecha, pago.compra.numero_compra, "ver_compra", pago.compra_id)
    for pago in PagoComisionProveedor.objects.filter(
        comision_origen__factura__empresa=empresa, monto__gt=0, fecha__range=rango,
    ).select_related("comision_origen__factura"):
        factura = pago.comision_origen.factura
        agregar(pago, "pago_comision_proveedor", "egreso", pago.fecha, factura.numero_factura, "ver_factura", factura.pk)
    for planilla in PeriodoPlanilla.objects.filter(empresa=empresa, estado__in=["cerrada", "pagada"]).filter(
        Q(fecha_fin__range=rango) | Q(estado="pagada", fecha_pago__range=rango)
    ).prefetch_related("detalles"):
        if rango[0] <= planilla.fecha_fin <= rango[1] and planilla.total_devengado > 0:
            agregar(planilla, "planilla", "cierre", planilla.fecha_fin, planilla.nombre, "ver_planilla")
        if planilla.estado == "pagada" and rango[0] <= planilla.fecha_pago <= rango[1] and planilla.total_neto > 0:
            agregar(planilla, "planilla", "pago", planilla.fecha_pago, planilla.nombre, "ver_planilla")
    return sorted(pendientes, key=lambda item: (item["fecha"], item["tipo"], item["referencia"]))


def revisar_cierre_periodo(empresa, periodo):
    if periodo.empresa_id != empresa.pk:
        raise ValueError("El período debe pertenecer a la empresa que se está revisando.")
    rango = {"fecha__gte": periodo.fecha_inicio, "fecha__lte": periodo.fecha_fin}
    asientos = AsientoContable.objects.filter(empresa=empresa, **rango)
    borradores = asientos.filter(estado="borrador").order_by("fecha", "id")
    from .estado_conversion import asientos_conversion_pendiente
    conversiones_pendientes = asientos_conversion_pendiente(
        empresa, fecha_corte=periodo.fecha_fin, asientos=asientos,
    ).order_by("fecha", "id")
    # Una operación y su reversión completa dentro del mismo mes no dejan
    # importes por convertir. La advertencia histórica se conserva en el asiento.
    neutralizados = Q(pk__in=[])
    for tipo, documento_id in conversiones_pendientes.order_by().values_list('documento_tipo', 'documento_id').distinct():
        if not tipo or documento_id is None:
            continue
        saldos = LineaAsientoContable.objects.filter(
            asiento__empresa=empresa, asiento__estado='contabilizado', asiento__fecha__range=(periodo.fecha_inicio, periodo.fecha_fin),
            asiento__documento_tipo=tipo, asiento__documento_id=documento_id,
        ).values('cuenta_id').annotate(debe=Sum('debe'), haber=Sum('haber'))
        if saldos and all((fila['debe'] - fila['haber']).quantize(Decimal('0.01')) == 0 for fila in saldos):
            neutralizados |= Q(documento_tipo=tipo, documento_id=documento_id)
    conversiones_pendientes = conversiones_pendientes.exclude(neutralizados)
    contabilizados = asientos.filter(estado="contabilizado").annotate(
        suma_debe=Coalesce(Sum("lineas__debe"), Decimal("0.00")),
        suma_haber=Coalesce(Sum("lineas__haber"), Decimal("0.00")),
        cantidad_lineas=Count("lineas"),
        lineas_invalidas=Count("lineas", filter=(
            Q(lineas__debe__lt=0) | Q(lineas__haber__lt=0)
            | Q(lineas__debe=0, lineas__haber=0)
            | Q(lineas__debe__gt=0, lineas__haber__gt=0)
            | ~Q(lineas__cuenta__empresa_id=F("empresa_id"))
        )),
    )
    invalidos = contabilizados.filter(
        Q(cantidad_lineas__lt=2) | Q(suma_debe__lte=0)
        | ~Q(suma_debe=F("suma_haber")) | Q(lineas_invalidas__gt=0)
    ).order_by("fecha", "id")
    movimientos = MovimientoBancario.objects.filter(empresa=empresa, **rango)
    no_contabilizados = movimientos.exclude(estado="contabilizado").select_related("cuenta_financiera", "clasificacion").order_by("fecha", "id")
    no_conciliados = movimientos.filter(estado="contabilizado", conciliado=False).select_related("cuenta_financiera", "asiento").order_by("fecha", "id")
    sin_asiento = movimientos.filter(estado="contabilizado").exclude(
        asiento__empresa=empresa, asiento__estado="contabilizado", asiento__fecha=F("fecha"),
    ).count()
    totales = LineaAsientoContable.objects.filter(
        asiento__empresa=empresa, asiento__estado="contabilizado",
        asiento__fecha__range=(periodo.fecha_inicio, periodo.fecha_fin),
    ).aggregate(debe=Sum("debe"), haber=Sum("haber"))
    debe, haber = totales["debe"] or Decimal("0.00"), totales["haber"] or Decimal("0.00")
    documentos = _documentos_sin_asiento(empresa, periodo)
    control = {
        "asientos_borrador": borradores.count(), "asientos_invalidos": invalidos.count(),
        "conversiones_pendientes": conversiones_pendientes.count(),
        "movimientos_bancarios": no_contabilizados.count(), "movimientos_sin_conciliar": no_conciliados.count(),
        "movimientos_sin_asiento": sin_asiento, "documentos_sin_asiento": len(documentos),
        "debe": debe, "haber": haber, "balance_cuadrado": debe == haber,
    }
    control["total"] = sum(control[clave] for clave in (
        "asientos_borrador", "asientos_invalidos", "conversiones_pendientes", "movimientos_bancarios",
        "movimientos_sin_conciliar", "movimientos_sin_asiento", "documentos_sin_asiento",
    )) + (0 if control["balance_cuadrado"] else 1)
    return {"control": control, "asientos_borrador": borradores, "asientos_invalidos": invalidos,
            "conversiones_pendientes": conversiones_pendientes,
            "movimientos_no_contabilizados": no_contabilizados, "movimientos_sin_conciliar": no_conciliados,
            "documentos_sin_asiento": documentos}
