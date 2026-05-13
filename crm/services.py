import json
import mimetypes
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError


MAX_WHATSAPP_IMAGE_BYTES = 5 * 1024 * 1024
TARGET_WHATSAPP_IMAGE_BYTES = 4_500_000
MAX_WHATSAPP_IMAGE_SIDE = 1600


class WhatsAppAPIError(Exception):
    pass


def normalizar_telefono_hn(numero):
    telefono = "".join(ch for ch in (numero or "") if ch.isdigit())
    if telefono and not telefono.startswith("504") and len(telefono) == 8:
        telefono = f"504{telefono}"
    return telefono


def _endpoint(config):
    version = (config.whatsapp_api_version or "v25.0").strip()
    phone_id = (config.whatsapp_phone_number_id or "").strip()
    if not phone_id:
        raise WhatsAppAPIError("Falta el identificador de numero de telefono de WhatsApp.")
    return f"https://graph.facebook.com/{version}/{phone_id}/messages"


def _media_endpoint(config):
    version = (config.whatsapp_api_version or "v25.0").strip()
    phone_id = (config.whatsapp_phone_number_id or "").strip()
    if not phone_id:
        raise WhatsAppAPIError("Falta el identificador de numero de telefono de WhatsApp.")
    return f"https://graph.facebook.com/{version}/{phone_id}/media"


def _post_whatsapp(config, payload):
    token = (config.whatsapp_token or "").strip()
    if not token:
        raise WhatsAppAPIError("Falta el token de WhatsApp Cloud API.")
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        _endpoint(config),
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise WhatsAppAPIError(f"Meta respondio con error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise WhatsAppAPIError(f"No se pudo conectar con WhatsApp Cloud API: {exc}") from exc


def _post_multipart(config, url, fields, file_field, file_path, content_type):
    token = (config.whatsapp_token or "").strip()
    if not token:
        raise WhatsAppAPIError("Falta el token de WhatsApp Cloud API.")

    boundary = f"----DVSolutionsERP{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    file_path = Path(file_path)
    filename = file_path.name
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    with file_path.open("rb") as file_obj:
        body.extend(file_obj.read())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = Request(
        url,
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body_text = response.read().decode("utf-8")
            return json.loads(body_text or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise WhatsAppAPIError(f"Meta respondio con error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise WhatsAppAPIError(f"No se pudo conectar con WhatsApp Cloud API: {exc}") from exc


def _guardar_imagen_optimizada(imagen, file_path, calidad):
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temporal:
        ruta_temporal = Path(temporal.name)
    imagen.save(ruta_temporal, format="JPEG", quality=calidad, optimize=True)
    if ruta_temporal.stat().st_size <= TARGET_WHATSAPP_IMAGE_BYTES:
        return ruta_temporal
    ruta_temporal.unlink(missing_ok=True)
    return None


def _optimizar_imagen_para_whatsapp(file_path, content_type):
    if file_path.stat().st_size <= MAX_WHATSAPP_IMAGE_BYTES:
        return file_path, content_type, None

    try:
        with Image.open(file_path) as imagen_original:
            imagen_original.verify()
        with Image.open(file_path) as imagen_original:
            imagen = imagen_original.copy()
    except UnidentifiedImageError as exc:
        raise WhatsAppAPIError("La media promocional debe ser una imagen valida.") from exc

    if imagen.mode in ("RGBA", "LA", "P"):
        fondo = Image.new("RGB", imagen.size, "white")
        if imagen.mode == "P":
            imagen = imagen.convert("RGBA")
        fondo.paste(imagen, mask=imagen.getchannel("A") if "A" in imagen.getbands() else None)
        imagen = fondo
    else:
        imagen = imagen.convert("RGB")

    imagen.thumbnail((MAX_WHATSAPP_IMAGE_SIDE, MAX_WHATSAPP_IMAGE_SIDE), Image.Resampling.LANCZOS)
    for calidad in (85, 75, 65, 55, 45, 35):
        ruta_temporal = _guardar_imagen_optimizada(imagen, file_path, calidad)
        if ruta_temporal:
            return ruta_temporal, "image/jpeg", ruta_temporal

    raise WhatsAppAPIError(
        "La imagen promocional es demasiado grande para WhatsApp. "
        "Sube una imagen mas liviana, idealmente menor a 5 MB."
    )


def enviar_plantilla_whatsapp(config, numero, nombre_plantilla=None, idioma=None):
    telefono = normalizar_telefono_hn(numero)
    if not telefono:
        raise WhatsAppAPIError("Falta el numero destino para WhatsApp.")
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "template",
        "template": {
            "name": nombre_plantilla or config.whatsapp_plantilla_prueba or "hello_world",
            "language": {"code": idioma or config.whatsapp_idioma_plantilla or "en_US"},
        },
    }
    return _post_whatsapp(config, payload)


def _texto_parametro(valor, fallback="-"):
    texto = str(valor or fallback).strip()
    return texto or fallback


def enviar_plantilla_marketing_whatsapp(
    config,
    numero,
    *,
    nombre_cliente,
    promocion,
    vigencia,
    enlace,
    media_id=None,
):
    telefono = normalizar_telefono_hn(numero)
    if not telefono:
        raise WhatsAppAPIError("Falta el numero destino para WhatsApp.")

    components = []
    if media_id:
        components.append({
            "type": "header",
            "parameters": [
                {
                    "type": "image",
                    "image": {"id": media_id},
                }
            ],
        })
    components.append({
        "type": "body",
        "parameters": [
            {"type": "text", "text": _texto_parametro(nombre_cliente, "cliente")},
            {"type": "text", "text": _texto_parametro(promocion, "promocion especial")},
            {"type": "text", "text": _texto_parametro(vigencia, "por tiempo limitado")},
            {"type": "text", "text": _texto_parametro(enlace, "responde a este mensaje")},
        ],
    })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono,
        "type": "template",
        "template": {
            "name": config.whatsapp_plantilla_marketing or "promo_general_imagen",
            "language": {"code": config.whatsapp_idioma_marketing or "es"},
            "components": components,
        },
    }
    return _post_whatsapp(config, payload)


def enviar_mensaje_whatsapp_texto(config, numero, mensaje):
    telefono = normalizar_telefono_hn(numero)
    if not telefono:
        raise WhatsAppAPIError("Falta el numero destino para WhatsApp.")
    if not mensaje:
        raise WhatsAppAPIError("Falta el mensaje para enviar por WhatsApp.")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": mensaje,
        },
    }
    return _post_whatsapp(config, payload)


def subir_media_whatsapp(config, archivo):
    if not archivo:
        raise WhatsAppAPIError("No se encontro imagen promocional para subir a WhatsApp.")
    try:
        file_path = Path(archivo.path)
    except (AttributeError, NotImplementedError, ValueError) as exc:
        raise WhatsAppAPIError("La imagen promocional no esta disponible como archivo local para subirla a WhatsApp.") from exc
    if not file_path.exists():
        raise WhatsAppAPIError("No se encontro el archivo fisico de la imagen promocional.")
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise WhatsAppAPIError("La media promocional debe ser una imagen valida.")
    upload_path, upload_content_type, temporal = _optimizar_imagen_para_whatsapp(file_path, content_type)
    try:
        response = _post_multipart(
            config,
            _media_endpoint(config),
            {"messaging_product": "whatsapp", "type": upload_content_type},
            "file",
            upload_path,
            upload_content_type,
        )
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)
    media_id = response.get("id")
    if not media_id:
        raise WhatsAppAPIError(f"Meta no devolvio id de media. Respuesta: {response}")
    return media_id


def enviar_imagen_whatsapp(config, numero, media_id, caption=""):
    telefono = normalizar_telefono_hn(numero)
    if not telefono:
        raise WhatsAppAPIError("Falta el numero destino para WhatsApp.")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono,
        "type": "image",
        "image": {
            "id": media_id,
            "caption": caption or "",
        },
    }
    return _post_whatsapp(config, payload)
