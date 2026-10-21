"""Presentación móvil del catálogo original de tratamientos de agenda.

No es el registro de tratamientos de pacientes ni otro formulario/guardado.
Las tres vistas conservan sus parsers, escrituras y auditoría originales.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa, permiso_clinica_desde_ruta
from core.clinical_permissions import (
    EMPRESAS_CLINICAS_CON_ROLES, permiso_clinica_granular, rol_clinico_granular,
)
from crm.models import OpcionServicioAgenda


def es_catalogo_agenda_app(request, *, ruta_accion=False):
    catalogos = request.GET.getlist("catalogo") + request.POST.getlist("catalogo")
    app = "1" in request.GET.getlist("app") or "1" in request.POST.getlist("app")
    return bool(
        "agenda" in catalogos or (app and (
            ruta_accion or "catalogo" in request.GET or "catalogo" in request.POST
            or "crear_opcion_agenda" in request.POST.getlist("accion")
        ))
    )


def _base_disponible(request, empresa):
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and usuario.is_authenticated and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    )


def _permiso_ruta(request, empresa, metodo):
    # El mapper original trata HEAD como edición. No convertirlo a GET ni
    # ampliar permisos del catálogo por adaptar la presentación.
    permiso = (permiso_clinica_granular("tratamientos", metodo)
               if rol_clinico_granular(request.user, empresa)
               else permiso_clinica_desde_ruta("tratamientos"))
    return request.user.tiene_permiso_erp(permiso, empresa)


def catalogo_agenda_disponible_app(request, empresa):
    """Capacidades puras; no consultan opciones ni crean catálogos."""
    from .views import _puede_administrar_catalogo_clinico

    if not _base_disponible(request, empresa):
        return {"ver": False, "administrar": False}
    return {
        "ver": _permiso_ruta(request, empresa, "GET"),
        "administrar": _permiso_ruta(request, empresa, "POST")
            and _puede_administrar_catalogo_clinico(request.user, empresa),
    }


def _numero_canonico(valor):
    texto = str(valor)
    if (not texto or not texto.isascii() or not texto.isdecimal()
            or texto.startswith("0") or len(texto) > 19
            or int(texto) > 9223372036854775807):
        raise PermissionDenied("Identificador o página del catálogo no válidos.")
    return int(texto)


def autorizar_catalogo_agenda_app(request, empresa, *, accion=None, opcion_id=None):
    from .views import _puede_administrar_catalogo_clinico

    if not (_base_disponible(request, empresa)
            and _permiso_ruta(request, empresa, request.method)):
        raise PermissionDenied("No tiene permiso para esta acción del catálogo de agenda.")
    metodos = {"POST"} if accion else {"GET", "HEAD"}
    if request.method not in metodos or request.FILES:
        raise PermissionDenied("Método o archivos no disponibles en el catálogo móvil.")
    if accion and not _puede_administrar_catalogo_clinico(request.user, empresa):
        raise PermissionDenied("Sólo los administradores autorizados administran este catálogo.")
    permitidos_get = {"app", "catalogo"} | ({"q", "estado", "page"} if not accion else set())
    if (set(request.GET) - permitidos_get
            or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
            or request.GET.getlist("app") != ["1"]
            or request.GET.getlist("catalogo") != ["agenda"]):
        raise PermissionDenied("Parámetros del catálogo de agenda móvil inválidos.")
    if accion:
        permitidos_post = {"app", "csrfmiddlewaretoken"}
        if accion == "crear":
            permitidos_post |= {"accion", "nombre"}
        elif accion == "editar":
            permitidos_post |= {"nombre", "activo"}
        elif accion != "eliminar":
            raise PermissionDenied("Acción del catálogo móvil no disponible.")
        if (set(request.POST) - permitidos_post
                or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST)
                or request.POST.getlist("app") != ["1"]
                or (accion == "crear" and request.POST.getlist("accion") != ["crear_opcion_agenda"])):
            raise PermissionDenied("Datos del catálogo de agenda móvil inválidos.")
    else:
        if request.POST or request.GET.get("estado", "") not in {"", "activos", "inactivos"}:
            raise PermissionDenied("Filtro de estado o datos de consulta no válidos.")
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])
    if opcion_id is not None:
        identificador = _numero_canonico(opcion_id)
        partes = request.path.rstrip("/").split("/")
        if len(partes) < 2 or partes[-2] != str(identificador):
            raise PermissionDenied("El identificador del catálogo en la ruta no es canónico.")
        # Selección propia antes de cualquier consulta del resto del catálogo.
        get_object_or_404(OpcionServicioAgenda, pk=identificador,
                          empresa=empresa, categoria="tratamientos")
    validar_relaciones_catalogo_agenda_app(empresa)


def validar_relaciones_catalogo_agenda_app(empresa):
    opciones = OpcionServicioAgenda.objects.filter(empresa=empresa, categoria="tratamientos")
    if opciones.filter(creado_por_id__isnull=False).exclude(
        creado_por_id__in=get_user_model().objects.values("pk"),
    ).exists():
        raise PermissionDenied("El catálogo contiene una relación de autor inexistente.")


def url_catalogo_agenda_app(empresa, opcion=None, *, eliminar=False, **filtros):
    vista = "clinica_tratamientos"
    args = [empresa.slug]
    if opcion is not None:
        vista = "clinica_tratamiento_agenda_eliminar" if eliminar else "clinica_tratamiento_agenda_editar"
        args.append(opcion.pk)
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', 'catalogo': 'agenda', **filtros})}"


def _contexto_catalogo(request, empresa):
    from crm.views import _proteger_agenda_mobile
    from .mobile_configuration import config_disponible_app

    permisos = catalogo_agenda_disponible_app(request, empresa)
    puede_modulos = _proteger_agenda_mobile(request, empresa) is None
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug]) if puede_modulos else ""
    puede_configurar = config_disponible_app(request, empresa)["hub"]
    configuracion = f"{reverse('clinica_configuracion', args=[empresa.slug])}?app=1" if puede_configurar else ""
    propia = url_catalogo_agenda_app(empresa)
    identidad = SimpleNamespace(
        pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre, slug=empresa.slug,
        tipo_solucion=empresa.tipo_solucion, activa=empresa.activa, logo=empresa.logo,
    )
    return {
        "empresa": identidad, "empresa_original": identidad,
        "app_home_disponible": False, "app_base_url": "",
        "can_view_agenda_catalog": permisos["ver"],
        "can_manage_agenda_catalog": permisos["administrar"],
        "can_view_app_modules": puede_modulos, "app_modules_url": modulos,
        "can_view_clinical_configuration": puede_configurar,
        "app_clinical_configuration_url": configuracion,
        "app_agenda_catalog_url": propia, "app_agenda_catalog_create_url": propia,
        "app_agenda_catalog_search_url": propia,
        "app_agenda_catalog_back_url": configuracion or modulos or propia,
    }


def _sin_cache(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def render_catalogo_agenda_app(request, empresa, *, modo="", opcion=None, status=200):
    contexto = _contexto_catalogo(request, empresa)
    opciones = OpcionServicioAgenda.objects.filter(empresa=empresa, categoria="tratamientos")
    busqueda, estado = request.GET.get("q", "").strip(), request.GET.get("estado", "")
    if busqueda:
        opciones = opciones.filter(nombre__icontains=busqueda)
    if estado:
        opciones = opciones.filter(activo=estado == "activos")
    pagina = Paginator(opciones.order_by("orden", "nombre", "id"), 20).get_page(request.GET.get("page", "1"))
    filtros = {"q": busqueda, "estado": estado}

    def item(registro):
        return {
            "id": registro.pk, "opcion": registro,
            "edit_url": url_catalogo_agenda_app(empresa, registro) if contexto["can_manage_agenda_catalog"] else "",
            "delete_url": url_catalogo_agenda_app(empresa, registro, eliminar=True) if contexto["can_manage_agenda_catalog"] else "",
        }

    contexto.update({
        "opciones_agenda": pagina, "page_obj": pagina,
        "mobile_agenda_catalog_items": [item(registro) for registro in pagina],
        "mobile_agenda_catalog_query": busqueda, "mobile_agenda_catalog_state": estado,
        "mobile_agenda_catalog_page": pagina.number,
        "mobile_agenda_catalog_num_pages": pagina.paginator.num_pages,
        "mobile_agenda_catalog_total": pagina.paginator.count,
        "mobile_agenda_catalog_previous_url": url_catalogo_agenda_app(empresa, page=pagina.previous_page_number(), **filtros) if pagina.has_previous() else "",
        "mobile_agenda_catalog_next_url": url_catalogo_agenda_app(empresa, page=pagina.next_page_number(), **filtros) if pagina.has_next() else "",
        "mobile_agenda_catalog_mode": modo,
        "mobile_agenda_catalog_edit_id": opcion.pk if opcion is not None else None,
        "mobile_agenda_catalog_edit_item": item(opcion) if opcion is not None else None,
        "mobile_agenda_catalog_bound": bool(modo),
        "mobile_agenda_catalog_values": {
            "nombre": request.POST.get("nombre", "") if modo else "",
            "activo": request.POST.get("activo") == "on" if modo == "editar" else True,
        },
        "app_agenda_catalog_form_url": url_catalogo_agenda_app(empresa, opcion),
    })
    return _sin_cache(render(request, "clinica/mobile_agenda_catalog.html", contexto, status=status))


def volver_catalogo_agenda_app(empresa):
    return _sin_cache(redirect(url_catalogo_agenda_app(empresa)))
