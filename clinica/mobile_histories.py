"""Presentación de formularios clínicos existentes, sin lógica médica."""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .mobile_patients import (
    autorizar_pacientes_app, contexto_pacientes_app, es_pacientes_app,
    permiso_pacientes_app, respuesta_pacientes_app,
)
from .models import HistoriaClinicaEspecialidad


TIPOS_HISTORIA_APP = frozenset({"capilar", "cirugia_plastica", "medicina_estetica", "enfermeria", "terapias"})
TIPOS_HISTORIA_EDITABLES_APP = TIPOS_HISTORIA_APP | {"evaluacion_integral", "tricopigmentacion"}
es_historias_app = es_pacientes_app


def _permiso_crear_tipo(tipo):
    return {"enfermeria": "puede_escribir_enfermeria", "terapias": "puede_escribir_terapias"}.get(tipo, "puede_crear_historia_clinica")


def autorizar_historias_app(request, empresa, tipo=None, editar=False):
    if not es_historias_app(request, empresa):
        return
    permitidos = TIPOS_HISTORIA_EDITABLES_APP if editar else TIPOS_HISTORIA_APP
    if tipo is not None and tipo not in permitidos:
        raise Http404("Este formulario especializado aún no está adaptado a la app.")
    autorizar_pacientes_app(request, empresa, "puede_editar_historia_clinica" if editar else _permiso_crear_tipo(tipo), "puede_expediente_clinico")


def validar_edicion_generica_app(request, empresa, historia, form):
    """Estas dos ediciones sólo admiten el formulario general existente."""
    if not es_historias_app(request, empresa) or historia.tipo not in {"evaluacion_integral", "tricopigmentacion"}:
        return
    if len(request.GET.getlist("app")) > 1:
        raise PermissionDenied("El formulario móvil contiene datos no compatibles.")
    if request.method == "POST":
        permitidos = {"app", "csrfmiddlewaretoken"} | {form.add_prefix(nombre) for nombre in form.fields}
        if (request.POST.getlist("app") != ["1"] or set(request.POST) - permitidos
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)):
            raise PermissionDenied("Esta edición móvil sólo permite los datos generales y textos del registro.")


def url_historia_form_app(empresa, paciente, tipo, historia=None):
    vista = "clinica_editar_historia_especialidad" if historia else "clinica_crear_historia_especialidad"
    args = [empresa.slug, paciente.pk, historia.pk if historia else tipo]
    return f"{reverse(vista, args=args)}?app=1"


def tipos_historia_creables_app(request, empresa, paciente=None):
    if not (
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and empresa.tiene_modulo_activo("clinica_medica") and request.user.is_authenticated
        and request.user.puede_acceder_empresa(empresa)
    ):
        return []
    return [{
        "codigo": tipo, "nombre": nombre,
        "url": url_historia_form_app(empresa, paciente, tipo) if paciente else "",
    } for tipo, nombre in HistoriaClinicaEspecialidad.TIPO_CHOICES if tipo in TIPOS_HISTORIA_APP and permiso_pacientes_app(request, empresa, _permiso_crear_tipo(tipo), "puede_expediente_clinico")]


def _secciones_historia_app(form):
    visibles = {campo.name: campo for campo in form.visible_fields()}
    usados, secciones = set(), []

    def agregar(key, titulo, nombres):
        campos = [visibles[nombre] for nombre in nombres if nombre in visibles and nombre not in usados]
        if campos:
            usados.update(campo.name for campo in campos)
            secciones.append({"key": key, "titulo": titulo, "campos": campos})

    agregar("registro", "Datos del registro", ("profesional", "fecha_atencion"))
    agregar("alopecia", "Clasificación capilar", ("paciente_sexo_confirmado", "alopecia_grado"))
    for nombre, etiqueta, *_ in form.campos_estructurados:
        agregar(nombre, etiqueta, (nombre, f"{nombre}_otros"))
    agregar("contenido", "Historia y nota clínica", tuple(nombre for nombre in visibles if nombre not in usados and nombre != "estado"))
    agregar("estado", "Estado del registro", ("estado",))
    return secciones


def contexto_historia_app(request, empresa, paciente, tipo, form, historia=None):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    generic_only = bool(historia and tipo in {"evaluacion_integral", "tricopigmentacion"})
    if generic_only and not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        contexto["app_home_disponible"] = False
        contexto["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    can_edit = permiso_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico")
    tipos = tipos_historia_creables_app(request, empresa, paciente)
    if contexto["can_view_history"]:
        regreso = contexto["app_patient_histories_url"]
    elif contexto["can_view_patient"]:
        regreso = contexto["app_patient_detail_url"]
    else:
        regreso = contexto["app_modules_url"]
    contexto.update({
        "can_edit_history": can_edit,
        "mobile_history_create_types": tipos,
        "mobile_history_form_sections": _secciones_historia_app(form),
        "mobile_history_generic_only": generic_only,
        "app_history_form_url": url_historia_form_app(empresa, paciente, tipo, historia),
        "app_history_back_url": regreso, "app_history_safe_cancel_url": regreso,
    })
    return contexto


def render_historia_app(request, empresa, paciente, tipo, contexto):
    if es_historias_app(request, empresa):
        contexto.update(contexto_historia_app(request, empresa, paciente, tipo, contexto["form"], contexto.get("historia")))
        template = "clinica/mobile_history_form.html"
    else:
        template = "clinica/historia_especialidad_form.html"
    return respuesta_pacientes_app(request, empresa, render(request, template, contexto))


def volver_historia_app(request, empresa, paciente, tipo, historia=None):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    if contexto["can_view_history"]:
        destino = contexto["app_patient_histories_url"]
    elif historia and permiso_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico"):
        destino = url_historia_form_app(empresa, paciente, tipo, historia)
    elif any(item["codigo"] == tipo for item in tipos_historia_creables_app(request, empresa, paciente)):
        destino = url_historia_form_app(empresa, paciente, tipo)
    else:
        destino = contexto["app_modules_url"]
    return respuesta_pacientes_app(request, empresa, redirect(destino))
