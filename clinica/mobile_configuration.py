"""Presentación móvil de la configuración y los catálogos clínicos existentes.

Las vistas originales conservan sus formularios, guardados y auditoría. Este
módulo sólo prepara permisos, navegación, datos de consulta y respuestas app.
"""

import re
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import CitaClinica, ProfesionalSalud, ServicioClinico, TratamientoPaciente


def es_configuracion_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def config_disponible_app(request, empresa):
    """Permisos existentes, sin consultar ni crear registros de los catálogos."""
    claves = ("hub", "profesionales", "editar_profesionales", "servicios", "editar_servicios", "eliminar_servicios")
    usuario = request.user
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return dict.fromkeys(claves, False)
    from .views import _puede_administrar_catalogo_clinico, _puede_administrar_servicios_clinicos

    configurar = usuario.tiene_permiso_erp("puede_configuracion_clinica", empresa)
    servicio_admin = configurar and _puede_administrar_servicios_clinicos(usuario, empresa)
    return {
        # El hub legado no tiene permiso de ruta. La excepción granular de
        # administración de manuales es la misma que admite el middleware web.
        "hub": not rol_clinico_granular(usuario, empresa) or configurar
            or usuario.tiene_permiso_erp("puede_administrar_manuales_pdf", empresa),
        "profesionales": configurar,
        "editar_profesionales": configurar and _puede_administrar_catalogo_clinico(usuario, empresa),
        "servicios": configurar,
        "editar_servicios": servicio_admin,
        "eliminar_servicios": servicio_admin,
    }


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página no válidos.")
    return numero


def autorizar_configuracion_app(request, empresa, catalogo="hub", *, editar=False, eliminar=False, objeto_id=None, campos=None):
    permisos = config_disponible_app(request, empresa)
    clave = "eliminar_servicios" if eliminar else f"editar_{catalogo}" if editar else catalogo
    if not permisos.get(clave, False):
        raise PermissionDenied("No tiene permiso para esta acción de configuración clínica.")
    metodos = {"POST"} if eliminar else {"GET", "HEAD"} if catalogo == "hub" else {"GET", "HEAD", "POST"}
    if request.method not in metodos:
        raise PermissionDenied("Método no disponible en la configuración móvil.")
    if objeto_id is not None:
        numero = _numero_canonico(objeto_id)
        segmentos = request.path.strip("/").split("/")
        if catalogo in segmentos:
            indice = segmentos.index(catalogo) + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != numero:
                raise PermissionDenied("Identificador del catálogo no válido.")
    permitidos_get = {"app"}
    if catalogo != "hub" and not editar and not eliminar and request.method in {"GET", "HEAD"}:
        permitidos_get.update({"q", "estado", "page"})
    if (set(request.GET) - permitidos_get
            or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])):
        raise PermissionDenied("Parámetros de configuración móvil inválidos.")
    if request.FILES:
        raise PermissionDenied("Este catálogo no admite archivos.")
    if request.method == "POST":
        permitidos_post = {"app", "csrfmiddlewaretoken"}
        if campos:
            permitidos_post.update(campos)
            permitidos_post.update(f"initial-{nombre}" for nombre, campo in campos.items() if campo.show_hidden_initial)
        if (set(request.POST) - permitidos_post
                or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST)
                or request.POST.getlist("app") != ["1"]):
            raise PermissionDenied("Datos del catálogo móvil inválidos.")
    else:
        if request.GET.getlist("app") != ["1"]:
            raise PermissionDenied("Esta pantalla requiere el modo app.")
        if request.GET.get("estado", "") not in {"", "activos", "inactivos"}:
            raise PermissionDenied("Filtro de estado no válido.")
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])


def validar_profesionales_app(profesionales):
    # Un usuario puede pertenecer a varias empresas. Sólo se comprueba que la
    # relación opcional exista; su empresa principal no es una restricción nueva.
    if profesionales.filter(usuario_id__isnull=False).exclude(
        usuario_id__in=get_user_model().objects.values("pk")
    ).exists():
        raise PermissionDenied("El profesional contiene una relación de usuario no válida.")


def validar_eliminacion_servicio_app(empresa, servicio):
    from crm.models import CitaCliente

    # Son las tres relaciones inversas actuales (SET_NULL); no hay M2M al
    # servicio. Una importación incoherente no debe modificar otra empresa.
    relaciones = (
        CitaClinica.objects.filter(servicio_id=servicio.pk),
        TratamientoPaciente.objects.filter(servicio_id=servicio.pk),
        CitaCliente.objects.filter(servicio_clinico_id=servicio.pk),
    )
    if any(registros.exclude(empresa_id=empresa.pk).exists() for registros in relaciones):
        raise PermissionDenied("El servicio está relacionado con registros de otra empresa.")


def url_configuracion_app(vista, empresa, objeto=None, **parametros):
    args = [empresa.slug] + ([objeto.pk] if objeto is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_configuracion_app(request, empresa):
    from core.context_processors import erp_access
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app
    from facturacion.mobile_suppliers import proveedores_disponibles_app
    from facturacion.mobile_transfers import transferencia_disponible_app

    usuario = request.user
    permisos = config_disponible_app(request, empresa)
    acceso = erp_access(SimpleNamespace(
        user=usuario, resolver_match=SimpleNamespace(kwargs={"empresa_slug": empresa.slug}),
    ))["erp_access"]
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(usuario, empresa) and not usuario.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos_disponibles = bool(
        usuario.is_superuser or usuario.tiene_permiso_erp("puede_citas", empresa)
        or usuario.tiene_alguna_permision_facturacion_empresa(empresa)
        or usuario.tiene_alguna_permision_clinica_empresa(empresa)
        or transferencia_disponible_app(request, empresa)
        or any(proveedores_disponibles_app(request, empresa).values())
    )
    configuracion = url_configuracion_app("clinica_configuracion", empresa)
    modulos = (url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])) if modulos_disponibles else configuracion
    return {
        "empresa": empresa, "modo_app": True, "erp_access": acceso,
        "app_base_url": inicio, "app_home_disponible": home and modulos_disponibles,
        "app_modules_url": modulos, "app_modules_disponible": modulos_disponibles,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_configuration_url": configuracion, "app_configuration_back_url": modulos,
        "app_professionals_url": url_configuracion_app("clinica_profesionales", empresa),
        "app_services_url": url_configuracion_app("clinica_servicios", empresa),
        "mobile_configuration_capabilities": permisos, "can_view_configuration": permisos["hub"],
        "can_manage_professionals": permisos["profesionales"], "can_create_professional": permisos["profesionales"],
        "can_edit_professional": permisos["editar_profesionales"],
        "can_manage_services": permisos["servicios"], "can_create_service": permisos["servicios"],
        "can_edit_service": permisos["editar_servicios"], "can_delete_service": permisos["eliminar_servicios"],
    }


def render_configuracion_hub_app(request, empresa):
    from core.mobile_advanced_configuration import configuracion_avanzada_disponible_app
    from core.mobile_company import configuracion_empresa_disponible_app
    from core.mobile_users import usuarios_disponibles_app
    from crm.mobile_crm_configuration import configuracion_crm_disponible_app
    from .mobile_agenda_catalog import catalogo_agenda_disponible_app, url_catalogo_agenda_app
    from .mobile_recipe_templates import permiso_plantillas_app
    from .mobile_treatments import tratamientos_disponibles_app
    from .views import _puede_administrar_manuales_receta, _puede_controlar_enlaces_pacientes, _recetas_avanzadas_activas

    contexto = _contexto_configuracion_app(request, empresa)
    acceso = contexto["erp_access"]
    permisos = contexto["mobile_configuration_capabilities"]
    opciones = []

    def agregar(codigo, nombre, descripcion, permitido, vista=None):
        if permitido:
            opciones.append({
                "codigo": codigo, "nombre": nombre, "descripcion": descripcion,
                "estado": "disponible" if vista else "pendiente",
                "url": url_configuracion_app(vista, empresa) if vista else "",
            })

    agregar("profesionales", "Profesionales", "Configurar el personal y sus datos profesionales.", permisos["profesionales"], "clinica_profesionales")
    agregar("servicios", "Servicios clínicos", "Configurar categoría, duración, color y estado.", permisos["servicios"], "clinica_servicios")
    agregar("tratamientos", "Tratamientos de pacientes", "Consultar los tratamientos asignados; el catálogo de agenda tiene su pantalla independiente.", acceso["tratamientos_clinicos"] and tratamientos_disponibles_app(request, empresa)["ver"], "clinica_tratamientos")
    catalogo_agenda = catalogo_agenda_disponible_app(request, empresa)
    if catalogo_agenda["ver"]:
        opciones.append({
            "codigo": "catalogo_tratamientos", "nombre": "Catálogo de tratamientos de agenda",
            "descripcion": "Consultar las opciones de esta empresa; sólo sus administradores autorizados pueden modificarlas.",
            "estado": "disponible", "url": url_catalogo_agenda_app(empresa),
        })
    recetas = _recetas_avanzadas_activas(empresa)
    agregar("plantillas_recetas", "Plantillas de recetas", "Administrar medicamentos e indicaciones reutilizables.", acceso["configuracion_clinica"] and recetas and permiso_plantillas_app(request, empresa), "clinica_plantillas_recetas")
    agregar("manuales_pdf", "Manuales PDF", "Cargar, editar y activar los documentos existentes.", recetas and acceso["administrar_manuales_pdf"] and _puede_administrar_manuales_receta(request.user, empresa), "clinica_manuales_recetas")
    agregar("enlaces_registro", "Control de enlaces de registro", "Esta pantalla de administración todavía no está adaptada a móvil.", empresa.slug == "hospital_mia" and _puede_controlar_enlaces_pacientes(request.user))
    agregar("usuarios_roles", "Usuarios y permisos", "Consultar usuarios y permisos del rol; asignarlo sólo en esta empresa.", usuarios_disponibles_app(request, empresa), "usuarios_clinicos")
    if configuracion_empresa_disponible_app(request, empresa):
        opciones.append({
            "codigo": "empresa", "nombre": "Configuración de empresa",
            "descripcion": "Datos, logo, plan, licencia y módulos mediante el editor existente de superadministración.",
            "estado": "disponible",
            "url": f"{reverse('superadmin_empresa_edit', args=[empresa.pk])}?app=1",
        })
    avanzadas = configuracion_avanzada_disponible_app(request, empresa)
    if any(avanzadas.values()):
        opciones.append({
            "codigo": "configuracion_avanzada", "nombre": "Opciones avanzadas",
            "descripcion": "Inventario, bodegas, manuales y parámetros originales según permisos del administrador de Django.",
            "estado": "disponible",
            "url": (f"{reverse('configuracion_avanzada_empresa_app', args=[empresa.pk])}?app=1" if avanzadas["ver"]
                    else f"{reverse('configuracion_avanzada_empresas_app')}?app=1"),
        })
    agregar("configuracion_facturacion", "Configuración de facturación", "Adaptación móvil pendiente; no se modifican permisos ni integraciones desde esta pantalla.", acceso["configuracion_facturacion"])
    agregar("crm", "Agenda y comunicación", "Configurar plantillas, textos y avisos con el permiso CRM existente, sin editar credenciales ni enviar pruebas.",
            configuracion_crm_disponible_app(request, empresa), "crm_configuracion")
    contexto.update({"titulo": "Configuración", "recetas_avanzadas": recetas, "mobile_configuration_items": opciones})
    return respuesta_configuracion_app(render(request, "clinica/mobile_configuration_hub.html", contexto))


def _contexto_catalogo_app(request, empresa, catalogo, form, objeto=None):
    contexto = _contexto_configuracion_app(request, empresa)
    profesional = catalogo == "profesionales"
    vista_lista = "clinica_profesionales" if profesional else "clinica_servicios"
    vista_editar = "clinica_profesional_editar" if profesional else "clinica_servicio_editar"
    permisos = contexto["mobile_configuration_capabilities"]
    if not profesional:
        valor = form["color_calendario"].value()
        # ColorInput no representa valores originales vacíos o no hexadecimales.
        # Cambiar sólo el widget de esta instancia evita serializar negro nuevo.
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(valor or "")):
            widget = form.fields["color_calendario"].widget
            widget.input_type = "text"
            widget.attrs.pop("type", None)
    nombres = (
        (("Datos profesionales", ("nombre", "especialidad", "colegiacion")), ("Contacto y estado", ("telefono", "activo")))
        if profesional else
        (("Servicio", ("nombre", "categoria")), ("Agenda", ("duracion_minutos", "color_calendario")), ("Condiciones y estado", ("precio_referencia", "requiere_consentimiento", "activo")))
    )
    listado = url_configuracion_app(vista_lista, empresa)
    contexto.update({
        "form": form, "titulo": "Profesionales" if profesional else "Servicios clínicos",
        "form_titulo": ("Editar profesional" if profesional else "Editar servicio") if objeto else ("Agregar profesional" if profesional else "Agregar servicio"),
        "form_descripcion": "Se conservan los datos, las validaciones y los permisos del catálogo existente.",
        "objeto_editando": objeto, "mobile_configuration_catalog_kind": catalogo,
        "can_create_catalog": permisos[catalogo], "can_edit_catalog": permisos[f"editar_{catalogo}"],
        "can_delete_catalog": not profesional and permisos["eliminar_servicios"],
        "app_catalog_url": listado, "app_catalog_search_url": listado,
        "app_catalog_form_url": url_configuracion_app(vista_editar, empresa, objeto) if objeto else listado,
        "app_catalog_back_url": listado,
        "app_catalog_edit_url": url_configuracion_app(vista_editar, empresa, objeto) if objeto and permisos[f"editar_{catalogo}"] else "",
        "app_catalog_delete_url": url_configuracion_app("clinica_servicio_eliminar", empresa, objeto) if objeto and not profesional and permisos["eliminar_servicios"] else "",
        "mobile_configuration_form_sections": [{"titulo": titulo, "campos": [form[nombre] for nombre in campos]} for titulo, campos in nombres],
    })
    return contexto


def render_catalogo_form_app(request, empresa, catalogo, form, objeto=None):
    return respuesta_configuracion_app(render(request, "clinica/mobile_configuration_form.html", _contexto_catalogo_app(request, empresa, catalogo, form, objeto)))


def render_catalogo_app(request, empresa, catalogo, form):
    profesional = catalogo == "profesionales"
    registros = (ProfesionalSalud if profesional else ServicioClinico).objects.filter(empresa=empresa)
    if profesional:
        validar_profesionales_app(registros)
    consulta = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    if consulta:
        registros = registros.filter(Q(nombre__icontains=consulta))
    if estado:
        registros = registros.filter(activo=estado == "activos")
    pagina = Paginator(registros.order_by("nombre", "id"), 20).get_page(request.GET.get("page", "1"))
    contexto = _contexto_catalogo_app(request, empresa, catalogo, form)
    vista_editar = "clinica_profesional_editar" if profesional else "clinica_servicio_editar"
    vista_lista = "clinica_profesionales" if profesional else "clinica_servicios"
    url_pagina = lambda numero: url_configuracion_app(vista_lista, empresa, q=consulta, estado=estado, page=numero)
    contexto.update({
        "items": pagina, "q": consulta, "estado": estado,
        "mobile_catalog_items": [{
            "registro": registro,
            "editar_url": url_configuracion_app(vista_editar, empresa, registro) if contexto["can_edit_catalog"] else "",
            "eliminar_url": url_configuracion_app("clinica_servicio_eliminar", empresa, registro) if contexto["can_delete_catalog"] else "",
        } for registro in pagina],
        "mobile_catalog_query": consulta, "mobile_catalog_state": estado, "mobile_catalog_total": pagina.paginator.count,
        "mobile_catalog_page": pagina.number, "mobile_catalog_num_pages": pagina.paginator.num_pages,
        "mobile_catalog_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_catalog_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    })
    return respuesta_configuracion_app(render(request, "clinica/mobile_configuration_catalog.html", contexto))


def respuesta_configuracion_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def volver_catalogo_app(empresa, catalogo):
    vista = "clinica_profesionales" if catalogo == "profesionales" else "clinica_servicios"
    return respuesta_configuracion_app(redirect(url_configuracion_app(vista, empresa)))
