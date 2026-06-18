import hashlib
import secrets


def hash_token_preconsulta(token):
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def generar_token_preconsulta():
    token = secrets.token_urlsafe(32)
    return token, hash_token_preconsulta(token), token[:8]
