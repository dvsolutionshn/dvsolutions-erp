"""Usuarios clínicos móviles sobre los permisos y asignaciones existentes.

El catálogo de roles es global y sólo se consulta. La única escritura adaptada
es la asignación original de UsuarioEmpresaPermiso para la empresa de la URL.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse

from .access import interfaz_clinica_activa
from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import Empresa, RolSistema, UsuarioEmpresaPermiso


def es_usuarios_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def usuarios_disponibles_app(request, empresa):
    """Mismo guard clínico, sin conceder licencia, módulos, agenda o control."""
    from .views import _puede_administrar_usuarios_clinicos

    return bool(empresa and empresa.activa and _puede_administrar_usuarios_clinicos(request.user, empresa))


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página de usuarios no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página de usuarios no válidos.")
    return numero


def autorizar_usuarios_app(request, empresa, usuario_id=None):
    if not usuarios_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para administrar usuarios de esta empresa.")
    metodos = {"GET", "HEAD", "POST"} if usuario_id is not None else {"GET", "HEAD"}
    if request.method not in metodos or request.FILES:
        raise PermissionDenied("Método no disponible para esta pantalla de usuarios.")
    permitidos_get = {"app"} if usuario_id is not None else {"app", "q", "estado", "page"}
    if (
        set(request.GET) - permitidos_get
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or ("app" in request.GET and request.GET.getlist("app") != ["1"])
    ):
        raise PermissionDenied("Parámetros de usuarios móviles inválidos.")
    if usuario_id is not None:
        identificador = _numero_canonico(usuario_id)
        segmentos = request.path.strip("/").split("/")
        if "usuarios-clinicos" in segmentos:
            indice = segmentos.index("usuarios-clinicos") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("Identificador de usuario no válido.")
    if request.method == "POST":
        if (
            set(request.POST) - {"app", "rol_sistema", "csrfmiddlewaretoken"}
            or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST)
            or request.POST.getlist("app") != ["1"]
            or len(request.POST.getlist("rol_sistema")) != 1
        ):
            raise PermissionDenied("La asignación móvil sólo admite un rol explícito para esta empresa.")
        if request.POST["rol_sistema"]:
            _numero_canonico(request.POST["rol_sistema"])
    else:
        if request.GET.getlist("app") != ["1"] or request.POST:
            raise PermissionDenied("La consulta móvil requiere app=1.")
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])
        if request.GET.get("estado", "") not in {"", "activos", "inactivos"}:
            raise PermissionDenied("Filtro de estado de usuarios no válido.")


def validar_usuarios_app(empresa, usuario_id=None):
    """Proteger sólo usuarios operativos y sus roles de la empresa consultada.

Un usuario compartido puede tener otra empresa principal o un rol legado. No
se inspeccionan ni se muestran sus asignaciones de las demás empresas.
"""
    from .views import _usuarios_operativos_de_empresa

    usuarios = _usuarios_operativos_de_empresa(empresa)
    if usuario_id is not None:
        usuarios = usuarios.filter(pk=usuario_id)
    roles = RolSistema.objects.values("pk")
    asignaciones = UsuarioEmpresaPermiso.objects.filter(empresa=empresa, usuario_id__in=usuarios.values("pk"))
    if (
        usuarios.filter(empresa_id__isnull=False).exclude(empresa_id__in=Empresa.objects.values("pk")).exists()
        or usuarios.filter(rol_sistema_id__isnull=False).exclude(rol_sistema_id__in=roles).exists()
        or asignaciones.filter(rol_sistema_id__isnull=False).exclude(rol_sistema_id__in=roles).exists()
    ):
        raise PermissionDenied("El usuario contiene una relación de empresa o rol inexistente.")


def url_usuarios_app(vista, empresa, usuario=None, **parametros):
    args = [empresa.slug] + ([usuario.pk] if usuario is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_usuarios_app(request, empresa):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = (
        inicio_app_disponible(request, empresa)
        and empresa.tiene_modulo_activo("agenda_citas")
        and (request.user.is_superuser or empresa.licencia_operativa)
    )
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    usuarios_url = url_usuarios_app("usuarios_clinicos", empresa)
    roles_url = url_usuarios_app("roles_clinicos", empresa)
    return {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_hide_agenda_navigation": not home,
        "app_hide_patient_navigation": not (request.user.is_superuser or empresa.licencia_operativa),
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_users_url": usuarios_url, "app_users_search_url": usuarios_url,
        "app_users_back_url": modulos, "app_roles_url": roles_url,
        "app_roles_search_url": roles_url, "app_roles_back_url": usuarios_url,
        "can_manage_clinical_users": True, "can_assign_clinical_role": True,
        "can_edit_global_roles": False, "can_create_users": False,
    }


def _pagina_usuarios_app(request, empresa, registros, *, roles=False):
    consulta = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    if consulta:
        termino = consulta.casefold()
        def coincide(item):
            objeto = item if roles else item["usuario"]
            campos = ("nombre", "codigo", "descripcion") if roles else ("first_name", "last_name", "username", "email")
            return any(termino in str(getattr(objeto, campo, "") or "").casefold() for campo in campos)
        registros = [item for item in registros if coincide(item)]
    if estado:
        registros = [item for item in registros if bool(item.activo if roles else item["usuario"].is_active) == (estado == "activos")]
    pagina = Paginator(registros, 20).get_page(request.GET.get("page", "1"))
    prefijo = "mobile_roles" if roles else "mobile_users"
    vista = "roles_clinicos" if roles else "usuarios_clinicos"
    filtros = {clave: valor for clave, valor in {"q": consulta, "estado": estado}.items() if valor}
    url_pagina = lambda numero: url_usuarios_app(vista, empresa, **filtros, page=numero)
    return pagina, {
        "q": consulta, "estado": estado, "page_obj": pagina,
        f"{prefijo}_query": consulta, f"{prefijo}_state": estado,
        f"{prefijo}_total": pagina.paginator.count, f"{prefijo}_page": pagina.number,
        f"{prefijo}_num_pages": pagina.paginator.num_pages,
        f"{prefijo}_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        f"{prefijo}_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    }


def render_usuarios_app(request, empresa, filas):
    from .mobile_user_permissions import permisos_usuario_editables_app, url_editar_permisos_usuario_app

    for fila in filas:
        fila["permisos_url"] = url_usuarios_app("usuario_clinico_permisos", empresa, fila["usuario"])
        fila["permissions_edit_url"] = (
            url_editar_permisos_usuario_app(empresa, fila["usuario"])
            if permisos_usuario_editables_app(request, empresa, fila["rol"]) else ""
        )
    pagina, paginacion = _pagina_usuarios_app(request, empresa, filas)
    contexto = {**_contexto_usuarios_app(request, empresa), **paginacion, "filas": pagina, "total_usuarios": len(filas)}
    return respuesta_usuarios_app(render(request, "core/mobile_users_list.html", contexto))


def render_roles_app(request, empresa, roles):
    from .clinical_role_details import contexto_detalles_roles
    from .views import _permisos_visibles_rol

    registros = list(roles.order_by("nombre", "pk"))
    pagina, paginacion = _pagina_usuarios_app(request, empresa, registros, roles=True)
    detalles = contexto_detalles_roles(empresa, pagina)
    contexto = {
        **_contexto_usuarios_app(request, empresa), **paginacion, **detalles,
        "roles": pagina, "total_roles": len(registros),
        "mobile_role_items": [
            {"rol": rol, "permisos": _permisos_visibles_rol(rol),
             "detalle": detalles.get("clinical_role_details", {}).get(str(rol.pk))}
            for rol in pagina
        ],
    }
    return respuesta_usuarios_app(render(request, "core/mobile_roles_list.html", contexto))


def render_usuario_permisos_app(request, empresa, contexto):
    from .clinical_role_details import contexto_detalles_roles
    from .mobile_user_permissions import permisos_usuario_editables_app, url_editar_permisos_usuario_app

    usuario = contexto["usuario_gestionado"]
    rol_actual = contexto["rol_actual"]
    actual_en_catalogo = bool(rol_actual is not None and contexto["roles"].filter(pk=rol_actual.pk).exists())
    url = url_usuarios_app("usuario_clinico_permisos", empresa, usuario)
    contexto = {
        **contexto, **_contexto_usuarios_app(request, empresa),
        **contexto_detalles_roles(empresa, contexto["roles"], rol_actual, usuario),
        "app_user_permissions_url": url, "app_user_permissions_form_url": url,
        "app_user_permissions_back_url": url_usuarios_app("usuarios_clinicos", empresa),
        "mobile_user_role_in_choices": actual_en_catalogo,
        "mobile_user_role_requires_selection": bool(rol_actual is not None and not actual_en_catalogo),
        "can_edit_user_clinical_permissions": permisos_usuario_editables_app(request, empresa, rol_actual),
        "app_user_permissions_edit_url": (
            url_editar_permisos_usuario_app(empresa, usuario)
            if permisos_usuario_editables_app(request, empresa, rol_actual) else ""
        ),
    }
    return respuesta_usuarios_app(render(request, "core/mobile_user_permissions.html", contexto))


def respuesta_usuarios_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
