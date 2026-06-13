import hashlib
import secrets


TOKEN_PREFIX = "DVS-RSP-"


def generar_token_respaldo():
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"
    return token, hash_token_respaldo(token), f"{token[:12]}...{token[-4:]}"


def hash_token_respaldo(token):
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()
