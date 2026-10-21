"""Estado de conversión compartido por cierres y reportes, por empresa y corte."""
from django.core.exceptions import ValidationError
from django.db.models import F, Sum

from .models import AsientoContable


def asientos_cobros_anulados_neutralizados(empresa, fecha_corte=None):
    """Omite alertas sólo cuando cobro y reversión se cancelan al corte.

    Los asientos permanecen en los mayores y balances. Antes de la fecha de
    reversión, el cobro original conserva sus advertencias históricas.
    """
    from facturacion.models import PagoFactura

    pagos = PagoFactura.objects.filter(factura__empresa=empresa, anulado=True)
    if fecha_corte:
        pagos = pagos.filter(fecha_anulacion__lte=fecha_corte)
    fuentes = AsientoContable.objects.filter(
        empresa=empresa, documento_tipo="pago_factura", documento_id__in=pagos.values("pk"),
        evento__in=("cobro", "anulacion"), estado="contabilizado",
    )
    if fecha_corte:
        fuentes = fuentes.filter(fecha__lte=fecha_corte)
    por_documento = {}
    for asiento in fuentes.values("pk", "documento_id", "evento"):
        por_documento.setdefault(asiento["documento_id"], {})[asiento["evento"]] = asiento["pk"]
    candidatos = {pk: eventos for pk, eventos in por_documento.items() if set(eventos) == {"cobro", "anulacion"}}
    if not candidatos:
        return []
    saldos = fuentes.filter(documento_id__in=candidatos).order_by().values("documento_id", "lineas__cuenta_id").annotate(
        saldo=Sum(F("lineas__debe") - F("lineas__haber")),
    )
    inconsistentes = {fila["documento_id"] for fila in saldos if fila["saldo"] is None or fila["saldo"] != 0}
    return [asiento_id for pk, eventos in candidatos.items() if pk not in inconsistentes for asiento_id in eventos.values()]


def asientos_conversion_pendiente(empresa, fecha_corte=None, asientos=None):
    from .revision_conversion import revision_conversion_vigente
    fuentes = (asientos if asientos is not None else AsientoContable.objects.all()).filter(
        empresa=empresa, estado="contabilizado", conversion_verificada=False,
    )
    if fecha_corte:
        fuentes = fuentes.filter(fecha__lte=fecha_corte)
    fuentes = fuentes.exclude(pk__in=asientos_cobros_anulados_neutralizados(empresa, fecha_corte))
    revisados = []
    for asiento in fuentes.select_related("revision_conversion", "revision_conversion__ajuste").iterator():
        try:
            if revision_conversion_vigente(asiento, fecha_corte=fecha_corte):
                revisados.append(asiento.pk)
        except ValidationError:
            # Una revisión inconsistente sigue pendiente; nunca libera un corte.
            continue
    return fuentes.exclude(pk__in=revisados)
