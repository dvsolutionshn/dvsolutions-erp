"""Presentación móvil de las plantillas existentes, sin lógica de prescripción."""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.clinical_permissions import rol_clinico_granular
from .mobile_patients import (
    contexto_pacientes_app, es_pacientes_app, respuesta_pacientes_app,
)


es_plantillas_app = es_pacientes_app


def permiso_plantillas_app(request, empresa):
    if rol_clinico_granular(request.user, empresa):
        return request.user.tiene_permiso_erp("puede_editar_historia_clinica", empresa)
    return request.user.tiene_permiso_erp("puede_expediente_clinico", empresa) or request.user.tiene_permiso_erp("puede_configuracion_clinica", empresa)


def autorizar_plantillas_app(request, empresa, habilitadas, consulta=False):
    if not es_plantillas_app(request, empresa):
        return
    if not habilitadas or not empresa.tiene_modulo_activo("clinica_medica"):
        raise Http404("Las plantillas de recetas no están habilitadas para esta empresa.")
    # Son rutas globales: reutilizan los menús legados de expediente y
    # configuración, sin exigir pacientes ni cambiar el mapper o el rol.
    if not request.user.puede_acceder_empresa(empresa) or not permiso_plantillas_app(request, empresa):
        raise PermissionDenied("No tiene permiso para administrar plantillas de recetas.")
    if consulta and request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("El listado móvil de plantillas es de consulta.")


def url_plantilla_app(vista, empresa, plantilla=None):
    args = [empresa.slug] + ([plantilla.pk] if plantilla else [])
    return f"{reverse(vista, args=args)}?app=1"


def contexto_plantillas_app(request, empresa, plantilla=None, plantillas=()):
    contexto = contexto_pacientes_app(request, empresa)
    contexto.update({
        "can_manage_recipe_templates": permiso_plantillas_app(request, empresa),
        "app_recipe_templates_url": url_plantilla_app("clinica_plantillas_recetas", empresa),
        "app_recipe_template_create_url": url_plantilla_app("clinica_crear_plantilla_receta", empresa),
        "app_recipe_template_form_url": url_plantilla_app("clinica_editar_plantilla_receta", empresa, plantilla) if plantilla else url_plantilla_app("clinica_crear_plantilla_receta", empresa),
        "mobile_recipe_template_items": [{
            "plantilla": item,
            "edit_url": url_plantilla_app("clinica_editar_plantilla_receta", empresa, item),
            "usuario": (item.creada_por.get_full_name() or item.creada_por.username) if item.creada_por else "Usuario no disponible",
            "detalles": [{
                "nombre": detalle.nombre_medicamento, "cantidad": detalle.cantidad,
                "indicaciones": detalle.indicaciones, "observaciones": detalle.observaciones,
            } for detalle in item.detalles.all()],
        } for item in plantillas if item.empresa_id == empresa.pk],
    })
    return contexto


def render_plantillas_app(request, empresa, web_template, mobile_template, contexto):
    if es_plantillas_app(request, empresa):
        contexto.update(contexto_plantillas_app(request, empresa, contexto.get("plantilla"), contexto.get("plantillas", ())))
        template = mobile_template
    else:
        template = web_template
    return respuesta_pacientes_app(request, empresa, render(request, template, contexto))


def volver_plantillas_app(request, empresa):
    if not es_plantillas_app(request, empresa):
        return redirect("clinica_plantillas_recetas", empresa_slug=empresa.slug)
    return respuesta_pacientes_app(request, empresa, redirect(url_plantilla_app("clinica_plantillas_recetas", empresa)))
