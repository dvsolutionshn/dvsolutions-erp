"""Consulta móvil de cartera usando exclusivamente el reporte financiero original.

Este adaptador no calcula saldos, modifica documentos ni inicializa catálogos.
Las guardas se ejecutan antes de que el helper original consulte sus relaciones.
"""

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from core.models import Empresa
from .mobile_receipts import _validar_pagos_app
from .models import Cliente, Factura, LineaFactura, NotaCredito, PagoFactura, Producto, TipoImpuesto


def es_cxc_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def cxc_disponible_app(request, empresa):
    """Capacidad existente; no consulta cartera ni concede otros permisos."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_cxc", empresa)
    )


def _identificador(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página de cartera inválidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página de cartera inválidos.")
    return numero


def autorizar_cxc_app(request, empresa):
    if not cxc_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar cuentas por cobrar en esta empresa.")
    if request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("La cartera móvil es únicamente de consulta.")
    if (set(request.GET) - {"app", "q", "cliente", "page"}
            or request.GET.getlist("app") != ["1"]
            or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
            or request.FILES):
        raise PermissionDenied("Parámetros de cartera móvil inválidos.")
    if "page" in request.GET:
        _identificador(request.GET["page"])
    if request.GET.get("cliente"):
        cliente_id = _identificador(request.GET["cliente"])
        get_object_or_404(Cliente, pk=cliente_id, empresa=empresa)


def _validar_lineas_cxc_app(empresa, factura):
    lineas = LineaFactura.objects.filter(factura=factura)
    if (lineas.exclude(impuesto_id__in=TipoImpuesto.objects.values("pk")).exists()
            or lineas.filter(producto_id__isnull=False).exclude(producto_id__in=Producto.objects.values("pk")).exists()):
        raise PermissionDenied("Las líneas de la factura contienen una relación inexistente.")
    extranjeras = lineas.filter(producto_id__isnull=False).exclude(
        producto_id__in=Producto.objects.filter(empresa=empresa).values("pk"),
    )
    if not extranjeras.exists():
        return

    # La conversión original de GA conserva productos del catálogo compartido
    # Hospital/Medical Spa. No exigir empresa local universalmente ni replicarlos.
    from clinica.catalogo_gastos_adicionales import producto_permitido_gasto
    from clinica.models import GastoAdicional

    gasto = GastoAdicional.objects.filter(factura=factura).first()
    if gasto is None or gasto.empresa_id != empresa.pk:
        raise PermissionDenied("El origen clínico de la factura no corresponde a esta empresa.")
    if Producto.objects.filter(pk__in=extranjeras.values("producto_id")).exclude(empresa_id__in=Empresa.objects.values("pk")).exists():
        raise PermissionDenied("El producto contiene una empresa inexistente.")
    for linea in extranjeras.select_related("producto", "producto__empresa"):
        producto = linea.producto
        if producto.controla_inventario or not producto_permitido_gasto(empresa, producto):
            raise PermissionDenied("El producto no pertenece al catálogo autorizado de esta factura.")


def validar_relaciones_cxc_app(empresa):
    """Validar todo el alcance original antes de búsqueda, saldos o paginación."""
    facturas = Factura.objects.filter(empresa=empresa, estado="emitida")
    if (facturas.exclude(cliente_id__in=Cliente.objects.filter(empresa=empresa).values("pk")).exists()
            or facturas.filter(vendedor_id__isnull=False).exclude(vendedor_id__in=get_user_model().objects.values("pk")).exists()):
        raise PermissionDenied("Las relaciones de la factura no corresponden a esta empresa.")
    _validar_pagos_app(PagoFactura.objects.filter(factura_id__in=facturas.values("pk")), empresa)
    notas = NotaCredito.objects.filter(factura_origen_id__in=facturas.values("pk"), estado="emitida")
    if notas.exclude(empresa_id=empresa.pk, cliente_id=F("factura_origen__cliente_id")).exists():
        raise PermissionDenied("Las notas de crédito aplicadas no corresponden a esta factura y empresa.")
    for factura in facturas.order_by("pk"):
        _validar_lineas_cxc_app(empresa, factura)


def url_cxc_app(empresa, **parametros):
    return f"{reverse('reporte_cxc', args=[empresa.slug])}?{urlencode({'app': '1', **parametros})}"


def render_cxc_app(request, empresa, contexto):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    q = contexto["q"]
    parametros = {"q": q} if q else {}
    listado = url_cxc_app(empresa, **parametros)
    seleccionado = contexto["cliente_seleccionado"]
    registros = (contexto["facturas_pendientes_cliente"] or []) if seleccionado else contexto["data"]
    if seleccionado:
        parametros["cliente"] = seleccionado.pk
    pagina = Paginator(registros, 20).get_page(request.GET.get("page", "1"))
    contexto = {**contexto, "modo_app": True, "app_home_disponible": home,
        "app_base_url": inicio, "app_modules_url": modulos,
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_cxc_url": url_cxc_app(empresa), "app_cxc_search_url": url_cxc_app(empresa, **({"cliente": seleccionado.pk} if seleccionado else {})),
        "app_cxc_back_url": listado if seleccionado else modulos,
        "can_view_cxc": True, "mobile_cxc_query": q,
        "mobile_cxc_client_id": seleccionado.pk if seleccionado else None,
        "mobile_cxc_items": [] if seleccionado else [{"item": item, "detail_url": url_cxc_app(empresa, **({"q": q} if q else {}), cliente=item["cliente"].pk)} for item in pagina],
        "mobile_cxc_documents": [{"factura": factura} for factura in pagina] if seleccionado else [],
        "mobile_cxc_total": pagina.paginator.count, "mobile_cxc_page": pagina.number,
        "mobile_cxc_num_pages": pagina.paginator.num_pages,
        "mobile_cxc_previous_url": url_cxc_app(empresa, **parametros, page=pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_cxc_next_url": url_cxc_app(empresa, **parametros, page=pagina.next_page_number()) if pagina.has_next() else "",
    }
    template = "facturacion/mobile_cxc_detail.html" if seleccionado else "facturacion/mobile_cxc_list.html"
    response = render(request, template, contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
