from math import ceil

from django.db.models import Sum

from .models import BahiaServicio, ConfiguracionTecnicentro, OrdenServicio


ESTADOS_OCUPANDO_BAHIA = ["recepcion", "diagnostico", "cotizacion", "aprobacion", "reparacion", "pruebas"]


def capacidad_operativa(empresa):
    return max(1, BahiaServicio.objects.filter(empresa=empresa, activa=True).count())


def actualizar_estimaciones_cola(empresa):
    """Reparte la carga pendiente entre bahías y devuelve la cola ya priorizada."""
    bahias = capacidad_operativa(empresa)
    config, _ = ConfiguracionTecnicentro.objects.get_or_create(empresa=empresa)
    carga_activa = OrdenServicio.objects.filter(
        empresa=empresa, estado__in=ESTADOS_OCUPANDO_BAHIA
    ).aggregate(total=Sum("tiempo_reparacion_estimado_min"))["total"] or 0
    cola = list(
        OrdenServicio.objects.filter(empresa=empresa, estado="espera").order_by("prioridad", "fecha_recepcion", "id")
    )
    # Urgentes y altas deben ir antes que las normales.
    peso = {"urgente": 0, "alta": 1, "normal": 2}
    cola.sort(key=lambda orden: (peso.get(orden.prioridad, 2), orden.fecha_recepcion, orden.id))
    acumulado = carga_activa + config.tiempo_recepcion_minutos
    cambios = []
    for orden in cola:
        espera = max(0, ceil(acumulado / bahias))
        if orden.tiempo_espera_estimado_min != espera:
            orden.tiempo_espera_estimado_min = espera
            cambios.append(orden)
        acumulado += orden.tiempo_reparacion_estimado_min
    if cambios:
        OrdenServicio.objects.bulk_update(cambios, ["tiempo_espera_estimado_min"])
    return cola


def espera_para_nuevo_ingreso(empresa):
    cola = actualizar_estimaciones_cola(empresa)
    if not cola:
        return 0
    ultima = cola[-1]
    return ultima.tiempo_espera_estimado_min + ceil(
        ultima.tiempo_reparacion_estimado_min / capacidad_operativa(empresa)
    )
