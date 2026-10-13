"""Consulta móvil de seguimientos existentes, sin programar ni sincronizar citas.

El permiso conserva el de la ruta original. Sólo se adapta la presentación del
queryset existente; sus operaciones de escritura siguen fuera de este bloque.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import ProfesionalSalud, ServicioClinico


def es_seguimientos_app(request, empresa):
    """Fuera de las cuatro clínicas, conservar la interfaz web original."""
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and ("app" in request.GET or "app" in request.POST)
    )


def seguimientos_disponibles_app(request, empresa):
    """Capacidad original de la ruta, sin consultar pacientes o citas."""
    usuario = request.user
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return False
    permiso = "puede_crear_recordatorios_paciente" if rol_clinico_granular(usuario, empresa) else "puede_pacientes"
    return usuario.tiene_permiso_erp(permiso, empresa)


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página de seguimientos no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página de seguimientos no válidos.")
    return numero


def autorizar_seguimientos_app(request, empresa, paciente_id):
    if not seguimientos_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar seguimientos de esta empresa.")
    # Denegar escrituras antes de leer el paciente, el historial o los catálogos.
    if request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("Los seguimientos móviles sólo permiten consulta.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("La consulta de seguimientos no admite un cuerpo de datos.")
    if (
        set(request.GET) - {"app", "q", "page"}
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros de seguimientos móviles inválidos.")
    identificador = _numero_canonico(paciente_id)
    segmentos = request.path.strip("/").split("/")
    if "pacientes" in segmentos:
        indice = segmentos.index("pacientes") + 1
        if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
            raise PermissionDenied("Identificador de paciente no válido.")
    if "page" in request.GET:
        _numero_canonico(request.GET["page"])


def validar_relaciones_seguimientos_app(empresa, paciente, recordatorios):
    # Validar todos los recordatorios del paciente antes de filtros, páginas o
    # JOINs; no ocultar una fila importada con empresa o relaciones incorrectas.
    base = recordatorios.select_related(None).order_by()
    if (
        base.exclude(empresa_id=empresa.pk, paciente_id=paciente.pk).exists()
        or base.filter(profesional_id__isnull=False).exclude(
            profesional_id__in=ProfesionalSalud.objects.filter(empresa=empresa).values("pk")
        ).exists()
        or base.filter(servicio_id__isnull=False).exclude(
            servicio_id__in=ServicioClinico.objects.filter(empresa=empresa).values("pk")
        ).exists()
    ):
        raise PermissionDenied("Los seguimientos contienen una relación incompatible con esta empresa.")


def url_seguimientos_app(empresa, paciente, **parametros):
    ruta = reverse("clinica_seguimientos_paciente", args=[empresa.slug, paciente.pk])
    return f"{ruta}?{urlencode({'app': '1', **parametros})}"


def render_seguimientos_app(request, empresa, paciente, recordatorios):
    from .mobile_patients import contexto_pacientes_app

    validar_relaciones_seguimientos_app(empresa, paciente, recordatorios)
    consulta = request.GET.get("q", "").strip()
    if consulta:
        recordatorios = recordatorios.filter(
            Q(tratamiento_recordatorio__icontains=consulta) | Q(motivo__icontains=consulta)
            | Q(observaciones__icontains=consulta) | Q(profesional__nombre__icontains=consulta)
        )
    pagina = Paginator(recordatorios, 20).get_page(request.GET.get("page", "1"))
    contexto = contexto_pacientes_app(request, empresa, paciente)
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    regreso = modulos
    if contexto["can_view_patient"]:
        regreso = contexto["app_patient_detail_url"]
    elif contexto["can_view_patients"]:
        regreso = contexto["app_patients_url"]
    url_pagina = lambda numero: url_seguimientos_app(empresa, paciente, **({"q": consulta} if consulta else {}), page=numero)
    contexto.update({
        "empresa": empresa,
        "paciente": {
            "id": paciente.pk, "pk": paciente.pk, "nombre": paciente.nombre,
            "expediente_codigo": paciente.expediente_codigo,
        },
        "app_home_disponible": False, "app_hide_agenda_navigation": True,
        "app_hide_patient_navigation": not contexto["can_view_patients"],
        "app_modules_url": modulos, "can_view_app_modules": True,
        "can_view_followups": True, "can_manage_followups": False,
        "app_followups_url": url_seguimientos_app(empresa, paciente),
        "app_followups_search_url": url_seguimientos_app(empresa, paciente),
        "app_followups_back_url": regreso,
        "recordatorios_tratamiento": pagina, "page_obj": pagina,
        "q": consulta, "mobile_followup_query": consulta,
        "mobile_followup_total": pagina.paginator.count,
        "mobile_followup_page": pagina.number, "mobile_followup_num_pages": pagina.paginator.num_pages,
        "mobile_followup_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_followup_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_followup_items": [{
            "id": item.pk, "tratamiento": item.tratamiento_recordatorio or item.motivo,
            "fecha_hora": item.fecha_hora, "estado": item.estado,
            "estado_label": item.get_estado_display(),
            "profesional": item.profesional.nombre if item.profesional_id else "",
            "observaciones": item.observaciones or "",
        } for item in pagina],
    })
    response = render(request, "clinica/mobile_followup_list.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
