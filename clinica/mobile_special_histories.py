"""Presentación de los formularios especializados del consolidado existente."""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .forms import EvaluacionClinicaIntegralForm, TricopigmentacionForm, FUNCIONES_ORGANICAS_SISTEMAS
from .mobile_patients import (
    autorizar_pacientes_app, contexto_pacientes_app, es_pacientes_app,
    permiso_pacientes_app, respuesta_pacientes_app,
)


FORMULARIOS_HISTORIA_ESPECIALES_APP = {
    "evaluacion_integral": ("Evaluación integral", "guardar_evaluacion_integral", "evaluacion", None),
    "funciones_organicas": ("Funciones orgánicas", "guardar_funciones_organicas", "funciones", "funciones_organicas"),
    "examen_fisico": ("Examen físico", "guardar_examen_fisico", "examen", "examen_fisico"),
    "tricopigmentacion": ("Tricopigmentación", "guardar_tricopigmentacion", "tricopigmentacion", "tricopigmentacion"),
}


def url_historia_especial_app(empresa, paciente, codigo):
    return f"{reverse('clinica_historial_clinico_consolidado', args=[empresa.slug, paciente.pk])}?{urlencode({'app': '1', 'formulario': codigo})}"


def formularios_historia_especiales_app(request, empresa, paciente=None):
    if not (
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and empresa.tiene_modulo_activo("clinica_medica") and request.user.is_authenticated
        and request.user.puede_acceder_empresa(empresa)
        and permiso_pacientes_app(request, empresa, "puede_ver_historia_clinica", "puede_expediente_clinico")
        and permiso_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico")
    ):
        return []
    return [{"codigo": codigo, "nombre": config[0], "url": url_historia_especial_app(empresa, paciente, codigo) if paciente else ""}
            for codigo, config in FORMULARIOS_HISTORIA_ESPECIALES_APP.items()]


def autorizar_historia_especial_app(request, empresa):
    autorizar_pacientes_app(request, empresa, "puede_ver_historia_clinica", "puede_expediente_clinico")
    autorizar_pacientes_app(request, empresa, "puede_editar_historia_clinica", "puede_expediente_clinico")


def formulario_historia_especial_app(empresa, codigo, profesional=None):
    _nombre, _accion, prefijo, seccion = FORMULARIOS_HISTORIA_ESPECIALES_APP[codigo]
    initial = {"fecha_atencion": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
    if profesional:
        initial["profesional"] = profesional
    if codigo == "tricopigmentacion":
        return TricopigmentacionForm(empresa=empresa, prefix=prefijo, initial=initial)
    return EvaluacionClinicaIntegralForm(empresa=empresa, prefix=prefijo, seccion=seccion, initial=initial)


def preparar_historia_especial_app(request, empresa):
    """Validar sólo el sobre móvil; la validación clínica permanece en el Form."""
    if not es_pacientes_app(request, empresa):
        return None
    query = request.GET.getlist("formulario")
    posted = request.POST.getlist("formulario")
    codigo = query[0] if query else (posted[0] if posted else None)
    if request.method == "POST":
        config = FORMULARIOS_HISTORIA_ESPECIALES_APP.get(codigo)
        if (not config or len(query) > 1 or posted != [codigo] or request.POST.getlist("app") != ["1"]
                or request.POST.getlist("accion") != [config[1]]):
            raise PermissionDenied("Esta acción no está disponible en el historial móvil.")
        campos = formulario_historia_especial_app(empresa, codigo)
        permitidos = {"app", "formulario", "accion", "csrfmiddlewaretoken"} | {campos.add_prefix(nombre) for nombre in campos.fields}
        if set(request.POST) - permitidos:
            raise PermissionDenied("El formulario móvil contiene acciones o datos no compatibles.")
        autorizar_historia_especial_app(request, empresa)
    elif codigo is not None:
        if len(query) != 1 or codigo not in FORMULARIOS_HISTORIA_ESPECIALES_APP:
            raise Http404("Formulario clínico móvil no válido.")
        if request.method not in {"GET", "HEAD"}:
            raise PermissionDenied("Método no disponible para este formulario móvil.")
        autorizar_historia_especial_app(request, empresa)
    return codigo


def _secciones_formulario(form):
    visibles = {campo.name: campo for campo in form.visible_fields()}
    usados, secciones = set(), []

    def agregar(key, titulo, nombres):
        campos = [visibles[nombre] for nombre in nombres if nombre in visibles and nombre not in usados]
        if campos:
            usados.update(campo.name for campo in campos)
            secciones.append({"key": key, "titulo": titulo, "campos": campos})

    agregar("registro", "Datos del registro", ("profesional", "fecha_atencion"))
    agregar("funciones_generales", "Funciones orgánicas generales", ("funciones_generales",))
    for codigo, etiqueta in FUNCIONES_ORGANICAS_SISTEMAS:
        agregar(f"funcion_{codigo}", etiqueta, (f"funcion_{codigo}", f"funcion_{codigo}_detalle"))
    agregar("mediciones", "Mediciones registradas", tuple(nombre for nombre in visibles if nombre.startswith("examen_") and nombre != "examen_fisico"))
    agregar("sesion", "Datos de la sesión", ("numero_sesion", "zona_tratada", "pigmento", "marca_lote", "tecnica", "aguja_cartucho", "proxima_sesion"))
    agregar("contenido", "Evaluación y notas", tuple(nombre for nombre in visibles if nombre not in usados and nombre != "estado"))
    agregar("estado", "Estado del registro", ("estado",))
    return secciones


def render_historia_especial_app(request, empresa, paciente, codigo, form):
    nombre, accion, _prefijo, _seccion = FORMULARIOS_HISTORIA_ESPECIALES_APP[codigo]
    contexto = contexto_pacientes_app(request, empresa, paciente)
    contexto.update({
        "empresa": empresa, "paciente": paciente, "form": form,
        "can_manage_special_history": True,
        "mobile_special_history_form": {"codigo": codigo, "nombre": nombre, "accion": accion, "url": url_historia_especial_app(empresa, paciente, codigo)},
        "mobile_special_history_sections": _secciones_formulario(form),
        "app_special_history_form_url": url_historia_especial_app(empresa, paciente, codigo),
        "app_special_history_back_url": contexto["app_patient_history_url"],
        "app_special_history_cancel_url": contexto["app_patient_history_url"],
    })
    return respuesta_pacientes_app(request, empresa, render(request, "clinica/mobile_special_history_form.html", contexto))


def volver_historia_especial_app(request, empresa, paciente, codigo):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    seccion = FORMULARIOS_HISTORIA_ESPECIALES_APP[codigo][3]
    destino = contexto["app_patient_history_url"]
    if seccion:
        destino += f"&seccion={seccion}"
    return respuesta_pacientes_app(request, empresa, redirect(destino))
