"""Server-side checks for connecting a customer's WhatsApp assets.

No number is registered, migrated, deleted or transferred here. In particular,
coexistence onboarding must finish in Meta before the CRM tests messaging.
"""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from .services import WhatsAppAPIError


class MetaOnboardingError(WhatsAppAPIError):
    """Safe to display: never includes credentials or Meta's raw error body."""


_TIMEOUT = 20
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_PAGES = 10
_REQUIRED_SCOPES = {"whatsapp_business_management", "whatsapp_business_messaging"}


def _value(value, label, maximum=120):
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise MetaOnboardingError(f"Falta un {label} valido para conectar WhatsApp.")
    return result


def _version():
    version = str(settings.META_WHATSAPP_GRAPH_VERSION or "").strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise MetaOnboardingError("La version de la API de Meta no esta configurada correctamente.")
    return version


def _request(path, *, token=None, query=None, method="GET", payload=None):
    # Build only our own graph.facebook.com URLs. Never follow a pagination URL
    # returned by the API: it may contain a token or point at another host.
    url = f"https://graph.facebook.com/{_version()}/{path}"
    if query:
        url += "?" + urlencode(query)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise MetaOnboardingError("La respuesta de Meta excede el limite permitido.")
        result = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        # Do not include str(exc), response bodies or request URLs: OAuth URLs
        # and Meta errors may contain credentials or a one-time code.
        status = int(exc.code)
        if status in {401, 403}:
            message = "Meta rechazo el acceso. Revise los permisos de la aplicacion y la autorizacion de la cuenta."
        else:
            message = f"Meta no pudo validar la conexion (HTTP {status}). Intente nuevamente."
        raise MetaOnboardingError(message) from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise MetaOnboardingError("No fue posible obtener una respuesta valida de Meta. Intente nuevamente.") from None
    if not isinstance(result, dict) or "error" in result:
        raise MetaOnboardingError("Meta no pudo completar la validacion de WhatsApp.")
    return result


def _collection(path, token, query=None):
    params = dict(query or {})
    params.setdefault("limit", 100)
    items = []
    seen_cursors = set()
    for _ in range(_MAX_PAGES):
        result = _request(path, token=token, query=params)
        data = result.get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise MetaOnboardingError("Meta devolvio una lista de activos no valida.")
        items.extend(data)
        paging = result.get("paging") or {}
        if not isinstance(paging, dict):
            raise MetaOnboardingError("Meta devolvio una paginacion no valida.")
        if not paging.get("next"):
            return items
        cursors = paging.get("cursors") or {}
        after = cursors.get("after") if isinstance(cursors, dict) else None
        if not isinstance(after, str) or not after or len(after) > 4096 or after in seen_cursors:
            raise MetaOnboardingError("No fue posible comprobar todos los activos de la cuenta de Meta.")
        seen_cursors.add(after)
        params["after"] = after
    raise MetaOnboardingError("La cuenta tiene demasiados activos para validarlos de forma automatica.")


def _verify_token(token):
    app_id = _value(settings.META_WHATSAPP_APP_ID, "identificador de aplicacion")
    secret = _value(settings.META_WHATSAPP_APP_SECRET, "secreto de aplicacion", 4096)
    result = _request("debug_token", token=f"{app_id}|{secret}", query={"input_token": token})
    data = result.get("data")
    if not isinstance(data, dict) or data.get("is_valid") is not True:
        raise MetaOnboardingError("La autorizacion de Meta no es valida o ha vencido. Conecte la cuenta nuevamente.")
    if str(data.get("app_id") or "") != app_id:
        raise MetaOnboardingError("La autorizacion de WhatsApp pertenece a otra aplicacion.")
    scopes = data.get("scopes")
    if not isinstance(scopes, list) or not _REQUIRED_SCOPES.issubset({s for s in scopes if isinstance(s, str)}):
        raise MetaOnboardingError("Meta no autorizo los permisos necesarios para enviar mensajes y administrar WhatsApp.")


def _phone_for_account(account_id, token, phone_id=None):
    account_id = _value(account_id, "identificador de cuenta")
    phones = _collection(
        f"{quote(account_id, safe='')}/phone_numbers", token,
        {"fields": "id,display_phone_number,verified_name"},
    )
    if phone_id:
        phone_id = _value(phone_id, "identificador de numero")
        matches = [phone for phone in phones if str(phone.get("id") or "") == phone_id]
        if len(matches) != 1:
            raise MetaOnboardingError("El numero autorizado no pertenece a la cuenta de WhatsApp seleccionada.")
        phone = matches[0]
    elif len(phones) == 1:
        phone = phones[0]
    elif not phones:
        raise MetaOnboardingError("La cuenta autorizada no tiene un numero disponible. Complete el registro en Meta.")
    else:
        raise MetaOnboardingError("Meta no indico que numero eligio. Seleccione un numero concreto y vuelva a conectar.")
    return {
        "business_account_id": account_id,
        "phone_number_id": _value(phone.get("id"), "identificador de numero"),
        "display_phone_number": _value(phone.get("display_phone_number"), "numero de telefono", 60),
        "verified_name": str(phone.get("verified_name") or "")[:255],
    }


def _is_subscribed(account_id, token):
    app_id = str(settings.META_WHATSAPP_APP_ID or "").strip()
    if not app_id:
        raise MetaOnboardingError("Falta configurar el identificador de la aplicacion de Meta.")
    apps = _collection(f"{quote(account_id, safe='')}/subscribed_apps", token)
    for item in apps:
        app = item.get("whatsapp_business_api_data", item)
        if isinstance(app, dict) and str(app.get("id") or "") == app_id:
            return True
    return False


def complete_connection(code, account_id, phone_id=None):
    """Validate assets, subscribe this app, and return a PAUSED connection.

    The caller persists the result for its authenticated company and must keep
    automations paused until an explicit test and activation have succeeded.
    """
    code = _value(code, "codigo de autorizacion", 4096)
    account_id = _value(account_id, "identificador de cuenta")
    app_id = _value(settings.META_WHATSAPP_APP_ID, "identificador de aplicacion")
    secret = _value(settings.META_WHATSAPP_APP_SECRET, "secreto de aplicacion", 4096)
    payload = _request("oauth/access_token", query={"client_id": app_id, "client_secret": secret, "code": code})
    token = _value(payload.get("access_token"), "token de autorizacion", 16384)
    _verify_token(token)
    result = _phone_for_account(account_id, token, phone_id)
    if not _is_subscribed(account_id, token):
        subscribed = _request(f"{quote(account_id, safe='')}/subscribed_apps", token=token, method="POST", payload={})
        if subscribed.get("success") is not True:
            raise MetaOnboardingError("Meta no confirmo la suscripcion de los avisos de WhatsApp.")
    return {**result, "access_token": token, "webhook_subscribed": True, "readiness": "pending_test"}


def verify_connection(config):
    """Recheck the stored token, account membership and subscription, read-only."""
    token = _value(config.whatsapp_token, "token de autorizacion", 16384)
    _verify_token(token)
    result = _phone_for_account(config.whatsapp_business_account_id, token, config.whatsapp_phone_number_id)
    if not _is_subscribed(result["business_account_id"], token):
        raise MetaOnboardingError("La aplicacion no esta suscrita a esta cuenta. Vuelva a conectar WhatsApp.")
    return {**result, "webhook_subscribed": True, "readiness": "pending_test"}


def validate_template(config, name, language, expected_body_parameters=None):
    """Check the exact approved template and positional body schema at Meta.

    When a body count is supplied this is for the CRM's body-only sender. Reject
    dynamic headers/buttons and named parameters that this sender cannot fill.
    """
    token = _value(config.whatsapp_token, "token de autorizacion", 16384)
    account_id = _value(config.whatsapp_business_account_id, "identificador de cuenta")
    name = _value(name, "nombre de plantilla", 512)
    language = _value(language, "idioma de plantilla", 20)
    templates = _collection(
        f"{quote(account_id, safe='')}/message_templates", token,
        {"name": name, "fields": "id,name,language,status,components,parameter_format"},
    )
    matches = [item for item in templates if item.get("name") == name and item.get("language") == language]
    if len(matches) != 1:
        raise MetaOnboardingError("La plantilla y el idioma elegidos no existen en la cuenta autorizada de WhatsApp.")
    template = matches[0]
    if template.get("status") != "APPROVED":
        raise MetaOnboardingError("La plantilla elegida todavia no esta aprobada por Meta.")
    components = template.get("components") or []
    if not isinstance(components, list) or any(not isinstance(c, dict) for c in components):
        raise MetaOnboardingError("Meta devolvio una estructura de plantilla no valida.")
    bodies = [c for c in components if str(c.get("type", "")).upper() == "BODY"]
    if len(bodies) != 1 or not isinstance(bodies[0].get("text"), str):
        raise MetaOnboardingError("La plantilla debe tener un cuerpo de texto valido.")
    placeholders = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", bodies[0]["text"])
    if any(not p.isdigit() or int(p) < 1 for p in placeholders):
        raise MetaOnboardingError("La plantilla usa variables con nombre. El CRM requiere variables numeradas.")
    indexes = {int(p) for p in placeholders}
    if indexes != set(range(1, len(indexes) + 1)):
        raise MetaOnboardingError("Las variables de la plantilla deben ser consecutivas desde 1.")
    if expected_body_parameters is not None:
        if len(indexes) != expected_body_parameters:
            raise MetaOnboardingError("La cantidad de variables de la plantilla no coincide con el mensaje del CRM.")
        for component in components:
            kind = str(component.get("type", "")).upper()
            if kind == "HEADER" and (component.get("format") != "TEXT" or "{{" in str(component.get("text", ""))):
                raise MetaOnboardingError("La plantilla requiere un encabezado dinamico que este envio no proporciona.")
            if kind == "BUTTONS":
                buttons = component.get("buttons") or []
                if not isinstance(buttons, list) or any(
                    not isinstance(b, dict) or "{{" in str(b.get("url", "")) for b in buttons
                ):
                    raise MetaOnboardingError("La plantilla requiere botones dinamicos que este envio no proporciona.")
    return {"id": str(template.get("id") or ""), "name": name, "language": language,
            "status": "APPROVED", "body_parameters": len(indexes)}
