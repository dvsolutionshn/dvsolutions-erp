"""Lecturas del panel contable, siempre limitadas a una sola empresa."""

from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    AsientoContable,
    CuentaContable,
    CuentaFinanciera,
    MovimientoBancario,
    PeriodoContable,
)


MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def datos_dashboard_contable(empresa, *, hoy=None):
    """Devuelve indicadores reales sin crear períodos, cuentas o configuración.

    Los conteos del catálogo y la actividad reciente son históricos. La
    distribución de estados pertenece exclusivamente al mes indicado.
    """
    hoy = hoy or timezone.localdate()
    inicio = date(hoy.year, hoy.month, 1)
    fin = date(hoy.year + (hoy.month == 12), 1 if hoy.month == 12 else hoy.month + 1, 1)
    filtro_mes = Q(fecha__gte=inicio, fecha__lt=fin)

    cuentas_por_tipo = list(
        CuentaContable.objects.filter(empresa=empresa)
        .values("tipo")
        .annotate(
            total=Count("id"),
            activas=Count("id", filter=Q(activa=True)),
            de_movimiento=Count("id", filter=Q(activa=True, acepta_movimientos=True)),
        )
        .order_by("tipo")
    )
    nombres_tipo = dict(CuentaContable.TIPO_CHOICES)
    for tipo in cuentas_por_tipo:
        tipo["nombre"] = nombres_tipo.get(tipo["tipo"], tipo["tipo"])

    asientos = AsientoContable.objects.filter(empresa=empresa)
    conteos = asientos.aggregate(
        asientos=Count("id"),
        contabilizados=Count("id", filter=Q(estado="contabilizado")),
        borradores=Count("id", filter=Q(estado="borrador")),
        anulados=Count("id", filter=Q(estado="anulado")),
        mes_total=Count("id", filter=filtro_mes),
        mes_contabilizados=Count("id", filter=filtro_mes & Q(estado="contabilizado")),
        mes_borradores=Count("id", filter=filtro_mes & Q(estado="borrador")),
        mes_anulados=Count("id", filter=filtro_mes & Q(estado="anulado")),
    )
    total_mes = conteos["mes_total"]
    actividad_mes = {
        "etiqueta": f"{MESES[hoy.month - 1].capitalize()} de {hoy.year}",
        "fecha_inicio": inicio,
        "total": total_mes,
        "contabilizados": conteos["mes_contabilizados"],
        "borradores": conteos["mes_borradores"],
        "anulados": conteos["mes_anulados"],
        "porcentaje_contabilizado": round(conteos["mes_contabilizados"] * 100 / total_mes) if total_mes else 0,
    }
    actividad_mes["estados"] = [
        {
            "clave": clave,
            "nombre": nombre,
            "total": actividad_mes[clave],
            "porcentaje": round(actividad_mes[clave] * 100 / total_mes) if total_mes else 0,
        }
        for clave, nombre in (
            ("contabilizados", "Contabilizados"),
            ("borradores", "Borradores"),
            ("anulados", "Anulados"),
        )
    ]

    # Alias propios: total_debe y total_haber son propiedades del modelo.
    ultimos_asientos = list(
        asientos.annotate(
            importe_debe=Coalesce(
                Sum("lineas__debe"), Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            ),
            importe_haber=Coalesce(
                Sum("lineas__haber"), Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            ),
        ).order_by("-fecha", "-id")[:8]
    )
    periodo_actual = PeriodoContable.objects.filter(
        empresa=empresa, anio=hoy.year, mes=hoy.month,
    ).first()
    cuentas_financieras = CuentaFinanciera.objects.filter(empresa=empresa).aggregate(
        cuentas_activas=Count("id", filter=Q(activa=True)),
        bancos_activos=Count("id", filter=Q(activa=True, tipo="banco")),
    )
    movimientos = MovimientoBancario.objects.filter(empresa=empresa).aggregate(
        pendientes=Count("id", filter=Q(estado__in=("pendiente", "clasificado"))),
        sin_clasificar=Count("id", filter=Q(estado="pendiente")),
    )

    return {
        "fecha_hoy": hoy,
        "resumen": {
            **{clave: conteos[clave] for clave in ("asientos", "contabilizados", "borradores", "anulados")},
            "cuentas": sum(tipo["total"] for tipo in cuentas_por_tipo),
            "cuentas_activas": sum(tipo["activas"] for tipo in cuentas_por_tipo),
            "cuentas_movimiento": sum(tipo["de_movimiento"] for tipo in cuentas_por_tipo),
        },
        "cuentas_por_tipo": cuentas_por_tipo,
        "actividad_mes": actividad_mes,
        "ultimos_asientos": ultimos_asientos,
        "periodo_actual": periodo_actual,
        "estado_periodo": periodo_actual.get_estado_display() if periodo_actual else "Sin período registrado",
        "bancos_resumen": {
            **cuentas_financieras,
            "movimientos_pendientes": movimientos["pendientes"],
            "movimientos_sin_clasificar": movimientos["sin_clasificar"],
        },
    }
