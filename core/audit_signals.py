from datetime import date, datetime, time
from decimal import Decimal
import logging
import uuid

from django.db import OperationalError, ProgrammingError
from django.db.models import ForeignKey
from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .audit_context import get_audit_context


logger = logging.getLogger(__name__)
AUDITED_APPS = {"core", "facturacion", "contabilidad", "rrhh", "crm", "clinica"}
EXCLUDED_MODELS = {"registroauditoria"}
SENSITIVE_PARTS = {"password", "contrasena", "secret", "token", "credential", "api_key", "hash"}
MODULE_LABELS = {
    "core": "Configuracion",
    "facturacion": "Facturacion",
    "contabilidad": "Contabilidad",
    "rrhh": "Recursos Humanos",
    "crm": "CRM y Agenda",
    "clinica": "Clinica",
}
_NOT_SET = object()


def _auditable(sender):
    meta = getattr(sender, "_meta", None)
    return bool(
        meta
        and not meta.auto_created
        and meta.app_label in AUDITED_APPS
        and meta.model_name not in EXCLUDED_MODELS
    )


def _sensitive(field_name):
    name = (field_name or "").lower()
    return any(part in name for part in SENSITIVE_PARTS)


def _serialize(value, field_name="", depth=0):
    if _sensitive(field_name):
        return "[PROTEGIDO]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if depth < 2 and isinstance(value, dict):
        return {
            str(key)[:120]: _serialize(item, str(key), depth + 1)
            for key, item in list(value.items())[:100]
        }
    if depth < 2 and isinstance(value, (list, tuple, set)):
        return [_serialize(item, field_name, depth + 1) for item in list(value)[:100]]
    text = str(value)
    return text if len(text) <= 1200 else f"{text[:1200]}..."


def _snapshot(instance):
    values = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key:
            continue
        field_name = field.name
        try:
            value = field.value_from_object(instance)
        except Exception:
            continue
        values[field_name] = _serialize(value, field_name)
    return values


def _changes(previous, current, created=False):
    result = {}
    if created:
        for field_name, value in current.items():
            if value not in (None, "", [], {}, False):
                result[field_name] = {"anterior": None, "nuevo": value}
        return result
    for field_name in sorted(set(previous) | set(current)):
        old = previous.get(field_name)
        new = current.get(field_name)
        if old != new:
            result[field_name] = {"anterior": old, "nuevo": new}
    return result


def _request_data(action):
    context = get_audit_context()
    request = context.get("request")
    user = context.get("user")
    reason = (context.get("reason") or "").strip()
    path = ""
    method = ""
    ip = None
    user_agent = ""
    if request is not None:
        request_user = getattr(request, "user", None)
        if request_user is not None and getattr(request_user, "is_authenticated", False):
            user = request_user
        path = (getattr(request, "path", "") or "")[:500]
        method = (getattr(request, "method", "") or "")[:10]
        forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        ip = forwarded or request.META.get("REMOTE_ADDR") or None
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:500]
        if not reason and method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                for key in ("motivo_auditoria", "_audit_reason", "motivo", "razon", "motivo_eliminacion"):
                    candidate = (request.POST.get(key) or "").strip()
                    if candidate:
                        reason = candidate
                        break
            except Exception:
                pass
    if not reason:
        if path:
            reason = f"{action.capitalize()} desde {path}"
        else:
            reason = "Proceso automatico del sistema"
    return {
        "user": user if getattr(user, "pk", None) else None,
        "reason": reason[:500],
        "path": path,
        "method": method,
        "ip": ip,
        "user_agent": user_agent,
        "request_id": context.get("request_id") or uuid.uuid4(),
    }


def _empresa_id(instance, user=None):
    if instance._meta.app_label == "core" and instance._meta.model_name == "empresa":
        return instance.pk
    direct = getattr(instance, "empresa_id", None)
    if direct:
        return direct
    for field in instance._meta.concrete_fields:
        if not isinstance(field, ForeignKey) or field.name in {"empresa", "usuario", "creado_por", "actualizado_por"}:
            continue
        related_model = field.remote_field.model
        try:
            related_model._meta.get_field("empresa")
        except Exception:
            continue
        related_id = getattr(instance, field.attname, None)
        if related_id:
            empresa_id = related_model._default_manager.filter(pk=related_id).values_list("empresa_id", flat=True).first()
            if empresa_id:
                return empresa_id
    return getattr(user, "empresa_id", None)


def _record(instance, action, changes, *, object_repr=None, empresa_id_override=_NOT_SET):
    from .models import RegistroAuditoria

    request_data = _request_data(action)
    empresa_id = (
        _empresa_id(instance, request_data["user"])
        if empresa_id_override is _NOT_SET
        else empresa_id_override
    )
    try:
        RegistroAuditoria.objects.create(
            empresa_id=empresa_id,
            usuario=request_data["user"],
            accion=action,
            modulo=MODULE_LABELS.get(instance._meta.app_label, instance._meta.app_label.title()),
            app_label=instance._meta.app_label,
            modelo=instance._meta.model_name,
            objeto_id=str(instance.pk or ""),
            objeto_representacion=(object_repr or str(instance) or instance._meta.verbose_name)[:300],
            cambios=changes,
            motivo=request_data["reason"],
            ruta=request_data["path"],
            metodo_http=request_data["method"],
            direccion_ip=request_data["ip"],
            agente_usuario=request_data["user_agent"],
            identificador_solicitud=request_data["request_id"],
        )
    except (OperationalError, ProgrammingError):
        logger.debug("La tabla de auditoria aun no esta disponible.", exc_info=True)
    except Exception:
        logger.exception("No se pudo registrar auditoria para %s.%s", instance._meta.app_label, instance._meta.model_name)


@receiver(pre_save)
def capture_previous_state(sender, instance, raw=False, **kwargs):
    if raw or not _auditable(sender) or not instance.pk:
        return
    previous = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_previous_state = _snapshot(previous) if previous else {}


@receiver(post_save)
def record_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not _auditable(sender):
        return
    current = _snapshot(instance)
    changes = _changes(getattr(instance, "_audit_previous_state", {}), current, created=created)
    if not created and not changes:
        return
    action = "crear" if created else "modificar"
    _record(instance, action, changes)


@receiver(pre_delete)
def record_delete(sender, instance, **kwargs):
    if not _auditable(sender):
        return
    empresa_override = None if instance._meta.model_name == "empresa" else _empresa_id(instance)
    _record(
        instance,
        "eliminar",
        {"registro_eliminado": {"anterior": _snapshot(instance), "nuevo": None}},
        object_repr=str(instance),
        empresa_id_override=empresa_override,
    )


@receiver(m2m_changed)
def record_m2m(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"} or not _auditable(instance.__class__):
        return
    _record(
        instance,
        "relacion",
        {"relacion": {
            "anterior": None,
            "nuevo": {
                "operacion": action.replace("post_", ""),
                "modelo_relacionado": model._meta.label_lower,
                "ids": sorted(str(pk) for pk in (pk_set or [])),
                "inversa": reverse,
            },
        }},
    )
