"""Navegación y presentación móvil de las recetas existentes, sin lógica médica."""

from django.shortcuts import redirect, render
from django.urls import reverse

from .mobile_patients import (
    autorizar_pacientes_app,
    contexto_pacientes_app,
    es_pacientes_app,
    permiso_pacientes_app,
    respuesta_pacientes_app,
    url_paciente_app,
)


es_recetas_app = es_pacientes_app
respuesta_recetas_app = respuesta_pacientes_app


def autorizar_recetas_app(request, empresa, permiso):
    autorizar_pacientes_app(request, empresa, permiso, "puede_expediente_clinico")


def url_receta_app(vista, empresa, paciente, receta=None):
    args = [empresa.slug, paciente.pk] + ([receta.pk] if receta else [])
    return f"{reverse(vista, args=args)}?app=1"


def _urls_receta_app(empresa, paciente, receta):
    return {
        nombre: url_receta_app(vista, empresa, paciente, receta)
        for nombre, vista in (
            ("preview_url", "clinica_receta_imprimir"),
            ("pdf_url", "clinica_receta_pdf"),
            ("email_url", "clinica_enviar_receta_correo"),
            ("whatsapp_url", "clinica_enviar_receta_whatsapp"),
        )
    }


def accesos_recetas_paciente_app(request, empresa, paciente):
    return {
        "app_patient_recipes_url": url_receta_app("clinica_recetas_paciente", empresa, paciente),
        "app_patient_recipe_create_url": url_receta_app("clinica_crear_receta_paciente", empresa, paciente),
        "can_create_recipe": permiso_pacientes_app(request, empresa, "puede_crear_recetas", "puede_expediente_clinico"),
    }


def contexto_selector_recetas_app(request, empresa):
    destino = request.GET.get("destino", "")
    permiso = {"recetas": "puede_ver_recetas", "crear_receta": "puede_crear_recetas"}.get(destino)
    if request.method != "GET" or not es_recetas_app(request, empresa) or not permiso:
        return {}
    if not permiso_pacientes_app(request, empresa, permiso, "puede_expediente_clinico"):
        return {}
    return {
        "mobile_patient_recipe_mode": destino,
        "mobile_patient_recipe_picker_url": url_paciente_app("clinica_pacientes", empresa, destino=destino),
    }


def contexto_recetas_app(request, empresa, paciente, receta=None, recetas=None):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    contexto.update(accesos_recetas_paciente_app(request, empresa, paciente))
    contexto.update({
        "app_recipes_url": url_receta_app("clinica_recetas_paciente", empresa, paciente),
        "app_recipe_create_url": url_receta_app("clinica_crear_receta_paciente", empresa, paciente),
        "app_recipe_form_url": url_receta_app("clinica_crear_receta_paciente", empresa, paciente),
        # Imprimir/PDF y envío comparten el permiso original de las rutas web.
        "can_send_recipe": permiso_pacientes_app(request, empresa, "puede_enviar_recetas", "puede_expediente_clinico"),
    })
    destino = "recetas" if contexto["can_view_recipes"] else "crear_receta" if contexto["can_create_recipe"] else ""
    contexto["app_recipe_patient_picker_url"] = (
        url_paciente_app("clinica_pacientes", empresa, destino=destino)
        if contexto["can_view_patients"] and destino else ""
    )
    if receta:
        contexto.update({f"app_recipe_{nombre}": url for nombre, url in _urls_receta_app(empresa, paciente, receta).items()})
        contexto["app_recipe_send_email_url"] = contexto["app_recipe_email_url"]
        contexto["app_recipe_send_whatsapp_url"] = contexto["app_recipe_whatsapp_url"]
    if recetas is not None:
        contexto["mobile_recipe_items"] = [
            {"receta": item, **_urls_receta_app(empresa, paciente, item)} for item in recetas
        ]
    return contexto


def render_recetas_app(request, empresa, web_template, mobile_template, contexto):
    if es_recetas_app(request, empresa):
        contexto.update(contexto_recetas_app(
            request, empresa, contexto["paciente"], contexto.get("receta"), contexto.get("recetas"),
        ))
        template = mobile_template
    else:
        template = web_template
    return respuesta_recetas_app(request, empresa, render(request, template, contexto))


def volver_receta_app(request, empresa, paciente, receta):
    if not es_recetas_app(request, empresa):
        return redirect("clinica_receta_imprimir", empresa_slug=empresa.slug, paciente_id=paciente.pk, receta_id=receta.pk)
    contexto = contexto_recetas_app(request, empresa, paciente, receta)
    for flag, destino in (
        ("can_send_recipe", "app_recipe_preview_url"),
        ("can_view_recipes", "app_recipes_url"),
        ("can_view_patient", "app_patient_detail_url"),
        ("can_create_recipe", "app_recipe_create_url"),
    ):
        if contexto[flag]:
            return respuesta_recetas_app(request, empresa, redirect(contexto[destino]))
    return respuesta_recetas_app(request, empresa, redirect(contexto["app_modules_url"]))
