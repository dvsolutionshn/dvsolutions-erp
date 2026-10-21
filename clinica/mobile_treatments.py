"""Consulta y presentación móvil del TratamientoPaciente existente.

No administra el catálogo de citas ni crea planes históricos o recordatorios.
La creación sigue usando TratamientoPacienteForm y el guardado de su vista.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente


def es_tratamientos_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def tratamientos_disponibles_app(request, empresa):
    """Permisos del catálogo móvil, sin consultar tratamientos o crear datos."""
    usuario = request.user
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return {"ver": False, "crear": False}
    granular = bool(rol_clinico_granular(usuario, empresa))
    return {
        "ver": usuario.tiene_permiso_erp("puede_ver_planes_tratamiento" if granular else "puede_tratamientos_clinicos", empresa),
        "crear": usuario.tiene_permiso_erp("puede_editar_planes_tratamiento" if granular else "puede_tratamientos_clinicos", empresa),
    }


def _numero_canonico(valor):
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página no válidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página no válidos.")
    return numero


def autorizar_tratamientos_app(request, empresa, crear=False, campos=None):
    if not tratamientos_disponibles_app(request, empresa)["crear" if crear else "ver"]:
        raise PermissionDenied("No tiene permiso para esta acción de tratamientos.")
    if request.method not in ({"GET", "HEAD", "POST"} if crear else {"GET", "HEAD"}):
        raise PermissionDenied("El listado móvil de tratamientos es de consulta.")
    permitidos_get = {"app", "paciente"} if crear else {"app", "q", "estado", "page"}
    if set(request.GET) - permitidos_get or any(len(request.GET.getlist(k)) != 1 for k in request.GET):
        raise PermissionDenied("Parámetros de tratamientos inválidos.")
    if "app" in request.GET and request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("Modo de app inválido.")
    if crear:
        if request.method in {"GET", "HEAD"} and "paciente" in request.GET:
            _numero_canonico(request.GET["paciente"])
        if request.method == "POST":
            campos = campos or {}
            iniciales = {
                f"initial-{nombre}" for nombre, campo in campos.items()
                if campo.show_hidden_initial
            }
            if (
                set(request.POST) - (set(campos) | iniciales | {"app", "csrfmiddlewaretoken"})
                or any(len(request.POST.getlist(k)) != 1 for k in request.POST)
                or request.POST.getlist("app") != ["1"]
            ):
                raise PermissionDenied("Datos del formulario de tratamiento inválidos.")
            for campo in ("paciente", "servicio", "profesional"):
                if request.POST.get(campo):
                    _numero_canonico(request.POST[campo])
    else:
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])
        estados = {codigo for codigo, _ in TratamientoPaciente.ESTADO_CHOICES}
        if request.GET.get("estado", "") not in estados | {""}:
            raise PermissionDenied("Estado de tratamiento inválido.")


def paciente_inicial_tratamiento_app(request, empresa):
    if request.method in {"GET", "HEAD"} and "paciente" in request.GET:
        return get_object_or_404(Paciente, pk=request.GET["paciente"], empresa=empresa, activo=True)
    return None


def url_tratamiento_app(vista, empresa, **parametros):
    return f"{reverse(vista, args=[empresa.slug])}?{urlencode({'app': '1', **parametros})}"


def _contexto_app(request, empresa):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    permisos = tratamientos_disponibles_app(request, empresa)
    granular = bool(rol_clinico_granular(request.user, empresa))
    ver_paciente = request.user.tiene_permiso_erp("puede_ver_datos_generales_paciente" if granular else "puede_pacientes", empresa)
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not granular and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_tratamiento_app("clinica_tratamientos", empresa)
    crear = url_tratamiento_app("clinica_crear_tratamiento", empresa)
    return {
        "modo_app": True, "app_base_url": inicio, "app_home_disponible": home,
        "app_modules_url": modulos, "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_treatments": permisos["ver"], "can_create_treatment": permisos["crear"],
        "can_view_patient": ver_paciente, "app_treatments_url": listado,
        "app_treatment_search_url": listado, "app_treatment_create_url": crear,
        "app_treatment_form_url": crear,
        "app_treatment_back_url": listado if permisos["ver"] else modulos,
        "app_treatment_cancel_url": listado if permisos["ver"] else modulos,
        "mobile_treatment_states": [{"codigo": codigo, "nombre": nombre} for codigo, nombre in TratamientoPaciente.ESTADO_CHOICES],
    }


def _validar_integridad_tratamientos(tratamientos, empresa):
    # Las importaciones antiguas pueden haber saltado las elecciones del
    # formulario. Se comprueban todas las filas propias, antes de filtros/página.
    if (
        tratamientos.exclude(paciente_id__in=Paciente.objects.filter(empresa=empresa).values("pk")).exists()
        or tratamientos.filter(profesional_id__isnull=False).exclude(profesional_id__in=ProfesionalSalud.objects.filter(empresa=empresa).values("pk")).exists()
        or tratamientos.filter(servicio_id__isnull=False).exclude(servicio_id__in=ServicioClinico.objects.filter(empresa=empresa).values("pk")).exists()
    ):
        raise PermissionDenied("Los datos relacionados del tratamiento no pertenecen a la empresa activa.")


def listar_tratamientos_app(request, empresa):
    tratamientos = TratamientoPaciente.objects.filter(empresa=empresa)
    _validar_integridad_tratamientos(tratamientos, empresa)
    consulta, estado = request.GET.get("q", "").strip(), request.GET.get("estado", "")
    if consulta:
        tratamientos = tratamientos.filter(Q(nombre__icontains=consulta) | Q(paciente__nombre__icontains=consulta) | Q(servicio__nombre__icontains=consulta))
    if estado:
        tratamientos = tratamientos.filter(estado=estado)
    pagina = Paginator(tratamientos.select_related("paciente", "profesional", "servicio").order_by("-fecha_inicio", "-id"), 20).get_page(request.GET.get("page", "1"))
    contexto = {"empresa": empresa, "tratamientos": pagina, **_contexto_app(request, empresa)}
    parametros = {key: value for key, value in (("q", consulta), ("estado", estado)) if value}
    url_pagina = lambda numero: url_tratamiento_app("clinica_tratamientos", empresa, **parametros, page=numero)
    contexto.update({
        "mobile_treatment_query": consulta, "mobile_treatment_state": estado,
        "mobile_treatment_items": [{
            "tratamiento": item, "paciente": item.paciente, "nombre": item.nombre,
            "estado": item.estado, "estado_label": item.get_estado_display(),
            "patient_url": f"{reverse('clinica_paciente_detalle', args=[empresa.slug, item.paciente_id])}?app=1" if contexto["can_view_patient"] else "",
        } for item in pagina],
        "mobile_treatment_pagination": [{"numero": numero, "url": url_pagina(numero), "active": numero == pagina.number} for numero in range(max(1, pagina.number - 2), min(pagina.paginator.num_pages, pagina.number + 2) + 1)],
        "app_treatment_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "app_treatment_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    })
    return respuesta_tratamientos_app(render(request, "clinica/mobile_treatment_list.html", contexto))


def _secciones_formulario(form):
    grupos = (
        ("Paciente y equipo", ("paciente", "servicio", "profesional")),
        ("Tratamiento", ("nombre", "fecha_inicio", "fecha_fin_estimada", "estado")),
        ("Indicaciones", ("objetivo", "plan_clinico")),
        ("Monto estimado", ("monto_estimado",)),
    )
    visibles = {campo.name: campo for campo in form.visible_fields()}
    usados, secciones = set(), []
    for titulo, nombres in grupos:
        campos = [visibles[nombre] for nombre in nombres if nombre in visibles]
        if campos:
            secciones.append({"titulo": titulo, "campos": campos})
            usados.update(campo.name for campo in campos)
    restantes = [campo for nombre, campo in visibles.items() if nombre not in usados]
    if restantes:
        secciones.append({"titulo": "Datos adicionales", "campos": restantes})
    return secciones


def render_tratamiento_form_app(request, empresa, form):
    for nombre in ("fecha_inicio", "fecha_fin_estimada"):
        form.fields[nombre].widget.format = "%Y-%m-%d"
    contexto = {"empresa": empresa, "form": form, "titulo": "Nuevo tratamiento", **_contexto_app(request, empresa)}
    contexto.update({
        "mobile_treatment_sections": _secciones_formulario(form),
    })
    return respuesta_tratamientos_app(render(request, "clinica/mobile_treatment_form.html", contexto))


def respuesta_tratamientos_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def volver_tratamientos_app(request, empresa):
    vista = "clinica_tratamientos" if tratamientos_disponibles_app(request, empresa)["ver"] else "clinica_crear_tratamiento"
    return respuesta_tratamientos_app(redirect(url_tratamiento_app(vista, empresa)))
