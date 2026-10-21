"""Presentación de consulta del Kardex original, sin escrituras de inventario.

La mezcla, deduplicación, orden y resúmenes siguen en kardex_inventario. Este
adaptador sólo protege las relaciones y pagina sus registros históricos.
"""

from datetime import date
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import (
    BodegaInventario, CompraInventario, EntradaInventarioDocumento, Factura,
    LoteInventario, MovimientoInventario, MovimientoLoteBodega, NotaCredito,
    Producto, Proveedor,
)


TIPOS_LOTE_KARDEX = ("traslado_salida", "traslado_entrada", "regalia")


def es_kardex_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def kardex_disponible_app(request, empresa):
    """Capacidad existente; no lee movimientos ni prepara configuración."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_inventario", empresa)
    )


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página del Kardex no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página del Kardex no válidos.")
    return numero


def autorizar_kardex_app(request, empresa):
    if not kardex_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar el Kardex en esta empresa.")
    if request.method not in {"GET", "HEAD"} or request.FILES or request.POST:
        raise PermissionDenied("El Kardex móvil es únicamente de consulta.")
    permitidos = {"app", "producto", "tipo", "fecha_desde", "fecha_hasta", "page"}
    if (
        set(request.GET) - permitidos
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros del Kardex móvil inválidos.")
    if request.GET.get("producto"):
        _numero_canonico(request.GET["producto"])
    if "page" in request.GET:
        _numero_canonico(request.GET["page"])
    tipos = {valor for valor, _ in MovimientoInventario.TIPOS} | set(TIPOS_LOTE_KARDEX)
    if request.GET.get("tipo", "").strip() not in {"", *tipos}:
        raise PermissionDenied("Tipo de movimiento no válido.")
    for campo in ("fecha_desde", "fecha_hasta"):
        valor = request.GET.get(campo, "").strip()
        if not valor:
            continue
        try:
            fecha = date.fromisoformat(valor)
        except ValueError:
            raise PermissionDenied("Fecha de filtro del Kardex no válida.") from None
        if fecha.isoformat() != valor:
            raise PermissionDenied("Fecha de filtro del Kardex no válida.")


def validar_relaciones_kardex_app(empresa, producto_id=None):
    """Validar todo el ámbito original antes de filtros, JOIN o deduplicación.

No se exige actividad, stock ni coincidencia de snapshots. Los movimientos por
lote que el Kardex original no utiliza tampoco forman parte de este ámbito.
"""
    # Una selección inexistente o ajena se deniega antes de consultar el
    # historial. Las banderas actuales del producto no restringen su historia.
    seleccionado = get_object_or_404(
        Producto, pk=_numero_canonico(producto_id), empresa=empresa,
    ) if producto_id else None
    productos = Producto.objects.filter(empresa=empresa).values("pk")
    bodegas = BodegaInventario.objects.filter(empresa=empresa).values("pk")
    usuarios = get_user_model().objects.values("pk")
    facturas = Factura.objects.filter(empresa=empresa).values("pk")
    proveedores = Proveedor.objects.filter(empresa=empresa).values("pk")
    lotes = LoteInventario.objects.filter(empresa=empresa, producto_id__in=productos).filter(
        Q(proveedor_id__isnull=True) | Q(proveedor_id__in=proveedores),
    ).values("pk")
    generales = MovimientoInventario.objects.filter(empresa=empresa)
    por_lote = MovimientoLoteBodega.objects.filter(empresa=empresa, tipo__in=TIPOS_LOTE_KARDEX)
    if (
        generales.exclude(producto_id__in=productos).exists()
        or generales.filter(bodega_id__isnull=False).exclude(bodega_id__in=bodegas).exists()
        or generales.filter(factura_id__isnull=False).exclude(factura_id__in=facturas).exists()
        or generales.filter(nota_credito_id__isnull=False).exclude(nota_credito_id__in=NotaCredito.objects.filter(empresa=empresa).values("pk")).exists()
        or generales.filter(entrada_documento_id__isnull=False).exclude(entrada_documento_id__in=EntradaInventarioDocumento.objects.filter(empresa=empresa).values("pk")).exists()
        or generales.filter(compra_documento_id__isnull=False).exclude(compra_documento_id__in=CompraInventario.objects.filter(empresa=empresa).values("pk")).exists()
        or generales.filter(usuario_id__isnull=False).exclude(usuario_id__in=usuarios).exists()
        or por_lote.exclude(lote_id__in=lotes).exists()
        or por_lote.exclude(bodega_id__in=bodegas).exists()
        or por_lote.filter(factura_id__isnull=False).exclude(factura_id__in=facturas).exists()
        or por_lote.filter(usuario_id__isnull=False).exclude(usuario_id__in=usuarios).exists()
    ):
        raise PermissionDenied("El Kardex contiene una relación incompatible con la empresa activa.")
    return seleccionado


def url_kardex_app(empresa, **parametros):
    return f"{reverse('kardex_inventario', args=[empresa.slug])}?{urlencode({'app': '1', **parametros})}"


def render_kardex_app(request, empresa, contexto, producto_seleccionado=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    pagina = Paginator(contexto["movimientos"], 20).get_page(request.GET.get("page", "1"))
    filtros = {campo: contexto[campo] for campo in ("tipo", "fecha_desde", "fecha_hasta") if contexto[campo]}
    if contexto["producto_id"]:
        filtros["producto"] = contexto["producto_id"]
    url_pagina = lambda numero: url_kardex_app(empresa, **filtros, page=numero)
    contexto = {**contexto, **{
        "modo_app": True, "app_base_url": inicio, "app_home_disponible": home,
        "app_modules_url": modulos, "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_kardex_url": url_kardex_app(empresa), "app_kardex_search_url": url_kardex_app(empresa),
        "app_kardex_back_url": modulos, "can_view_kardex": True,
        "movimientos": pagina, "page_obj": pagina, "producto_seleccionado": producto_seleccionado,
        "mobile_kardex_total": pagina.paginator.count, "mobile_kardex_page": pagina.number,
        "mobile_kardex_num_pages": pagina.paginator.num_pages,
        "mobile_kardex_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_kardex_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_kardex_items": [{
            "movimiento": movimiento,
            "origen": "lote" if movimiento.lote_kardex is not None else "general",
            "origen_label": "Lote y bodega" if movimiento.lote_kardex is not None else "Inventario general",
        } for movimiento in pagina],
    }}
    response = render(request, "facturacion/mobile_kardex_list.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
