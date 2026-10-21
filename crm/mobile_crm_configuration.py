"""Presentación móvil de la configuración CRM, sin edición de credenciales.

El formulario, su inicializador POST, el guardado y la auditoría siguen siendo
los originales. Retirar el campo sensible sólo de esta instancia del formulario
conserva literalmente el valor que se leyó del modelo; no añade control de
concurrencia al guardado completo existente.
"""

from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular


SECCIONES_CRM_APP = (
    ("Estado general", ("whatsapp_activo", "recordatorio_citas_activo")),
    ("Número y conexión", (
        "whatsapp_api_version", "whatsapp_phone_number_id",
        "whatsapp_business_account_id", "whatsapp_numero_prueba",
    )),
    ("Plantillas aprobadas", (
        "whatsapp_plantilla_prueba", "whatsapp_idioma_plantilla",
        "whatsapp_plantilla_marketing", "whatsapp_idioma_marketing",
        "whatsapp_plantilla_cita", "whatsapp_idioma_cita",
        "whatsapp_plantilla_preconsulta", "whatsapp_idioma_preconsulta",
    )),
    ("Mensajes de citas", (
        "mensaje_cita_confirmacion", "mensaje_cita_recordatorio_7_dias",
        "mensaje_cita_recordatorio_3_dias", "mensaje_cita_recordatorio_1_dia",
        "mensaje_cita_cancelada", "mensaje_cita_reagendada", "whatsapp_cita_incluir_enlace",
    )),
    ("Cumpleaños", (
        "recordatorio_cumpleanos_activo", "cumpleanos_recordatorio_1_dia",
        "cumpleanos_recordatorio_7_dias",
    )),
    ("Correo y alertas", ("remitente_correo", "dias_alerta_producto")),
)
CAMPOS_CRM_APP = frozenset(nombre for _, nombres in SECCIONES_CRM_APP for nombre in nombres)


def es_configuracion_crm_app(request):
    """Detecta el opt-in antes de decidir si la empresa está en el alcance."""
    return "1" in request.GET.getlist("app") or "1" in request.POST.getlist("app")


def configuracion_crm_disponible_app(request, empresa):
    """Reutiliza las puertas CRM existentes, sin crear configuración."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("crm_marketing")
        and (
            usuario.is_superuser or usuario.es_administrador_empresa
            or usuario.tiene_permiso_erp("puede_configuracion_crm", empresa)
        )
    )


def autorizar_configuracion_crm_app(request, empresa):
    if not configuracion_crm_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para la configuración CRM móvil de esta empresa.")
    if request.method not in {"GET", "HEAD", "POST"} or request.FILES:
        raise PermissionDenied("Método o archivos no disponibles en la configuración CRM móvil.")
    if (
        set(request.GET) - {"app"}
        or request.GET.getlist("app") != ["1"]
        or any(len(request.GET.getlist(nombre)) != 1 for nombre in request.GET)
    ):
        raise PermissionDenied("Parámetros de configuración CRM móvil inválidos.")
    if request.method == "POST":
        if (
            set(request.POST) - (CAMPOS_CRM_APP | {"app", "csrfmiddlewaretoken"})
            or request.POST.getlist("app") != ["1"]
            or any(len(request.POST.getlist(nombre)) != 1 for nombre in request.POST)
        ):
            raise PermissionDenied("La configuración móvil no admite credenciales ni datos adicionales.")
    elif request.POST:
        raise PermissionDenied("La consulta de configuración móvil no admite datos de guardado.")


def denegar_accion_crm_no_adaptada_app(request, payload=None):
    # El JSON ya lo analiza la vista original; aquí no se añade otro parser ni
    # se lee el cuerpo por segunda vez. Ningún opt-in móvil inicia transportes.
    app_json = isinstance(payload, dict) and str(payload.get("app", "")) == "1"
    if es_configuracion_crm_app(request) or app_json:
        raise PermissionDenied("La app no conecta credenciales ni envía pruebas de WhatsApp.")


def adaptar_formulario_crm_app(form):
    # No usar disabled: CharField.clean podría transformar espacios o NULL.
    # construct_instance omite el campo ausente de cleaned_data y el save
    # original conserva el atributo de la instancia leído en este request.
    form.fields.pop("whatsapp_token", None)
    form.initial.pop("whatsapp_token", None)
    return form


def _modulos_disponibles(request, empresa):
    """Mismas puertas del catálogo actual; no concede calendario ni inicio."""
    from core.mobile_users import usuarios_disponibles_app
    from facturacion.mobile_suppliers import proveedores_disponibles_app
    from .views import _proteger_agenda_mobile

    if _proteger_agenda_mobile(request, empresa) is None:
        return True
    if usuarios_disponibles_app(request, empresa) or any(proveedores_disponibles_app(request, empresa).values()):
        return True
    if rol_clinico_granular(request.user, empresa):
        from facturacion.mobile_transfers import transferencia_disponible_app

        return transferencia_disponible_app(request, empresa)
    return False


def contexto_configuracion_crm_app(request, empresa, config):
    """Captura identidad y estado persistido antes de validar el ModelForm."""
    from clinica.mobile_configuration import config_disponible_app

    identidad = SimpleNamespace(
        id=empresa.pk, pk=empresa.pk, nombre=empresa.nombre, slug=empresa.slug,
        tipo_solucion=empresa.tipo_solucion, activa=empresa.activa, logo=empresa.logo,
    )
    propia_url = f"{reverse('crm_configuracion', args=[empresa.slug])}?app=1"
    puede_config_clinica = config_disponible_app(request, empresa)["hub"]
    config_clinica_url = (
        f"{reverse('clinica_configuracion', args=[empresa.slug])}?app=1" if puede_config_clinica else ""
    )
    puede_modulos = _modulos_disponibles(request, empresa)
    modulos_url = reverse("agenda_mobile_modules_screen", args=[empresa.slug]) if puede_modulos else ""
    return {
        "empresa": identidad, "empresa_original": identidad,
        "modo_app": True, "app_home_disponible": False, "app_base_url": "",
        "configuracion_existe": bool(config.pk),
        # Sólo presencia, no validez, longitud, fragmentos ni copia del secreto.
        "mobile_crm_token_present": bool(config.whatsapp_token),
        "mobile_crm_summary": {
            "whatsapp_activo": config.whatsapp_activo,
            "recordatorio_citas_activo": config.recordatorio_citas_activo,
            "recordatorio_cumpleanos_activo": config.recordatorio_cumpleanos_activo,
            "whatsapp_numero_conectado": config.whatsapp_numero_conectado,
        },
        "mobile_crm_confirmation_name": empresa.nombre,
        "can_view_crm_configuration": True,
        "can_view_clinical_configuration": puede_config_clinica,
        "can_view_app_modules": puede_modulos,
        "app_crm_configuration_url": propia_url, "app_crm_form_url": propia_url,
        "app_crm_back_url": config_clinica_url or modulos_url or propia_url,
        "app_clinical_configuration_url": config_clinica_url, "app_modules_url": modulos_url,
    }


def _sin_cache(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def render_configuracion_crm_app(request, form, contexto):
    contexto = dict(contexto)
    contexto.update({
        "form": form,
        "mobile_crm_sections": [
            {"titulo": titulo, "campos": [form[nombre] for nombre in nombres]}
            for titulo, nombres in SECCIONES_CRM_APP
        ],
    })
    return _sin_cache(render(request, "crm/mobile_configuration_form.html", contexto))


def volver_configuracion_crm_app(empresa):
    return _sin_cache(redirect(f"{reverse('crm_configuracion', args=[empresa.slug])}?app=1"))
