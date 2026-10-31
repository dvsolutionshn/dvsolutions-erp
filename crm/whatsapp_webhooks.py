"""Verified Cloud API callbacks. Incoming text never changes an appointment by guesswork."""
from datetime import datetime, timezone as datetime_timezone
import hashlib
import hmac
import json
import re

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import (
    ConfiguracionCRM, MensajeWhatsAppWebhook,
    NotificacionCitaWhatsApp, NotificacionCumpleanosWhatsApp,
)


def _string(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def _id(value, limit):
    return value if isinstance(value, str) and len(value) <= limit else ""


def _timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _configuracion_autorizada(account_id, phone_number_id):
    """Both Meta asset IDs must match exactly one configured company."""
    if not account_id or not phone_number_id:
        return None
    matches = list(ConfiguracionCRM.objects.filter(
        whatsapp_business_account_id=account_id,
        whatsapp_phone_number_id=phone_number_id,
    ).only("empresa_id", "whatsapp_phone_number_id", "whatsapp_business_account_id")[:2])
    return matches[0] if len(matches) == 1 else None


def _texto_mensaje(message):
    kind = message.get("type")
    if kind == "text":
        text = message.get("text")
        return _string(text.get("body"), 4096) if isinstance(text, dict) else ""
    if kind == "button":
        button = message.get("button")
        return _string(button.get("text"), 4096) if isinstance(button, dict) else ""
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        if not isinstance(interactive, dict):
            return ""
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return _string(reply.get("title"), 4096) if isinstance(reply, dict) else ""
    # Avoid retaining medical documents, photos, URLs or the complete webhook payload.
    return ""


def _recibir_mensaje(config, message):
    message_id = _id(message.get("id"), 255)
    if not message_id:
        return False
    message_record, _ = MensajeWhatsAppWebhook.objects.get_or_create(
        empresa_id=config.empresa_id, message_id=message_id,
        defaults={
            "direccion": "entrante",
            "telefono_cliente": _string(message.get("from"), 40),
            "phone_number_id": config.whatsapp_phone_number_id,
            "cuenta_id": config.whatsapp_business_account_id,
            "tipo": _string(message.get("type"), 30),
            "texto": _texto_mensaje(message),
            "estado": "received",
            "fecha_meta": _timestamp(message.get("timestamp")),
        },
    )
    return (
        message_record.direccion == "entrante"
        and message_record.cuenta_id == config.whatsapp_business_account_id
        and message_record.phone_number_id == config.whatsapp_phone_number_id
    )


def _avance_estado(current, new):
    """Delivery/read evidence wins over late sent/failed callbacks."""
    rank = {"sent": 1, "failed": 1, "delivered": 2, "read": 3}
    if current == new or current == "received":
        return False
    if current == "failed" and new == "sent":
        return False
    return rank.get(new, 0) > rank.get(current, 0) or (current == "sent" and new == "failed")


def _actualizar_notificaciones(config, message):
    """Keep 'enviado' (API accepted) separate from the actual delivery receipt."""
    filters = {"respuesta__messages__0__id": message.message_id}
    queries = [
        NotificacionCitaWhatsApp.objects.filter(cita__empresa_id=config.empresa_id, **filters),
        NotificacionCumpleanosWhatsApp.objects.filter(empresa_id=config.empresa_id, **filters),
    ]
    for query in queries:
        for notification in query.select_for_update():
            response = dict(notification.respuesta or {})
            response["webhook"] = {
                "status": message.estado,
                "timestamp": message.estado_actualizado_en.isoformat() if message.estado_actualizado_en else None,
                "error_code": message.error_codigo,
            }
            notification.respuesta = response
            notification.save(update_fields=["respuesta", "fecha_actualizacion"])


@transaction.atomic
def _recibir_estado(config, status):
    message_id = _id(status.get("id"), 255)
    state = status.get("status")
    if not message_id or not isinstance(state, str) or state not in {"sent", "delivered", "read", "failed"}:
        return False
    moment = _timestamp(status.get("timestamp"))
    errors = status.get("errors") or []
    error_code = str(errors[0].get("code", ""))[:80] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else ""
    message, created = MensajeWhatsAppWebhook.objects.get_or_create(
        empresa_id=config.empresa_id, message_id=message_id,
        defaults={
            "direccion": "saliente", "telefono_cliente": _string(status.get("recipient_id"), 40),
            "phone_number_id": config.whatsapp_phone_number_id,
            "cuenta_id": config.whatsapp_business_account_id,
            "estado": state, "fecha_meta": moment, "estado_actualizado_en": moment,
            "error_codigo": error_code,
        },
    )
    message = MensajeWhatsAppWebhook.objects.select_for_update().get(pk=message.pk)
    if (
        message.direccion != "saliente"
        or message.cuenta_id != config.whatsapp_business_account_id
        or message.phone_number_id != config.whatsapp_phone_number_id
    ):
        return False
    if not created and _avance_estado(message.estado, state):
        # A delayed failed callback must not override a newer sent timestamp.
        if state == "failed" and moment and message.estado_actualizado_en and moment < message.estado_actualizado_en:
            return True
        message.estado = state
        message.estado_actualizado_en = moment
        message.error_codigo = error_code if state == "failed" else ""
        message.save(update_fields=["estado", "estado_actualizado_en", "error_codigo"])
    _actualizar_notificaciones(config, message)
    return True


def procesar_webhook(payload):
    """Persist only messages from an unambiguous account/phone pair; unknown assets are ignored."""
    if payload.get("object") != "whatsapp_business_account":
        return
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        account_id = _id(entry.get("id"), 120)
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                continue
            config = _configuracion_autorizada(account_id, _id(metadata.get("phone_number_id"), 120))
            if config is None:
                continue
            messages = value.get("messages", [])
            if isinstance(messages, list):
                for message in messages:
                    if isinstance(message, dict):
                        _recibir_mensaje(config, message)
            statuses = value.get("statuses", [])
            if isinstance(statuses, list):
                for status in statuses:
                    if isinstance(status, dict):
                        _recibir_estado(config, status)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    if request.method == "GET":
        expected = getattr(settings, "META_WHATSAPP_VERIFY_TOKEN", "")
        supplied = request.GET.get("hub.verify_token", "")
        if not expected:
            return JsonResponse({"ok": False}, status=503)
        if request.GET.get("hub.mode") != "subscribe" or not hmac.compare_digest(str(expected).encode(), supplied.encode()):
            return JsonResponse({"ok": False}, status=403)
        challenge = request.GET.get("hub.challenge", "")
        if not challenge:
            return JsonResponse({"ok": False}, status=400)
        return HttpResponse(challenge, content_type="text/plain")

    limit = getattr(settings, "META_WHATSAPP_WEBHOOK_MAX_BYTES", 1024 * 1024)
    try:
        body = request.body
    except RequestDataTooBig:
        return JsonResponse({"ok": False}, status=413)
    if len(body) > limit:
        return JsonResponse({"ok": False}, status=413)
    secret = getattr(settings, "META_WHATSAPP_APP_SECRET", "")
    if not secret:
        return JsonResponse({"ok": False}, status=503)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature):
        return JsonResponse({"ok": False}, status=403)
    expected = "sha256=" + hmac.new(str(secret).encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        return JsonResponse({"ok": False}, status=403)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False}, status=400)
    procesar_webhook(payload)
    return JsonResponse({"ok": True})
