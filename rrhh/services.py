from calendar import monthrange
from decimal import Decimal

from django.db import transaction

from .models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla


TWOPLACES = Decimal("0.01")


def q2(valor):
    return Decimal(valor or 0).quantize(TWOPLACES)


def configuracion_rrhh(empresa):
    return ConfiguracionRRHHEmpresa.objects.get_or_create(empresa=empresa)[0]


def dias_periodo(periodo):
    return (periodo.fecha_fin - periodo.fecha_inicio).days + 1


def dias_pagables_empleado(empleado, periodo):
    inicio = max(empleado.fecha_ingreso, periodo.fecha_inicio)
    fin = min(empleado.fecha_salida or periodo.fecha_fin, periodo.fecha_fin)
    if fin < inicio:
        return Decimal("0.00")
    if periodo.frecuencia == "mensual":
        return Decimal("30.00")
    return Decimal((fin - inicio).days + 1)


def calcular_decimo_cuarto_proporcional(empleado, periodo):
    if not periodo.incluir_14avo:
        return Decimal("0.00")
    corte_inicio = periodo.fecha_fin.replace(month=7, day=1)
    corte_fin = periodo.fecha_fin.replace(month=6, day=30)
    if periodo.fecha_fin.month <= 6:
        corte_inicio = corte_inicio.replace(year=periodo.fecha_fin.year - 1)
    else:
        corte_fin = corte_fin.replace(year=periodo.fecha_fin.year + 1)
    inicio = max(empleado.fecha_ingreso, corte_inicio)
    fin = min(empleado.fecha_salida or corte_fin, corte_fin)
    if fin < inicio:
        return Decimal("0.00")
    dias = Decimal((fin - inicio).days + 1)
    return q2((empleado.salario_mensual / Decimal("360.00")) * dias)


def movimientos_periodo(empleado, periodo):
    qs = MovimientoPlanilla.objects.filter(empleado=empleado, aplicado=False).filter(
        periodo__isnull=True
    ) | MovimientoPlanilla.objects.filter(empleado=empleado, periodo=periodo, aplicado=False)
    return qs


def calcular_detalle_planilla(empleado, periodo, *, horas_extra_diurnas=0, horas_extra_nocturnas=0, horas_extra_feriado=0):
    config = configuracion_rrhh(periodo.empresa)
    dias = dias_pagables_empleado(empleado, periodo)
    salario_base = q2((empleado.salario_mensual / Decimal(str(config.dias_base_mes))) * dias)
    monto_horas_extra = q2(
        (Decimal(horas_extra_diurnas or 0) * empleado.salario_hora * config.hora_extra_diurna_factor)
        + (Decimal(horas_extra_nocturnas or 0) * empleado.salario_hora * config.hora_extra_nocturna_factor)
        + (Decimal(horas_extra_feriado or 0) * empleado.salario_hora * config.hora_extra_feriado_factor)
    )

    bonos = Decimal("0.00")
    comisiones = Decimal("0.00")
    prestamos = Decimal("0.00")
    otras_deducciones = Decimal("0.00")
    movimientos = list(movimientos_periodo(empleado, periodo))
    for movimiento in movimientos:
        if movimiento.tipo == "bono":
            bonos += movimiento.monto
        elif movimiento.tipo == "comision":
            comisiones += movimiento.monto
        elif movimiento.tipo == "prestamo":
            prestamos += movimiento.monto
        elif movimiento.tipo == "deduccion":
            otras_deducciones += movimiento.monto

    decimo_cuarto = calcular_decimo_cuarto_proporcional(empleado, periodo)
    total_devengado = q2(salario_base + monto_horas_extra + bonos + comisiones + decimo_cuarto)
    ihss_base = min(salario_base, config.ihss_techo_mensual)
    ihss = q2(ihss_base * config.ihss_trabajador_porcentaje) if empleado.aplica_ihss else Decimal("0.00")
    rap = q2(salario_base * config.rap_trabajador_porcentaje) if empleado.aplica_rap and config.aplicar_rap else Decimal("0.00")
    isr = q2(salario_base * config.isr_porcentaje_base) if empleado.aplica_isr else Decimal("0.00")
    total_deducciones = q2(ihss + rap + isr + prestamos + otras_deducciones)
    neto = q2(total_devengado - total_deducciones)
    return {
        "dias_pagados": dias,
        "salario_base": salario_base,
        "horas_extra_diurnas": Decimal(horas_extra_diurnas or 0),
        "horas_extra_nocturnas": Decimal(horas_extra_nocturnas or 0),
        "horas_extra_feriado": Decimal(horas_extra_feriado or 0),
        "monto_horas_extra": monto_horas_extra,
        "bonos": q2(bonos),
        "comisiones": q2(comisiones),
        "decimo_cuarto": decimo_cuarto,
        "total_devengado": total_devengado,
        "ihss": ihss,
        "rap": rap,
        "isr": isr,
        "prestamos": q2(prestamos),
        "otras_deducciones": q2(otras_deducciones),
        "total_deducciones": total_deducciones,
        "neto_pagar": neto,
        "movimientos": movimientos,
    }


def recalcular_detalle_planilla(detalle):
    config = configuracion_rrhh(detalle.periodo.empresa)
    detalle.monto_horas_extra = q2(
        (Decimal(detalle.horas_extra_diurnas or 0) * detalle.empleado.salario_hora * config.hora_extra_diurna_factor)
        + (Decimal(detalle.horas_extra_nocturnas or 0) * detalle.empleado.salario_hora * config.hora_extra_nocturna_factor)
        + (Decimal(detalle.horas_extra_feriado or 0) * detalle.empleado.salario_hora * config.hora_extra_feriado_factor)
    )
    detalle.salario_base = q2(detalle.salario_base)
    detalle.bonos = q2(detalle.bonos)
    detalle.comisiones = q2(detalle.comisiones)
    detalle.decimo_cuarto = q2(detalle.decimo_cuarto)
    detalle.ihss = q2(detalle.ihss)
    detalle.rap = q2(detalle.rap)
    detalle.isr = q2(detalle.isr)
    detalle.prestamos = q2(detalle.prestamos)
    detalle.otras_deducciones = q2(detalle.otras_deducciones)
    detalle.total_devengado = q2(
        detalle.salario_base
        + detalle.monto_horas_extra
        + detalle.bonos
        + detalle.comisiones
        + detalle.decimo_cuarto
    )
    detalle.total_deducciones = q2(
        detalle.ihss
        + detalle.rap
        + detalle.isr
        + detalle.prestamos
        + detalle.otras_deducciones
    )
    detalle.neto_pagar = q2(detalle.total_devengado - detalle.total_deducciones)
    return detalle


@transaction.atomic
def generar_planilla(periodo):
    empleados = Empleado.objects.filter(empresa=periodo.empresa, estado="activo", fecha_ingreso__lte=periodo.fecha_fin)
    if periodo.fecha_inicio:
        empleados = empleados.filter(fecha_salida__isnull=True) | empleados.filter(fecha_salida__gte=periodo.fecha_inicio)
    creados = 0
    for empleado in empleados.distinct():
        data = calcular_detalle_planilla(empleado, periodo)
        movimientos = data.pop("movimientos")
        DetallePlanilla.objects.update_or_create(
            periodo=periodo,
            empleado=empleado,
            defaults=data,
        )
        MovimientoPlanilla.objects.filter(id__in=[mov.id for mov in movimientos]).update(periodo=periodo, aplicado=True)
        creados += 1
    periodo.estado = "calculada"
    periodo.save(update_fields=["estado"])
    return creados
