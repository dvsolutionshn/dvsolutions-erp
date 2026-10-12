"""Presentación móvil de la bitácora de planes clínicos existente.

El formulario y su guardado siguen siendo los de planes_tratamiento_paciente.
El compositor no consulta planes anteriores ni el profesional predeterminado.
"""

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import Paciente, PlanTratamientoPaciente, ProfesionalSalud


def es_planes_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def planes_disponibles_app(request, empresa):
    """Capacidades reales del módulo, sin consultar la bitácora."""
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
    if rol_clinico_granular(usuario, empresa):
        return {
            "ver": usuario.tiene_permiso_erp("puede_ver_planes_tratamiento", empresa),
            "crear": usuario.tiene_permiso_erp("puede_editar_planes_tratamiento", empresa),
        }
    pacientes = usuario.tiene_permiso_erp("puede_pacientes", empresa)
    return {
        "ver": pacientes,
        "crear": pacientes and usuario.tiene_permiso_erp("puede_expediente_clinico", empresa),
    }


def _numero_canonico(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página no válidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página no válidos.")
    return numero


def autorizar_planes_app(request, empresa, paciente_id, compositor=False):
    crear = compositor or request.method == "POST"
    if not planes_disponibles_app(request, empresa)["crear" if crear else "ver"]:
        raise PermissionDenied("No tiene permiso para esta acción de planes clínicos.")
    metodos = {"GET", "HEAD"} if compositor else {"GET", "HEAD", "POST"}
    if request.method not in metodos:
        raise PermissionDenied("Método no disponible en los planes móviles.")
    _numero_canonico(paciente_id)
    # El convertidor de URL recibe int; conservar también la representación
    # canónica cuando la petición incluye el segmento real de pacientes.
    segmentos = request.path.strip("/").split("/")
    if "pacientes" in segmentos:
        indice = segmentos.index("pacientes") + 1
        if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != paciente_id:
            raise PermissionDenied("Identificador de paciente no válido.")
    permitidos_get = {"app"} if crear else {"app", "q", "page"}
    if (set(request.GET) - permitidos_get
            or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])):
        raise PermissionDenied("Parámetros de planes móviles inválidos.")
    if compositor and request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("Este formulario requiere el modo app.")
    if request.method == "POST":
        if (set(request.POST) - {"app", "texto", "csrfmiddlewaretoken"}
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
                or request.POST.getlist("app") != ["1"] or request.FILES):
            raise PermissionDenied("Datos del formulario de plan inválidos.")
    elif "page" in request.GET:
        _numero_canonico(request.GET["page"])


def paciente_plan_app(empresa, paciente_id):
    # No se necesitan contactos, datos clínicos ni estado activo para este flujo.
    return get_object_or_404(
        Paciente.objects.only("id", "empresa_id", "nombre", "expediente_codigo"),
        pk=paciente_id, empresa=empresa,
    )


def url_plan_app(vista, empresa, paciente, **parametros):
    return f"{reverse(vista, args=[empresa.slug, paciente.pk])}?{urlencode({'app': '1', **parametros})}"


def _contexto_plan_app(request, empresa, paciente):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    permisos = planes_disponibles_app(request, empresa)
    granular = bool(rol_clinico_granular(request.user, empresa))
    ver_paciente = request.user.tiene_permiso_erp(
        "puede_ver_datos_generales_paciente" if granular else "puede_pacientes", empresa,
    )
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not granular and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_plan_app("clinica_planes_tratamiento_paciente", empresa, paciente)
    crear = url_plan_app("clinica_plan_tratamiento_crear_app", empresa, paciente)
    return {
        "empresa": empresa, "modo_app": True,
        "paciente": {"id": paciente.pk, "pk": paciente.pk, "nombre": paciente.nombre, "expediente_codigo": paciente.expediente_codigo},
        "app_base_url": inicio, "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_plans": permisos["ver"], "can_create_plan": permisos["crear"],
        "can_view_patient": ver_paciente,
        "app_patient_detail_url": url_plan_app("clinica_paciente_detalle", empresa, paciente) if ver_paciente else "",
        "app_plans_url": listado, "app_plan_search_url": listado,
        "app_plan_create_url": crear, "app_plan_form_url": listado,
        "app_patient_plans_url": listado, "app_patient_plan_create_url": crear,
        "app_plan_back_url": listado if permisos["ver"] else modulos,
    }


def _validar_historial_planes(planes, empresa):
    # Comprobar TODO el historial del paciente antes de filtros/página. Los
    # subqueries detectan también FK huérfanas sin ocultarlas con INNER JOIN.
    if (
        planes.exclude(empresa_id=empresa.pk).exists()
        or planes.filter(profesional_id__isnull=False).exclude(profesional_id__in=ProfesionalSalud.objects.filter(empresa=empresa).values("pk")).exists()
        or planes.filter(creado_por_id__isnull=False).exclude(creado_por_id__in=get_user_model().objects.values("pk")).exists()
    ):
        raise PermissionDenied("El historial de planes contiene una relación no compatible con esta empresa.")


def listar_planes_app(request, empresa, paciente):
    planes = PlanTratamientoPaciente.objects.filter(paciente_id=paciente.pk)
    _validar_historial_planes(planes, empresa)
    consulta = request.GET.get("q", "").strip()
    if consulta:
        planes = planes.filter(Q(texto__icontains=consulta) | Q(profesional_nombre__icontains=consulta) | Q(autor_nombre__icontains=consulta))
    pagina = Paginator(planes.select_related("profesional", "creado_por").order_by("-fecha_creacion", "-id"), 20).get_page(request.GET.get("page", "1"))
    contexto = _contexto_plan_app(request, empresa, paciente)
    url_pagina = lambda numero: url_plan_app("clinica_planes_tratamiento_paciente", empresa, paciente, **({"q": consulta} if consulta else {}), page=numero)
    contexto.update({
        "planes": pagina,
        "mobile_plan_items": [{
            "plan": plan, "responsable": plan.responsable_display, "fecha": plan.fecha_creacion,
            "autor": plan.autor_nombre or (plan.creado_por.get_full_name().strip() or plan.creado_por.username if plan.creado_por_id else "Usuario no disponible"),
            "texto": plan.texto,
        } for plan in pagina],
        "mobile_plan_query": consulta, "mobile_plan_total": pagina.paginator.count,
        "mobile_plan_page": pagina.number, "mobile_plan_num_pages": pagina.paginator.num_pages,
        "mobile_plan_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_plan_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    })
    return respuesta_planes_app(render(request, "clinica/mobile_plan_list.html", contexto))


def render_plan_form_app(request, empresa, paciente, form):
    contexto = {**_contexto_plan_app(request, empresa, paciente), "form": form, "titulo": "Agregar plan clínico"}
    return respuesta_planes_app(render(request, "clinica/mobile_plan_form.html", contexto))


def respuesta_planes_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def volver_planes_app(request, empresa, paciente):
    vista = "clinica_planes_tratamiento_paciente" if planes_disponibles_app(request, empresa)["ver"] else "clinica_plan_tratamiento_crear_app"
    return respuesta_planes_app(redirect(url_plan_app(vista, empresa, paciente)))
