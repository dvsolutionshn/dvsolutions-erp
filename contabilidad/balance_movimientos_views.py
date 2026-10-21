"""Consulta diferida del detalle de una cuenta, usando el Mayor existente."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import CuentaContable
from .reportes import datos_mayor, saldo_natural
from .views import _empresa_desde_slug, _verificar_permiso_contable


def _fecha_iso(valor):
    if not valor:
        return None
    fecha = parse_date(valor)
    if fecha is None or fecha.isoformat() != valor:
        raise ValueError("Fecha no válida")
    return fecha


def _render_movimientos(request, empresa, cuenta, bloque, ruta, filtros_detalle, filtros_mayor, **contexto):
    """Comparte la consulta paginada y sus enlaces; el Mayor calcula los importes."""
    pagina = Paginator(bloque["movimientos"], 50).get_page(request.GET.get("pagina"))
    mayor_base = reverse("mayor_cuenta", args=[empresa.slug])
    filtros_mayor = {"cuenta": cuenta.pk, **filtros_mayor}
    mayor_url = f"{mayor_base}?{urlencode(filtros_mayor)}"
    for movimiento in pagina:
        linea = movimiento["linea"]
        linea_id = linea.pk
        movimiento["url_mayor"] = (
            f"{mayor_base}?{urlencode({**filtros_mayor, 'movimiento': linea_id})}"
            f"#movimiento-{linea_id}"
        )
        if contexto.get("modo_resultados"):
            movimiento["importe"] = saldo_natural(cuenta, linea.debe, linea.haber)

    detalle_base = reverse(ruta, args=[empresa.slug, cuenta.pk])

    def enlace_pagina(numero):
        return f"{detalle_base}?{urlencode({**filtros_detalle, 'pagina': numero})}"

    respuesta = render(request, "contabilidad/partials/balance_movimientos.html", {
        "empresa": empresa, "cuenta": cuenta, **contexto,
        "pagina": pagina, "bloque": bloque, "mayor_url": mayor_url,
        "pagina_anterior_url": enlace_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "pagina_siguiente_url": enlace_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    })
    respuesta["X-Balance-Movimientos"] = "1"
    return respuesta


@login_required
@require_GET
@never_cache
def movimientos_balance_general(request, empresa_slug, cuenta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_reportes_contables")
    cuenta = get_object_or_404(
        CuentaContable, pk=cuenta_id, empresa=empresa,
        tipo__in=("activo", "pasivo", "patrimonio"),
    )
    fecha_corte = request.GET.get("fecha_corte") or ""
    try:
        fecha = _fecha_iso(fecha_corte)
    except ValueError:
        return HttpResponseBadRequest("La fecha de corte no es válida.")

    # El Balance es acumulado hasta su corte. El Mayor comparte ese mismo alcance
    # y mantiene sus importes Decimal y su saldo natural, sin una fórmula paralela.
    bloque = datos_mayor(empresa, fecha_fin=fecha_corte, cuenta=cuenta)[0]
    return _render_movimientos(
        request, empresa, cuenta, bloque, "movimientos_balance_general",
        {"fecha_corte": fecha_corte}, {"fecha_fin": fecha_corte},
        fecha_corte=fecha_corte, fecha_corte_fecha=fecha,
    )


@login_required
@require_GET
@never_cache
def movimientos_estado_resultados(request, empresa_slug, cuenta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_reportes_contables")
    cuenta = get_object_or_404(
        CuentaContable, pk=cuenta_id, empresa=empresa,
        tipo__in=("ingreso", "costo", "gasto"),
    )
    filtros = {
        "fecha_inicio": request.GET.get("fecha_inicio") or "",
        "fecha_fin": request.GET.get("fecha_fin") or "",
    }
    try:
        inicio = _fecha_iso(filtros["fecha_inicio"])
        fin = _fecha_iso(filtros["fecha_fin"])
    except ValueError:
        return HttpResponseBadRequest("Las fechas del período no son válidas.")
    if inicio and fin and inicio > fin:
        return HttpResponseBadRequest("La fecha inicial no puede ser posterior a la final.")

    # El Estado de Resultados omite cierres y considera sólo el período elegido.
    # Reutilizamos el mismo filtro del Mayor; su apertura no se presenta como
    # importe de un movimiento del período.
    bloque = datos_mayor(empresa, cuenta=cuenta, excluir_cierres=True, **filtros)[0]
    return _render_movimientos(
        request, empresa, cuenta, bloque, "movimientos_estado_resultados",
        filtros, {**filtros, "origen": "estado_resultados"},
        modo_resultados=True, fecha_inicio_fecha=inicio, fecha_fin_fecha=fin,
    )
