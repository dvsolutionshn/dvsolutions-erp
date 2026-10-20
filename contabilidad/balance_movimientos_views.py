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
from .reportes import datos_mayor
from .views import _empresa_desde_slug, _verificar_permiso_contable


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
    fecha = None
    if fecha_corte:
        try:
            fecha = parse_date(fecha_corte)
        except ValueError:
            fecha = None
        if fecha is None or fecha.isoformat() != fecha_corte:
            return HttpResponseBadRequest("La fecha de corte no es válida.")

    # El Balance es acumulado hasta su corte. El Mayor comparte ese mismo alcance
    # y mantiene sus importes Decimal y su saldo natural, sin una fórmula paralela.
    bloque = datos_mayor(empresa, fecha_fin=fecha_corte, cuenta=cuenta)[0]
    pagina = Paginator(bloque["movimientos"], 50).get_page(request.GET.get("pagina"))
    mayor_base = reverse("mayor_cuenta", args=[empresa.slug])
    filtros_mayor = {"cuenta": cuenta.pk, "fecha_fin": fecha_corte}
    mayor_url = f"{mayor_base}?{urlencode(filtros_mayor)}"
    for movimiento in pagina:
        linea_id = movimiento["linea"].pk
        movimiento["url_mayor"] = (
            f"{mayor_base}?{urlencode({**filtros_mayor, 'movimiento': linea_id})}"
            f"#movimiento-{linea_id}"
        )

    detalle_base = reverse("movimientos_balance_general", args=[empresa.slug, cuenta.pk])

    def enlace_pagina(numero):
        return f"{detalle_base}?{urlencode({'fecha_corte': fecha_corte, 'pagina': numero})}"

    respuesta = render(request, "contabilidad/partials/balance_movimientos.html", {
        "empresa": empresa, "cuenta": cuenta, "fecha_corte": fecha_corte, "fecha_corte_fecha": fecha,
        "pagina": pagina, "bloque": bloque, "mayor_url": mayor_url,
        "pagina_anterior_url": enlace_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "pagina_siguiente_url": enlace_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    })
    respuesta["X-Balance-Movimientos"] = "1"
    return respuesta
