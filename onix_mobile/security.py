import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    configured = str(getattr(settings, "ONIX_CONNECTION_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        key = configured.encode("ascii")
    elif settings.DEBUG:
        digest = hashlib.sha256(f"{settings.SECRET_KEY}:onix-connections".encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    else:
        raise ImproperlyConfigured(
            "ONIX_CONNECTION_ENCRYPTION_KEY es obligatoria para guardar conexiones externas."
        )
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            "ONIX_CONNECTION_ENCRYPTION_KEY debe ser una clave Fernet valida."
        ) from exc


def validar_cifrado_conexiones():
    """Falla antes del OAuth si el servidor no puede proteger los tokens."""
    _fernet()


def cifrar_secreto(valor):
    if not valor:
        return ""
    return _fernet().encrypt(str(valor).encode("utf-8")).decode("ascii")


def descifrar_secreto(valor):
    if not valor:
        return ""
    try:
        return _fernet().decrypt(str(valor).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("No fue posible descifrar la conexion externa.") from exc
