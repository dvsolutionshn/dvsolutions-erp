"""Presentación de los antecedentes adicionales y su historial existente."""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .mobile_patients import (
    autorizar_pacientes_app, contexto_pacientes_app, es_pacientes_app,
    permiso_pacientes_app, respuesta_pacientes_app,
)
from .models import AntecedenteAdicionalHistorial, AntecedenteAdicionalPaciente


def antecedentes_disponibles_app(request, empresa, paciente=None):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and empresa.tiene_modulo_activo("clinica_medica") and request.user.is_authenticated
        and request.user.puede_acceder_empresa(empresa)
        and (paciente is None or paciente.empresa_id == empresa.pk)
        and permiso_pacientes_app(request, empresa, "puede_ver_historia_clinica", "puede_expediente_clinico")
        and permiso_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico")
    )


def url_antecedentes_app(empresa, paciente):
    return f"{reverse('clinica_historial_clinico_consolidado', args=[empresa.slug, paciente.pk])}?{urlencode({'app': '1', 'formulario': 'antecedentes'})}"


def preparar_antecedentes_app(request, empresa):
    """Validar el sobre móvil; el contenido lo valida el Form original."""
    if not es_pacientes_app(request, empresa):
        return False
    query = request.GET.getlist("formulario")
    posted = request.POST.getlist("formulario")
    accion = "guardar_antecedente_adicional"
    if not ("antecedentes" in query or "antecedentes" in posted or accion in request.POST.getlist("accion")):
        return False
    if request.method == "POST":
        permitidos = {"app", "formulario", "accion", "csrfmiddlewaretoken", "antecedente_adicional-contenido"}
        if (query not in ([], ["antecedentes"]) or posted != ["antecedentes"]
                or request.POST.getlist("app") != ["1"] or request.POST.getlist("accion") != [accion]
                or set(request.POST) - permitidos or any(len(request.POST.getlist(key)) != 1 for key in request.POST)):
            raise PermissionDenied("Esta acción no está disponible en los antecedentes móviles.")
    elif request.method not in {"GET", "HEAD"} or query != ["antecedentes"]:
        raise Http404("Formulario de antecedentes móvil no válido.")
    if len(request.GET.getlist("app")) > 1:
        raise PermissionDenied("El formulario móvil contiene datos no compatibles.")
    autorizar_pacientes_app(request, empresa, "puede_ver_historia_clinica", "puede_expediente_clinico")
    autorizar_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico")
    return True


def _validar_antecedente_app(empresa, paciente, antecedente):
    if antecedente and (antecedente.empresa_id != empresa.pk or antecedente.paciente_id != paciente.pk):
        raise PermissionDenied("Los antecedentes no corresponden al paciente de esta empresa.")
    if antecedente and antecedente.profesional_id and antecedente.profesional.empresa_id != empresa.pk:
        raise PermissionDenied("El responsable de los antecedentes no corresponde a esta empresa.")


def antecedente_paciente_app(empresa, paciente):
    # No ocultar una relación OneToOne importada de otra empresa para luego
    # intentar crear un segundo registro desde el formulario móvil.
    empresa_actual = AntecedenteAdicionalPaciente.objects.filter(paciente=paciente).values_list("empresa_id", flat=True).first()
    if empresa_actual is not None and empresa_actual != empresa.pk:
        raise PermissionDenied("Los antecedentes no corresponden al paciente de esta empresa.")
    antecedente = AntecedenteAdicionalPaciente.objects.filter(empresa=empresa, paciente=paciente).select_related("profesional", "actualizado_por").first()
    _validar_antecedente_app(empresa, paciente, antecedente)
    registros = _versiones_antecedentes_app(empresa, paciente, antecedente)
    if ((not antecedente and registros.exists())
            or (antecedente and registros.exclude(empresa=empresa, paciente=paciente, antecedente=antecedente).exists())
            or registros.filter(profesional__isnull=False).exclude(profesional__empresa=empresa).exists()):
        raise PermissionDenied("El historial de antecedentes contiene una relación no compatible.")
    return antecedente


def _versiones_antecedentes_app(empresa, paciente, antecedente):
    alcance = Q(empresa=empresa, paciente=paciente)
    if antecedente:
        alcance |= Q(antecedente=antecedente)
    return AntecedenteAdicionalHistorial.objects.filter(alcance)


def _historial_antecedentes_app(empresa, paciente, antecedente):
    _validar_antecedente_app(empresa, paciente, antecedente)
    registros = _versiones_antecedentes_app(empresa, paciente, antecedente).select_related("profesional").order_by("-fecha", "-id")
    items = []
    for registro in registros:
        if (not antecedente or registro.empresa_id != empresa.pk or registro.paciente_id != paciente.pk
                or registro.antecedente_id != antecedente.pk):
            raise PermissionDenied("El historial de antecedentes contiene una relación no compatible.")
        if registro.profesional_id and registro.profesional.empresa_id != empresa.pk:
            raise PermissionDenied("El responsable del historial no corresponde a esta empresa.")
        items.append({
            "registro": registro, "anterior": registro.texto_anterior, "nuevo": registro.texto_nuevo,
            "fecha": registro.fecha, "responsable": registro.responsable_display,
        })
    return items


def render_antecedentes_app(request, empresa, paciente, form, antecedente):
    historial = _historial_antecedentes_app(empresa, paciente, antecedente)
    contexto = contexto_pacientes_app(request, empresa, paciente)
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        contexto["app_home_disponible"] = False
        contexto["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    contexto.update({
        "empresa": empresa, "paciente": paciente, "form": form, "titulo": "Antecedentes adicionales",
        "can_manage_antecedents": True, "mobile_antecedent": antecedente,
        "mobile_antecedent_history": historial,
        "app_antecedents_form_url": url_antecedentes_app(empresa, paciente),
        "app_antecedents_back_url": contexto["app_patient_history_url"],
    })
    return respuesta_pacientes_app(request, empresa, render(request, "clinica/mobile_antecedents_form.html", contexto))


def volver_antecedentes_app(request, empresa, paciente):
    return respuesta_pacientes_app(request, empresa, redirect(url_antecedentes_app(empresa, paciente)))
