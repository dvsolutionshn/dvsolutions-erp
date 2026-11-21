"""Presentación de formularios clínicos existentes, sin lógica médica."""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import (
    EMPRESAS_CLINICAS_CON_ROLES, datos_paciente_limitados, historial_paciente_limitado,
    PERMISOS_EDICION_HISTORIAS, permiso_historia_por_tipo, puede_abrir_expediente_limitado,
    rol_clinico_granular, rol_clinico_v2,
)
from .mobile_controls import url_lectura_expediente_limitado
from .mobile_patients import (
    contexto_pacientes_app, es_pacientes_app,
    notas_historia_app, permiso_pacientes_app, respuesta_pacientes_app,
)
from .models import HistoriaClinicaEspecialidad


TIPOS_HISTORIA_APP = frozenset({"capilar", "cirugia_plastica", "medicina_estetica", "enfermeria", "terapias"})
TIPOS_HISTORIA_EDITABLES_APP = TIPOS_HISTORIA_APP | {"evaluacion_integral", "tricopigmentacion"}
es_historias_app = es_pacientes_app


def _permiso_crear_tipo(tipo):
    return {"enfermeria": "puede_escribir_enfermeria", "terapias": "puede_escribir_terapias"}.get(tipo, "puede_crear_historia_clinica")


def permiso_historia_app(request, empresa, tipo=None, *, editar=False):
    """Resolver la acción del área sin exigir acceso a otras historias."""
    usuario = request.user
    if not (
        empresa and empresa.activa and interfaz_clinica_activa(empresa)
        and empresa.tiene_modulo_activo("clinica_medica")
        and getattr(usuario, "is_authenticated", False) and usuario.puede_acceder_empresa(empresa)
    ):
        return False
    rol_v2 = rol_clinico_v2(usuario, empresa)
    if rol_v2:
        if tipo is None and editar:
            # La URL sólo contiene el id. La vista valida luego el tipo real.
            return any(usuario.tiene_permiso_erp(nombre, empresa) for nombre in PERMISOS_EDICION_HISTORIAS)
        acciones_separadas = bool(rol_v2.usa_permisos_acciones_v3)
        permiso = permiso_historia_por_tipo(tipo, crear=not editar and acciones_separadas, editar=editar or not acciones_separadas)
        return bool(permiso and usuario.tiene_permiso_erp(permiso, empresa))
    return permiso_pacientes_app(
        request, empresa, "puede_editar_historia_clinica" if editar else _permiso_crear_tipo(tipo),
        "puede_expediente_clinico",
    )


def autorizar_historias_app(request, empresa, tipo=None, editar=False):
    if not es_historias_app(request, empresa):
        return
    permitidos = TIPOS_HISTORIA_EDITABLES_APP if editar else TIPOS_HISTORIA_APP
    if tipo is not None and tipo not in permitidos:
        raise Http404("Este formulario especializado aún no está adaptado a la app.")
    if not empresa.tiene_modulo_activo("clinica_medica"):
        raise Http404("El módulo clínico no está habilitado para esta empresa.")
    if not permiso_historia_app(request, empresa, tipo, editar=editar):
        raise PermissionDenied("No tiene permiso para esta acción del expediente.")


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
    } for tipo, nombre in HistoriaClinicaEspecialidad.TIPO_CHOICES if tipo in TIPOS_HISTORIA_APP and permiso_historia_app(request, empresa, tipo)]


def _secciones_historia_app(form):
    visibles = {campo.name: campo for campo in form.visible_fields()}
    usados, secciones = set(), []

    def agregar(key, titulo, nombres):
        campos = [visibles[nombre] for nombre in nombres if nombre in visibles and nombre not in usados]
        if campos:
            usados.update(campo.name for campo in campos)
            secciones.append({"key": key, "titulo": titulo, "campos": campos})

    agregar("registro", "Datos del registro", ("fecha_atencion",))
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
    can_edit = permiso_historia_app(request, empresa, tipo, editar=True)
    tipos = tipos_historia_creables_app(request, empresa, paciente)
    if contexto["can_view_history"]:
        regreso = contexto["app_patient_histories_url"]
    elif contexto["can_view_patient"]:
        regreso = contexto["app_patient_detail_url"]
    else:
        regreso = contexto["app_modules_url"]
    if historial_paciente_limitado(request.user, empresa):
        tipos = [area for area in tipos if area["codigo"] == tipo]
        regreso = (
            url_lectura_expediente_limitado(request, empresa, paciente, tipo)
            or contexto.get("app_patient_expediente_url")
            or (contexto["app_patients_url"] if contexto["can_view_patients"] else contexto["app_modules_url"])
        )
    contexto.update({
        "can_edit_history": can_edit,
        "mobile_history_create_types": tipos,
        "mobile_history_form_sections": _secciones_historia_app(form),
        "mobile_history_generic_only": generic_only,
        "app_history_form_url": url_historia_form_app(empresa, paciente, tipo, historia),
        "app_history_back_url": regreso, "app_history_safe_cancel_url": regreso,
        "app_history_area_url": f"{contexto['app_patient_histories_url']}&tipo={tipo}" if contexto["can_view_history"] else "",
    })
    return contexto


def render_historia_app(request, empresa, paciente, tipo, contexto):
    if empresa.slug in EMPRESAS_CLINICAS_CON_ROLES:
        from .preconsulta_respuestas import bloques_respuestas_paciente

        contexto["respuestas_paciente_recibidas"] = bloques_respuestas_paciente(
            request.user, empresa, paciente, modo_app=es_historias_app(request, empresa), tipos={"general", tipo},
        )
    if es_historias_app(request, empresa):
        contexto.update(contexto_historia_app(request, empresa, paciente, tipo, contexto["form"], contexto.get("historia")))
        contexto["mobile_history_previous_notes"] = notas_historia_app(contexto.get("historias_previas", [])) if contexto["can_view_history"] else []
        template = "clinica/mobile_history_form.html"
    else:
        template = "clinica/historia_especialidad_form.html"
        if datos_paciente_limitados(request.user, empresa):
            regreso = url_lectura_expediente_limitado(request, empresa, paciente, tipo)
            if not regreso and puede_abrir_expediente_limitado(request.user, empresa):
                regreso = reverse("clinica_paciente_expediente", args=[empresa.slug, paciente.pk])
            if not regreso:
                vista = "clinica_pacientes" if request.user.tiene_permiso_erp("puede_ver_pacientes", empresa) else "clinica_dashboard"
                regreso = reverse(vista, args=[empresa.slug])
            contexto["historia_back_url"] = regreso
    return respuesta_pacientes_app(request, empresa, render(request, template, contexto))


def volver_historia_app(request, empresa, paciente, tipo, historia=None):
    contexto = contexto_pacientes_app(request, empresa, paciente)
    lectura = url_lectura_expediente_limitado(request, empresa, paciente, tipo)
    if lectura:
        destino = lectura
    elif contexto["can_view_history"]:
        destino = contexto["app_patient_histories_url"]
    elif historia and permiso_historia_app(request, empresa, tipo, editar=True):
        destino = url_historia_form_app(empresa, paciente, tipo, historia)
    elif any(item["codigo"] == tipo for item in tipos_historia_creables_app(request, empresa, paciente)):
        destino = url_historia_form_app(empresa, paciente, tipo)
    else:
        destino = contexto["app_modules_url"]
    return respuesta_pacientes_app(request, empresa, redirect(destino))
