"""Presentación interna de la historia general con el formulario web original.

La vista conserva su creación inicial, su guardado y la sincronización histórica
de paciente/cliente. Este helper sólo cierra la entrada y adapta la presentación;
no es una consulta sin efectos ni un segundo servicio de historia clínica.
"""

from types import SimpleNamespace

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular


# Los nueve pasos presentes en el HTML del endpoint web, sin los campos retirados
# que todavía conserva su Form ni la fotografía del registro de paciente nuevo.
SECCIONES_HISTORIA_GENERAL_APP = (
    ("generales", "Datos generales", (
        "nombres", "apellidos", "identidad", "fecha_nacimiento", "sexo", "estado_civil",
    )),
    ("contacto", "Datos generales de contacto", (
        "correo", "telefono_codigo_area", "telefono", "direccion", "lugar_nacimiento",
        "ocupacion", "lugar_trabajo", "informante", "informante_detalle",
        "contacto_emergencia_completo", "referido_por", "referido_por_detalle",
    )),
    ("motivo", "Motivo y procedimiento de interés", (
        "motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros",
    )),
    ("funciones", "Funciones orgánicas generales", (
        "funcion_apetito", "funcion_sueno", "funcion_sed", "funcion_miccion", "funcion_evacuaciones",
    )),
    ("antecedentes", "Antecedentes médicos", (
        "diagnostico_medico", "diagnostico_medico_detalle", "alergias_respuesta", "alergias_detalle",
        "medicamentos_respuesta", "medicamentos_detalle", "antecedentes_infecciosos",
    )),
    ("hospitalarios", "Antecedentes quirúrgicos, hospitalarios y hábitos", (
        "antecedentes_hospitalarios", "antecedentes_hospitalarios_detalle", "quirurgicos_operado",
        "quirurgicos_detalle", "consumo_riesgo", "consumo_riesgo_detalle", "dieta", "ejercicio",
    )),
    ("familiares", "Antecedentes familiares", (
        "antecedentes_familiares", "antecedentes_familiares_detalle",
        "riesgo_tromboembolico", "riesgo_tromboembolico_otros",
    )),
    ("gine", "Historia / Riesgo Ginecológico", (
        "gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas", "gine_abortos",
        "gine_ultima_menstruacion", "gine_embarazada", "gine_lactancia", "gine_mamografia",
        "gine_mamografia_fecha",
    )),
    ("psicologica", "Evaluación psicológica y consentimiento", (
        "evaluacion_psicologica", "evaluacion_psicologica_detalle", "decision_cirugia",
        "decision_cirugia_otros", "consentimiento_datos",
    )),
)
CAMPOS_OCULTOS_HISTORIA_GENERAL_APP = (
    "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
    "contacto_emergencia", "telefono_emergencia",
)
CAMPOS_HISTORIA_GENERAL_APP = frozenset(
    nombre for _clave, _titulo, nombres in SECCIONES_HISTORIA_GENERAL_APP for nombre in nombres
) | frozenset(CAMPOS_OCULTOS_HISTORIA_GENERAL_APP)


def es_historia_general_app(request, empresa):
    """Una clave app malformada también activa el guard; otras empresas siguen web."""
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and ("app" in request.GET or "app" in request.POST)
    )


def historia_general_disponible_app(request, empresa):
    usuario = request.user
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and empresa.activa and interfaz_clinica_activa(empresa)
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return False
    permiso = "puede_editar_historia_clinica" if rol_clinico_granular(usuario, empresa) else "puede_pacientes"
    return usuario.tiene_permiso_erp(permiso, empresa)


def _identificador(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de paciente no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador de paciente no válido.")
    return numero


def autorizar_historia_general_app(request, empresa, paciente_id, form_class):
    if request.method not in {"GET", "HEAD", "POST"} or not historia_general_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para completar esta historia clínica.")
    if set(request.GET) != {"app"} or request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("Parámetros de historia general móvil no válidos.")
    numero = _identificador(paciente_id)
    partes = request.path.strip("/").split("/")
    if "pacientes" in partes:
        indice = partes.index("pacientes") + 1
        if indice < len(partes) and _identificador(partes[indice]) != numero:
            raise PermissionDenied("Identificador de paciente no válido.")
    if request.FILES:
        raise PermissionDenied("La historia general interna no admite archivos.")
    if request.method in {"GET", "HEAD"}:
        if request.POST or request.body:
            raise PermissionDenied("La apertura de historia general no admite un cuerpo de datos.")
        return
    permitidos = CAMPOS_HISTORIA_GENERAL_APP | {"app", "csrfmiddlewaretoken"}
    if set(request.POST) - permitidos or request.POST.getlist("app") != ["1"]:
        raise PermissionDenied("Campos de historia general móvil no válidos.")
    for nombre in request.POST:
        campo = form_class.base_fields.get(nombre)
        if not isinstance(campo, forms.MultipleChoiceField) and len(request.POST.getlist(nombre)) != 1:
            raise PermissionDenied("Campos repetidos de historia general móvil no válidos.")


def validar_generales_paciente_app(empresa, paciente, generales):
    if generales.select_related(None).exclude(empresa_id=empresa.pk, paciente_id=paciente.pk).exists():
        raise PermissionDenied("La historia general contiene una preconsulta de otra empresa.")


def validar_relaciones_guardado_app(paciente, preconsulta=None):
    """Sólo relaciones utilizadas por el POST original; no restringir actores compartidos."""
    from facturacion.models import Cliente

    if paciente.cliente_id and not Cliente.objects.filter(pk=paciente.cliente_id).exists():
        raise PermissionDenied("El paciente tiene un cliente asociado que no existe.")
    if preconsulta and preconsulta.creada_por_id and not get_user_model().objects.filter(pk=preconsulta.creada_por_id).exists():
        raise PermissionDenied("La historia general tiene un autor asociado que no existe.")


def _valor_plano(valor):
    if valor is None or isinstance(valor, (str, bool, int, float)):
        return True
    return isinstance(valor, (list, tuple)) and all(
        item is None or isinstance(item, (str, bool, int, float)) for item in valor
    )


def _validar_valor_usado(valor, campo=None):
    if not _valor_plano(valor) or (
        isinstance(campo, forms.MultipleChoiceField) and valor
        and not isinstance(valor, (str, list, tuple))
    ):
        raise PermissionDenied("La historia general contiene una respuesta con estructura no compatible.")


def validar_datos_generales_app(preconsulta, form_class):
    """Validar estructura usada, nunca códigos clínicos ni claves históricas desconocidas."""
    datos = preconsulta.datos_generales if isinstance(preconsulta.datos_generales, dict) else {}
    general = datos.get("formulario_general", {}) or {}
    if not isinstance(general, dict):
        raise PermissionDenied("La historia general contiene un formulario con estructura no compatible.")
    for origen in (datos, general):
        for nombre, valor in origen.items():
            if nombre in form_class.base_fields:
                _validar_valor_usado(valor, form_class.base_fields[nombre])
    # Compatibilidad que lee el constructor original fuera de sus base_fields.
    for nombre in ("alergias_medicamentos", "alergias_medicamentos_detalle"):
        if nombre in general:
            _validar_valor_usado(general[nombre])
    _validar_valor_usado(preconsulta.antecedentes_familiares, form_class.base_fields["antecedentes_familiares"])


def url_historia_general_app(empresa, paciente):
    return f"{reverse('clinica_completar_historia_clinica_paciente', args=[empresa.slug, paciente.pk])}?app=1"


def identidad_historia_general_app(empresa, paciente, preconsulta):
    """Capturar identidad persistida antes de que ModelForm valide su instancia."""
    return {
        "empresa": SimpleNamespace(nombre=empresa.nombre, slug=empresa.slug),
        "paciente": SimpleNamespace(id=paciente.pk, pk=paciente.pk, nombre=paciente.nombre,
                                    expediente_codigo=paciente.expediente_codigo),
        "preconsulta": SimpleNamespace(id=preconsulta.pk, tipo=preconsulta.tipo, estado=preconsulta.estado,
                                       fecha_completada=preconsulta.fecha_completada),
    }


def _navegacion(request, empresa, paciente):
    granular = bool(rol_clinico_granular(request.user, empresa))
    puede_paciente = request.user.tiene_permiso_erp(
        "puede_ver_datos_generales_paciente" if granular else "puede_pacientes", empresa
    )
    puede_historia = request.user.tiene_permiso_erp(
        "puede_ver_historia_clinica" if granular else "puede_expediente_clinico", empresa
    )
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    detalle = f"{reverse('clinica_paciente_detalle', args=[empresa.slug, paciente.pk])}?app=1" if puede_paciente else ""
    historias = f"{reverse('clinica_historias_especialidad', args=[empresa.slug, paciente.pk])}?app=1" if puede_historia else ""
    return {
        "can_view_patient": puede_paciente, "can_view_history": puede_historia,
        "can_view_app_modules": True, "app_modules_url": modulos,
        "app_patient_detail_url": detalle, "app_patient_histories_url": historias,
        "app_complete_history_back_url": detalle or historias or modulos,
    }


def volver_historia_general_app(request, empresa, paciente):
    return redirect(_navegacion(request, empresa, paciente)["app_complete_history_back_url"])


def render_historia_general_app(request, empresa, paciente, preconsulta, form, identidad):
    # Sólo formato del widget nativo de esta instancia; datos e input_formats
    # originales permanecen intactos, incluidos strings recibidos con errores.
    for nombre in CAMPOS_HISTORIA_GENERAL_APP:
        if isinstance(form.fields[nombre], forms.DateField):
            form.fields[nombre].widget.format = "%Y-%m-%d"
    contexto = {
        **identidad, **_navegacion(request, empresa, paciente), "form": form,
        "modo_app": True, "registro_interno": True, "modo_doctor_completar_historia": True,
        "app_home_disponible": False, "app_base_url": "",
        "app_hide_agenda_navigation": True, "app_hide_patient_navigation": True,
        "app_complete_history_form_url": url_historia_general_app(empresa, paciente),
        "mobile_complete_history_confirmation_name": identidad["paciente"].nombre,
        "mobile_complete_history_sections": [
            {"key": clave, "titulo": titulo, "campos": [form[nombre] for nombre in nombres]}
            for clave, titulo, nombres in SECCIONES_HISTORIA_GENERAL_APP
        ],
        "mobile_complete_history_hidden_fields": [form[nombre] for nombre in CAMPOS_OCULTOS_HISTORIA_GENERAL_APP],
    }
    response = render(request, "clinica/mobile_general_history_form.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
