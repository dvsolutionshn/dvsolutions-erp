import json
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def google_configurado():
    return bool(
        getattr(settings, "ONIX_GOOGLE_CLIENT_ID", "")
        and getattr(settings, "ONIX_GOOGLE_CLIENT_SECRET", "")
    )


def google_redirect_uri():
    configured = str(getattr(settings, "ONIX_GOOGLE_REDIRECT_URI", "") or "").strip()
    if configured:
        return configured
    return f"{settings.PUBLIC_BASE_URL}/api/onix/mobile/v1/connections/google/callback/"


def construir_autorizacion_google(estado):
    if not google_configurado():
        raise ValidationError("La conexion con Google todavia no esta configurada en el servidor.")
    scopes = getattr(settings, "ONIX_GOOGLE_SCOPES", []) or [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    return GOOGLE_AUTH_URL + "?" + urlencode(
        {
            "client_id": settings.ONIX_GOOGLE_CLIENT_ID,
            "redirect_uri": google_redirect_uri(),
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": estado,
        }
    )


def _json_request(url, *, data=None, token=""):
    body = urlencode(data).encode("utf-8") if data is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValidationError("Google no pudo completar la autorizacion. Intenta nuevamente.") from exc


def intercambiar_codigo_google(codigo):
    token = _json_request(
        GOOGLE_TOKEN_URL,
        data={
            "code": codigo,
            "client_id": settings.ONIX_GOOGLE_CLIENT_ID,
            "client_secret": settings.ONIX_GOOGLE_CLIENT_SECRET,
            "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code",
        },
    )
    acceso = str(token.get("access_token") or "")
    if not acceso:
        raise ValidationError("Google no entrego una autorizacion valida.")
    cuenta = _json_request(GOOGLE_USERINFO_URL, token=acceso)
    segundos = max(60, int(token.get("expires_in") or 3600))
    return {
        "access_token": acceso,
        "refresh_token": str(token.get("refresh_token") or ""),
        "expires_at": timezone.now() + timedelta(seconds=segundos),
        "scope": str(token.get("scope") or "").split(),
        "email": str(cuenta.get("email") or "")[:254],
        "name": str(cuenta.get("name") or "")[:180],
        "subject": str(cuenta.get("sub") or "")[:180],
    }
