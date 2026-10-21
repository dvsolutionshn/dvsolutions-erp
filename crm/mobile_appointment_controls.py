"""Presentación opt-in de los controles originales ligados a citas."""

from datetime import date
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.clinical_permissions import rol_clinico_granular
from .forms import (
    ProgramaCamaraHiperbaricaForm, ProgramaTerapiaPostQuirurgicaForm,
    SesionCamaraHiperbaricaForm, SesionTerapiaPostQuirurgicaForm,
)
from .models import CitaCliente, ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionCamaraHiperbarica, SesionTerapiaPostQuirurgica
from .mobile_modules import inicio_app_disponible, url_modulos_app


CONTROLES_CITA_APP = {
    "camara_hiperbarica": {
        "nombre": "Cámara hiperbárica · Con cita", "ver": "puede_ver_camara_hiperbarica",
        "escribir": "puede_escribir_camara_hiperbarica", "param": "control_camara",
        "vista": "agenda_camara_hiperbarica", "guardar": "agenda_camara_hiperbarica_guardar",
        "programa": "programa_camara", "sesion": "sesion_camara", "cita": "cita_control_camara",
        "citas": "citas_camara_hiperbarica", "historial": "historial_camara",
        "tablero": "tablero_sesiones_camara", "completadas": "sesiones_camara_completadas",
        "modelo_programa": ProgramaCamaraHiperbarica, "modelo_sesion": SesionCamaraHiperbarica,
        "form_programa": ProgramaCamaraHiperbaricaForm, "form_sesion": SesionCamaraHiperbaricaForm,
    },
    "terapias_postquirurgicas": {
        "nombre": "Terapias postquirúrgicas · Con cita", "ver": "puede_ver_postquirurgicas",
        "escribir": "puede_escribir_postquirurgicas", "param": "control_terapia",
        "vista": "agenda_terapias_postquirurgicas", "guardar": "agenda_terapias_postquirurgicas_guardar",
        "programa": "programa_terapia", "sesion": "sesion_terapia", "cita": "cita_control_terapia",
        "citas": "citas_terapias_post", "historial": "historial_terapias_post",
        "tablero": "tablero_sesiones_terapia", "completadas": "sesiones_terapia_completadas",
        "modelo_programa": ProgramaTerapiaPostQuirurgica, "modelo_sesion": SesionTerapiaPostQuirurgica,
        "form_programa": ProgramaTerapiaPostQuirurgicaForm, "form_sesion": SesionTerapiaPostQuirurgicaForm,
    },
}


def es_control_cita_app(request, empresa):
    return empresa.slug == "hospital_mia" and (request.GET.get("app") == "1" or request.POST.get("app") == "1")


def _permiso(request, empresa, nombre):
    return request.user.tiene_permiso_erp(nombre if rol_clinico_granular(request.user, empresa) else "puede_citas", empresa)


def url_control_cita_app(empresa, tipo, fecha=None, cita=None):
    query = {"app": "1"}
    if fecha:
        query["fecha"] = fecha.isoformat()
    if cita:
        query[CONTROLES_CITA_APP[tipo]["param"]] = str(cita.pk)
    return f"{reverse(CONTROLES_CITA_APP[tipo]['vista'], args=[empresa.slug])}?{urlencode(query)}"


def controles_cita_disponibles_app(request, empresa):
    if not (empresa.slug == "hospital_mia" and empresa.tiene_modulo_activo("agenda_citas")
            and request.user.is_authenticated and request.user.puede_acceder_empresa(empresa)):
        return []
    return [{"codigo": tipo, "nombre": config["nombre"], "url": url_control_cita_app(empresa, tipo),
             "puede_registrar": _permiso(request, empresa, config["escribir"])}
            for tipo, config in CONTROLES_CITA_APP.items() if _permiso(request, empresa, config["ver"])]


def _identificador(valor):
    if (not isinstance(valor, str) or not valor.isascii() or not valor.isdecimal()
            or len(valor) > 19 or valor.startswith("0") or int(valor) > 9223372036854775807):
        raise PermissionDenied("El identificador del control móvil no es válido.")
    return int(valor)


def autorizar_control_cita_app(request, empresa, tipo, cita_id=None):
    if not es_control_cita_app(request, empresa):
        return
    config = CONTROLES_CITA_APP[tipo]
    if not empresa.tiene_modulo_activo("agenda_citas"):
        raise Http404("El módulo de agenda no está habilitado para esta empresa.")
    if (not request.user.is_authenticated or not request.user.puede_acceder_empresa(empresa)
            or not _permiso(request, empresa, config["ver"])
            or (cita_id is not None and not _permiso(request, empresa, config["escribir"]))):
        raise PermissionDenied("No tiene permiso para esta atención ligada a una cita.")
    if request.method not in ({"POST"} if cita_id is not None else {"GET", "HEAD"}):
        raise PermissionDenied("Método no disponible para este control móvil.")
    if (set(request.GET) - {"app", "fecha", config["param"]}
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])
            or any(len(request.GET.getlist(key)) != 1 for key in request.GET)):
        raise PermissionDenied("La selección móvil contiene datos no compatibles.")
    if "fecha" in request.GET:
        try:
            fecha = date.fromisoformat(request.GET["fecha"])
        except ValueError:
            raise PermissionDenied("La fecha del control móvil no es válida.")
        if fecha.isoformat() != request.GET["fecha"]:
            raise PermissionDenied("La fecha del control móvil no es válida.")
    if config["param"] in request.GET:
        seleccionado = _identificador(request.GET[config["param"]])
        if cita_id is not None and seleccionado != cita_id:
            raise PermissionDenied("La cita seleccionada no coincide con el formulario.")
    if cita_id is not None:
        _identificador(str(cita_id))
        programa_form, sesion_form = config["form_programa"](), config["form_sesion"]()
        campos = {**programa_form.fields, **sesion_form.fields}
        multiples = {nombre for nombre, field in campos.items() if isinstance(field, forms.MultipleChoiceField)}
        permitidos = set(campos) | {"app", "accion", "programa_id", "csrfmiddlewaretoken"}
        if (request.POST.getlist("app") != ["1"] or len(request.POST.getlist("programa_id")) != 1
                or request.POST.getlist("accion") not in (["borrador"], ["finalizar"])
                or set(request.POST) - permitidos
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST if key not in multiples)):
            raise PermissionDenied("El formulario móvil contiene acciones o datos no compatibles.")
        if request.POST["programa_id"]:
            _identificador(request.POST["programa_id"])


def _validar_cita(empresa, cita):
    if (cita.empresa_id != empresa.pk or not cita.paciente_id or cita.paciente.empresa_id != empresa.pk
            or (cita.servicio_clinico_id and cita.servicio_clinico.empresa_id != empresa.pk)
            or (cita.producto_id and cita.producto.empresa_id != empresa.pk)
            or (cita.profesional_salud_id and cita.profesional_salud.empresa_id != empresa.pk)):
        raise PermissionDenied("La cita no corresponde a los datos clínicos de esta empresa.")


def _validar_programa(empresa, paciente, programa, modelo_sesion):
    if programa is None:
        return
    if programa.empresa_id != empresa.pk or programa.paciente_id != paciente.pk:
        raise PermissionDenied("El programa no pertenece al paciente de esta empresa.")
    registros = modelo_sesion.objects.filter(programa=programa)
    if (registros.exclude(empresa=empresa, paciente=paciente).exists()
            or registros.filter(cita__isnull=False).exclude(cita__empresa=empresa, cita__paciente=paciente).exists()):
        raise PermissionDenied("El programa contiene una sesión con relaciones no compatibles.")


def _citas_dia_validadas(empresa, fecha):
    citas = list(CitaCliente.objects.filter(empresa=empresa, fecha_hora__date=fecha, paciente__isnull=False).select_related("paciente", "servicio_clinico", "producto", "profesional_salud"))
    for cita in citas:
        _validar_cita(empresa, cita)
    return citas


def validar_control_cita_app(request, empresa, tipo, cita, clasificador=None):
    if not es_control_cita_app(request, empresa):
        return
    _validar_cita(empresa, cita)
    if clasificador is not None and not clasificador(cita):
        raise PermissionDenied("La cita no corresponde a este tipo de control.")
    fecha = timezone.localtime(cita.fecha_hora).date()
    if "fecha" in request.GET and fecha.isoformat() != request.GET["fecha"]:
        raise PermissionDenied("La cita seleccionada no corresponde a esta fecha.")
    if request.method == "POST":
        # También el error/autoguardado original reconstruye las tarjetas del
        # día. Denegar relaciones importadas ajenas antes de cualquier save.
        _citas_dia_validadas(empresa, fecha)
    config = CONTROLES_CITA_APP[tipo]
    sesion = config["modelo_sesion"].objects.filter(cita=cita).select_related("programa").first()
    if sesion and (sesion.empresa_id != empresa.pk or sesion.paciente_id != cita.paciente_id):
        raise PermissionDenied("La sesión no pertenece a esta cita y paciente.")
    programa = sesion.programa if sesion else config["modelo_programa"].objects.filter(empresa=empresa, paciente=cita.paciente, activo=True).first()
    _validar_programa(empresa, cita.paciente, programa, config["modelo_sesion"])
    if request.method == "POST":
        programa_id = request.POST["programa_id"]
        if not programa_id:
            if programa:
                raise PermissionDenied("Debe conservar el programa seleccionado en la atención.")
        else:
            seleccionado = get_object_or_404(config["modelo_programa"], pk=_identificador(programa_id), empresa=empresa, paciente=cita.paciente)
            if (sesion and seleccionado.pk != sesion.programa_id) or (not sesion and not seleccionado.activo):
                raise PermissionDenied("El programa seleccionado no corresponde a esta atención.")
            _validar_programa(empresa, cita.paciente, seleccionado, config["modelo_sesion"])


def validar_contexto_control_cita_app(request, empresa, tipo, fecha, clasificador):
    if not es_control_cita_app(request, empresa):
        return
    config = CONTROLES_CITA_APP[tipo]
    citas = _citas_dia_validadas(empresa, fecha)
    seleccionado = _identificador(request.GET[config["param"]]) if config["param"] in request.GET else None
    encontrada = None
    for cita in citas:
        if seleccionado == cita.pk:
            if not clasificador(cita):
                raise PermissionDenied("La cita no corresponde a este tipo de control.")
            encontrada = cita
    if seleccionado is not None:
        if encontrada is None:
            raise Http404("La cita seleccionada no está disponible en esta fecha.")
        validar_control_cita_app(request, empresa, tipo, encontrada)


def respuesta_control_cita_app(request, empresa, response):
    if es_control_cita_app(request, empresa):
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
    return response


def render_control_cita_app(request, empresa, tipo, web_template, contexto, borrador_guardado=False):
    if not es_control_cita_app(request, empresa):
        return render(request, web_template, contexto)
    # Se reutiliza únicamente el agrupador visual, no el circuito sin cita.
    from clinica.mobile_controls import _secciones

    config = CONTROLES_CITA_APP[tipo]
    cita = contexto.get(config["cita"])
    programa, sesion = contexto.get(config["programa"]), contexto.get(config["sesion"])
    programa_form, sesion_form = contexto.get(f"{config['programa']}_form"), contexto.get(f"{config['sesion']}_form")
    puede_registrar = _permiso(request, empresa, config["escribir"])
    readonly = not puede_registrar or bool(sesion and sesion.bloqueada)
    if programa_form and sesion_form and readonly:
        for field in list(programa_form.fields.values()) + list(sesion_form.fields.values()):
            field.disabled = True
    fecha = contexto["fecha_seleccionada"]
    program_sections = _secciones(programa_form, programa=True) if programa_form else []
    session_sections = _secciones(sesion_form) if sesion_form else []
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    items = contexto.get(config["tablero"], [])
    records = {registro.pk: registro for registro in contexto.get(config["historial"], [])}
    contexto.update({
        "modo_app": True, "app_base_url": inicio, "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_modules_url": url_modulos_app(request, empresa), "app_home_disponible": inicio_app_disponible(request, empresa),
        "can_view_appointment_control": True, "can_register_appointment_control": puede_registrar,
        "appointment_control_readonly": readonly, "appointment_control_draft_autosaved": borrador_guardado,
        "mobile_appointment_control_type": {"codigo": tipo, "nombre": config["nombre"]},
        "mobile_appointment_control_cita": cita, "mobile_appointment_control_program": programa,
        "mobile_appointment_control_session": sesion, "programa_form": programa_form, "sesion_form": sesion_form,
        "mobile_appointment_control_program_sections": program_sections, "mobile_appointment_control_session_sections": session_sections,
        "mobile_appointment_control_sections": program_sections + session_sections,
        "mobile_appointment_control_fixed_number": contexto.get("numero_sesion_desde_cita"),
        "mobile_appointment_control_fixed_phase": cita.fase_servicio if cita and tipo == "terapias_postquirurgicas" and cita.fase_servicio in {1, 2} else None,
        "mobile_appointment_control_phase": contexto.get("fase_terapia_actual"),
        "mobile_appointment_control_board_items": items,
        "mobile_appointment_control_stats": {"total": len(items), "finalizadas": contexto.get(config["completadas"], 0)},
        "mobile_appointment_control_records": [{"registro": registro, "numeros": [n for n in (registro.numero_sesion, registro.numero_sesion_adicional) if n],
            "fecha": registro.fecha_sesion, "estado": registro.estado, "ligada_cita": bool(registro.cita_id),
            "responsable": (registro.actualizado_por.get_full_name().strip() or registro.actualizado_por.username) if registro.actualizado_por_id else
            (registro.creado_por.get_full_name().strip() or registro.creado_por.username) if registro.creado_por_id else "Usuario no disponible"} for registro in records.values()],
        "mobile_appointment_control_appointments": [{"cita": item, "url": url_control_cita_app(empresa, tipo, fecha, item), "active": bool(cita and item.pk == cita.pk)} for item in contexto.get(config["citas"], [])],
        "app_appointment_control_url": url_control_cita_app(empresa, tipo, fecha, cita),
        "app_appointment_control_list_url": url_control_cita_app(empresa, tipo, fecha),
        "app_appointment_control_previous_url": url_control_cita_app(empresa, tipo, contexto["fecha_anterior"]),
        "app_appointment_control_next_url": url_control_cita_app(empresa, tipo, contexto["fecha_siguiente"]),
        "app_appointment_control_back_url": url_modulos_app(request, empresa),
        "app_appointment_control_form_url": f"{reverse(config['guardar'], args=[empresa.slug, cita.pk])}?app=1" if cita else "",
    })
    return respuesta_control_cita_app(request, empresa, render(request, "crm/mobile_appointment_control.html", contexto))


def volver_control_cita_app(request, empresa, tipo, cita):
    fecha = timezone.localtime(cita.fecha_hora).date()
    return respuesta_control_cita_app(request, empresa, redirect(url_control_cita_app(empresa, tipo, fecha, cita)))
