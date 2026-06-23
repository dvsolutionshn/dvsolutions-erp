from django.core import signing
from django.urls import reverse


CITA_TOKEN_SALT = "dvsolutions.crm.cita-respuesta"
CITA_TOKEN_MAX_AGE = 60 * 60 * 24 * 60


def generar_token_respuesta_cita(cita):
    return signing.dumps(
        {"cita_id": cita.id, "empresa": cita.empresa.slug},
        salt=CITA_TOKEN_SALT,
    )


def leer_token_respuesta_cita(token):
    return signing.loads(
        token,
        salt=CITA_TOKEN_SALT,
        max_age=CITA_TOKEN_MAX_AGE,
    )


def construir_url_respuesta_cita(cita, request=None, base_url=""):
    path = reverse("crm_cita_respuesta_publica", args=[generar_token_respuesta_cita(cita)])
    if request is not None:
        return request.build_absolute_uri(path)
    return f"{(base_url or '').rstrip('/')}{path}"
