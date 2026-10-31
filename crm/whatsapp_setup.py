"""Explicit activation after an authorized, delivered WhatsApp test."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Empresa
from .meta_onboarding import MetaOnboardingError, validate_template, verify_connection
from .models import ConfiguracionCRM, MensajeWhatsAppWebhook
from .mobile_crm_configuration import denegar_accion_crm_no_adaptada_app


def bloquear_asignaciones_whatsapp():
    """Serialize asset assignments across companies without rewriting legacy data.

    Call inside transaction.atomic(), before reading duplicates or taking a
    company/configuration lock. The oldest existing company is a stable common
    lock for callbacks and the manual configuration form in PostgreSQL.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("La asignacion de WhatsApp requiere una transaccion.")
    return Empresa.objects.order_by("pk").select_for_update().first()


def autorizar_configuracion_whatsapp(request, empresa):
    user = request.user
    if user.is_superuser:
        return
    if not (
        user.puede_acceder_empresa(empresa) and empresa.licencia_operativa
        and empresa.tiene_modulo_activo("crm_marketing")
        and (user.es_administrador_empresa or user.tiene_permiso_erp("puede_configuracion_crm", empresa))
    ):
        raise PermissionDenied("No tiene permiso para configurar WhatsApp en esta empresa.")


def prueba_entregada(config):
    return bool(config.whatsapp_prueba_message_id and MensajeWhatsAppWebhook.objects.filter(
        empresa_id=config.empresa_id, message_id=config.whatsapp_prueba_message_id,
        cuenta_id=config.whatsapp_business_account_id,
        phone_number_id=config.whatsapp_phone_number_id,
        direccion="saliente", estado__in=["delivered", "read"],
    ).exists())


@login_required
@require_POST
def activar_whatsapp(request, empresa_slug):
    denegar_accion_crm_no_adaptada_app(request)
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    autorizar_configuracion_whatsapp(request, empresa)
    config = get_object_or_404(ConfiguracionCRM, empresa=empresa)
    destino = redirect("crm_configuracion", empresa_slug=empresa.slug)
    if not config.whatsapp_pendiente_validacion:
        messages.info(request, "La conexion no tiene una validacion pendiente.")
        return destino
    if request.POST.get("confirmar_activacion") != "si" or not prueba_entregada(config):
        messages.error(request, "Espera la entrega de la prueba y confirma la activacion de los envios.")
        return destino
    try:
        verify_connection(config)
        if config.recordatorio_citas_activo:
            validate_template(config, config.whatsapp_plantilla_cita, config.whatsapp_idioma_cita,
                              expected_body_parameters=7 if config.whatsapp_cita_incluir_enlace else 6)
        if config.recordatorio_cumpleanos_activo:
            validate_template(config, config.whatsapp_plantilla_marketing, config.whatsapp_idioma_marketing,
                              expected_body_parameters=4)
    except MetaOnboardingError as exc:
        messages.error(request, str(exc))
        return destino
    with transaction.atomic():
        actual = ConfiguracionCRM.objects.select_for_update().get(pk=config.pk)
        campos = ("whatsapp_phone_number_id", "whatsapp_business_account_id", "whatsapp_token", "whatsapp_api_version",
                  "whatsapp_prueba_message_id", "whatsapp_plantilla_cita", "whatsapp_idioma_cita",
                  "whatsapp_cita_incluir_enlace", "recordatorio_citas_activo", "recordatorio_cumpleanos_activo",
                  "whatsapp_plantilla_marketing", "whatsapp_idioma_marketing")
        if any(getattr(actual, campo) != getattr(config, campo) for campo in campos) or not prueba_entregada(actual):
            messages.error(request, "La configuracion cambio durante la validacion. Repite la prueba.")
            return destino
        actual.whatsapp_pendiente_validacion = False
        actual.whatsapp_activo = True
        actual.whatsapp_validado_en = timezone.now()
        actual.save(update_fields=["whatsapp_pendiente_validacion", "whatsapp_activo", "whatsapp_validado_en"])
    messages.success(request, "WhatsApp validado. Se habilitaron los envios y recordatorios configurados para esta empresa.")
    return destino


@login_required
def mensajes_whatsapp(request, empresa_slug):
    denegar_accion_crm_no_adaptada_app(request)
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    autorizar_configuracion_whatsapp(request, empresa)
    from django.core.paginator import Paginator
    registros = MensajeWhatsAppWebhook.objects.filter(empresa=empresa)
    response = render(request, "crm/whatsapp_mensajes.html", {
        "empresa": empresa, "pagina": Paginator(registros, 50).get_page(request.GET.get("pagina")),
    })
    response["Cache-Control"] = "no-store, private"
    return response
