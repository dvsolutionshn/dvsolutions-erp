"""Estado de conversión compartido por cierres y reportes, por empresa y corte."""
from django.core.exceptions import ValidationError

from .models import AsientoContable


def asientos_conversion_pendiente(empresa, fecha_corte=None, asientos=None):
    from .revision_conversion import revision_conversion_vigente
    fuentes = (asientos if asientos is not None else AsientoContable.objects.all()).filter(
        empresa=empresa, estado="contabilizado", conversion_verificada=False,
    )
    if fecha_corte:
        fuentes = fuentes.filter(fecha__lte=fecha_corte)
    revisados = []
    for asiento in fuentes.select_related("revision_conversion", "revision_conversion__ajuste").iterator():
        try:
            if revision_conversion_vigente(asiento, fecha_corte=fecha_corte):
                revisados.append(asiento.pk)
        except ValidationError:
            # Una revisión inconsistente sigue pendiente; nunca libera un corte.
            continue
    return fuentes.exclude(pk__in=revisados)
