"""Consulta del catálogo original, sin formularios ni lectura de existencias.

Los filtros y resúmenes permanecen en productos_facturacion. Este adaptador
protege sus relaciones y presenta solamente datos comerciales autorizados.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .models import (
    CategoriaProductoFarmaceutico, EMPRESAS_PRECIO_FINAL_CON_IMPUESTO,
    PerfilFarmaceuticoProducto, TipoImpuesto,
)


def es_productos_app(request, empresa):
    """Fuera de las cuatro clínicas se conserva la interfaz web original."""
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and ("app" in request.GET or "app" in request.POST)
    )


def productos_disponibles_app(request, empresa):
    """Permiso original del catálogo, sin consultar productos o inventario."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_productos", empresa)
    )


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página del catálogo no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página del catálogo no válidos.")
    return numero


def autorizar_productos_app(request, empresa):
    if not productos_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar el catálogo de esta empresa.")
    if request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("El catálogo móvil sólo permite consulta.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("La consulta del catálogo no admite cuerpo o archivos.")
    if (
        set(request.GET) - {"app", "q", "categoria", "tipo", "page"}
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros del catálogo móvil inválidos.")
    categoria = request.GET.get("categoria", "").strip()
    if categoria:
        _numero_canonico(categoria)
    if "page" in request.GET:
        _numero_canonico(request.GET["page"])
    # La normalización del tipo y la búsqueda conservan el parser original.


def validar_relaciones_productos_app(empresa, productos, categoria_id=""):
    """Revisar todo el catálogo original antes de sus filtros y JOINs.

Un perfil o impuesto histórico inactivo sigue siendo válido. No se consultan
inventarios, lotes, bodegas, costes históricos ni documentos financieros.
"""
    categoria = get_object_or_404(
        CategoriaProductoFarmaceutico, pk=_numero_canonico(categoria_id), empresa=empresa,
    ) if categoria_id else None
    base = productos.select_related(None).order_by()
    perfiles = PerfilFarmaceuticoProducto.objects.filter(producto_id__in=base.values("pk"))
    if (
        base.filter(impuesto_predeterminado_id__isnull=False).exclude(
            impuesto_predeterminado_id__in=TipoImpuesto.objects.values("pk"),
        ).exists()
        or perfiles.exclude(empresa_id=empresa.pk).exists()
        or perfiles.filter(categoria_id__isnull=False).exclude(
            categoria_id__in=CategoriaProductoFarmaceutico.objects.filter(empresa=empresa).values("pk"),
        ).exists()
    ):
        raise PermissionDenied("El catálogo contiene una relación incompatible con esta empresa.")
    return categoria


def url_productos_app(empresa, **parametros):
    ruta = reverse("productos_facturacion", args=[empresa.slug])
    return f"{ruta}?{urlencode({'app': '1', **parametros})}"


def render_productos_app(request, empresa, productos, resumen, q, categoria_id, tipo_item, categoria_seleccionada=None):
    from .forms import EliminarProductoForm
    from .mobile_product_management import gestion_productos_disponible_app, url_gestion_producto_app
    from .mobile_stock_operations import acciones_stock_app, url_stock_app

    permisos_gestion = gestion_productos_disponible_app(request, empresa)
    acciones_stock = acciones_stock_app(request, empresa)
    pagina = Paginator(productos.order_by("nombre", "id"), 20).get_page(request.GET.get("page", "1"))
    filtros = {"q": q, "categoria": categoria_id, "tipo": tipo_item}
    url_pagina = lambda numero: url_productos_app(empresa, **filtros, page=numero)
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    items, filas = [], []
    for producto in pagina:
        perfil = getattr(producto, "perfil_farmaceutico", None)
        item = {
            "id": producto.pk, "nombre": producto.nombre, "codigo": producto.codigo or "",
            "tipo_label": producto.get_tipo_item_display(),
            "unidad_label": producto.get_unidad_medida_display(),
            "precio": producto.precio, "activo": producto.activo,
            "descripcion": producto.descripcion or "",
            "categoria": perfil.categoria.nombre if perfil and perfil.categoria_id else "",
            "principio_activo": perfil.principio_activo or "" if perfil else "",
            "controlado": bool(perfil and perfil.producto_controlado),
            "refrigerado": bool(perfil and perfil.requiere_refrigeracion),
            "impuesto_label": producto.impuesto_predeterminado.nombre if producto.impuesto_predeterminado_id else "",
        }
        items.append(item)
        filas.append({"item": item, "actions": {
            "edit_url": url_gestion_producto_app("editar_producto_facturacion", empresa, producto) if permisos_gestion["editar"] else "",
            "delete_url": url_gestion_producto_app("eliminar_producto_facturacion", empresa, producto) if permisos_gestion["eliminar"] else "",
            "delete_form": EliminarProductoForm(auto_id=f"productDelete{producto.pk}_%s") if permisos_gestion["eliminar"] else None,
            "stock_entry_url": url_stock_app(empresa, producto=producto.pk) if acciones_stock and producto.activo and producto.controla_inventario else "",
        }})
    # Los bucles del contexto también reciben la proyección, no los modelos
    # completos. La paginación conserva sus índices y totales originales.
    pagina.object_list = items
    contexto = {
        "empresa": {"nombre": empresa.nombre, "slug": empresa.slug},
        "modo_app": True, "app_home_disponible": False,
        "app_hide_agenda_navigation": True, "app_hide_patient_navigation": True,
        "can_view_products": True, "can_view_app_modules": True,
        "can_create_product": permisos_gestion["crear"], "can_edit_product": permisos_gestion["editar"],
        "can_delete_product": permisos_gestion["eliminar"],
        "app_product_create_url": url_gestion_producto_app("crear_producto_facturacion", empresa) if permisos_gestion["crear"] else "",
        "app_modules_url": modulos, "app_products_back_url": modulos,
        "app_products_url": url_productos_app(empresa),
        "app_products_search_url": url_productos_app(empresa),
        "productos": pagina, "page_obj": pagina, "mobile_product_items": items,
        "mobile_product_rows": filas,
        "resumen": resumen, "q": q, "categoria_id": categoria_id, "tipo_item": tipo_item,
        "categorias_farmaceuticas": CategoriaProductoFarmaceutico.objects.filter(
            empresa=empresa, activa=True,
        ).order_by("nombre"),
        "categoria_seleccionada": categoria_seleccionada,
        "mobile_product_price_includes_tax": empresa.slug in EMPRESAS_PRECIO_FINAL_CON_IMPUESTO,
        "mobile_product_types": [{
            "codigo": codigo, "nombre": nombre, "activo": tipo_item == codigo,
            "url": url_productos_app(empresa, q=q, categoria=categoria_id, tipo=codigo),
        } for codigo, nombre in (("todos", "Todo el catálogo"), ("producto", "Productos"), ("servicio", "Servicios"))],
        "mobile_product_query": q, "mobile_product_total": pagina.paginator.count,
        "mobile_product_page": pagina.number, "mobile_product_num_pages": pagina.paginator.num_pages,
        "mobile_product_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_product_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        **acciones_stock,
    }
    response = render(request, "facturacion/mobile_product_catalog.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
