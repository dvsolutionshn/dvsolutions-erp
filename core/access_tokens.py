import hashlib
from io import BytesIO
import secrets
import warnings
from email.mime.image import MIMEImage
from email.utils import make_msgid
from pathlib import Path

from PIL import Image, ImageOps

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import override

from .access_security import firma_estado_usuario, token_acceso_valido
from .models import TokenAccesoUsuario, Usuario


def hash_token_acceso(token):
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def emitir_token_acceso(usuario, tipo, creado_por=None, request=None, horas=24):
    ahora = timezone.now()
    token_raw = secrets.token_urlsafe(32)
    with transaction.atomic():
        usuario_actual = Usuario.objects.select_for_update().get(pk=usuario.pk)
        token = TokenAccesoUsuario.objects.create(
            usuario=usuario_actual,
            tipo=tipo,
            token_hash=hash_token_acceso(token_raw),
            token_preview=f"{token_raw[:8]}...{token_raw[-4:]}",
            estado_usuario_hash=firma_estado_usuario(usuario_actual),
            estado_envio=TokenAccesoUsuario.ENVIO_PENDIENTE,
            creado_por=creado_por,
            fecha_expiracion=ahora + timezone.timedelta(hours=horas),
            ip_solicitud=_client_ip(request) if request else "",
        )
    return token_raw, token


def enviar_correo_acceso(request, usuario, token_raw, token, tipo):
    ruta = reverse("establecer_acceso", args=[token_raw])
    base_url = getattr(settings, "ACCESS_PUBLIC_BASE_URL", "").rstrip("/")
    url = f"{base_url}{ruta}" if base_url else request.build_absolute_uri(ruta)
    intento = timezone.now()
    try:
        # Mismo orden de bloqueos que al consumir el enlace o editar la cuenta.
        # El enlace anterior se conserva hasta que SMTP acepte el nuevo mensaje.
        with transaction.atomic():
            usuario_actual = Usuario.objects.select_for_update().get(pk=usuario.pk)
            token_actual = TokenAccesoUsuario.objects.select_for_update().get(pk=token.pk)
            if (
                token_actual.usuario_id != usuario_actual.pk
                or token_actual.tipo != tipo
                or token_actual.token_hash != hash_token_acceso(token_raw)
                or not token_acceso_valido(token_actual, usuario_actual)
                or token_actual.fecha_envio is not None
            ):
                raise ValueError("El enlace ya no es valido para esta cuenta.")
            if settings.EMAIL_BACKEND in {
                "django.core.mail.backends.console.EmailBackend",
                "django.core.mail.backends.dummy.EmailBackend",
                "django.core.mail.backends.filebased.EmailBackend",
            }:
                raise ImproperlyConfigured("Configura SMTP para enviar invitaciones por correo.")
            _enviar_mensaje_acceso(usuario_actual, token_actual, tipo, url)
            ahora = timezone.now()
            token_actual.estado_envio = TokenAccesoUsuario.ENVIO_ENVIADO
            token_actual.fecha_intento_envio = intento
            token_actual.fecha_envio = ahora
            token_actual.save(update_fields=["estado_envio", "fecha_intento_envio", "fecha_envio"])
            TokenAccesoUsuario.objects.filter(
                usuario=usuario_actual, tipo=tipo, revocado=False, fecha_uso__isnull=True,
            ).exclude(pk=token_actual.pk).update(revocado=True, fecha_revocacion=ahora)
    except Exception:
        TokenAccesoUsuario.objects.filter(pk=token.pk, fecha_envio__isnull=True).update(
            estado_envio=TokenAccesoUsuario.ENVIO_ERROR,
            fecha_intento_envio=intento,
            revocado=True,
            fecha_revocacion=timezone.now(),
        )
        raise
    return url


def _enviar_mensaje_acceso(usuario, token, tipo, url):
    mensaje = _crear_mensaje_acceso(usuario, token, tipo, url)
    if mensaje.send(fail_silently=False) != 1:
        raise RuntimeError("El servidor de correo no acepto el mensaje.")


def _preparar_logo_empresa(empresa):
    """Imagen pequena para correo; un logo ausente o danado no impide invitar."""
    if not empresa or not empresa.logo:
        return None
    try:
        limite_bytes = 5 * 1024 * 1024
        with empresa.logo.open("rb") as archivo:
            contenido = archivo.read(limite_bytes + 1)
        if len(contenido) > limite_bytes:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(contenido)) as original:
                if original.width * original.height > 12_000_000:
                    return None
                imagen = ImageOps.exif_transpose(original).convert("RGBA")
                escala = min(180 / imagen.width, 64 / imagen.height, 1)
                ancho = max(1, round(imagen.width * escala))
                alto = max(1, round(imagen.height * escala))
                imagen.thumbnail((360, 128), Image.Resampling.LANCZOS)
                imagen.info.clear()
                salida = BytesIO()
                imagen.save(salida, format="PNG")
        return salida.getvalue(), ancho, alto
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def _crear_mensaje_acceso(usuario, token, tipo, url):
    es_invitacion = tipo == TokenAccesoUsuario.TIPO_INVITACION
    nombre_empresa = usuario.empresa.nombre if usuario.empresa else "Oasis"
    asunto = (
        f"Activa tu acceso a {nombre_empresa} | Oasis"
        if es_invitacion
        else f"Restablece tu acceso a {nombre_empresa} | Oasis"
    )
    expiracion_local = timezone.localtime(token.fecha_expiracion, timezone.get_default_timezone())
    with override("es"):
        fecha_expiracion_texto = date_format(expiracion_local, r"j \d\e F \d\e Y, H:i")
    desplazamiento = expiracion_local.strftime("%z")
    zona_horaria = f"UTC{desplazamiento[:3]}:{desplazamiento[3:]}"
    if settings.TIME_ZONE == "America/Tegucigalpa":
        zona_horaria = f"hora de Honduras · {zona_horaria}"
    logo_cid = make_msgid(idstring="oasis-logo", domain="dvsolutionshn.com")
    logo_empresa = _preparar_logo_empresa(usuario.empresa)
    logo_empresa_cid = make_msgid(idstring="empresa-logo", domain="dvsolutionshn.com") if logo_empresa else ""
    contexto = {
        "usuario": usuario,
        "empresa": usuario.empresa,
        "nombre_destinatario": usuario.get_full_name().strip() or usuario.email,
        "nombre_empresa": nombre_empresa,
        "url_acceso": url,
        "fecha_expiracion": token.fecha_expiracion,
        "fecha_expiracion_texto": fecha_expiracion_texto,
        "zona_horaria": zona_horaria,
        "es_invitacion": es_invitacion,
        "logo_cid": logo_cid[1:-1],
        "logo_empresa_cid": logo_empresa_cid[1:-1],
        "logo_empresa_ancho": logo_empresa[1] if logo_empresa else None,
        "logo_empresa_alto": logo_empresa[2] if logo_empresa else None,
    }
    texto = render_to_string("core/emails/acceso_usuario.txt", contexto)
    html = render_to_string("core/emails/acceso_usuario.html", contexto)
    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    mensaje.attach_alternative(html, "text/html")
    mensaje.mixed_subtype = "related"
    logo_path = Path(__file__).resolve().parent / "static" / "core" / "images" / "oasis-email-logo.png"
    logo = MIMEImage(logo_path.read_bytes(), _subtype="png")
    logo.add_header("Content-ID", logo_cid)
    logo.add_header("Content-Disposition", "inline", filename="oasis-logo.png")
    mensaje.attach(logo)
    if logo_empresa:
        imagen_empresa = MIMEImage(logo_empresa[0], _subtype="png")
        imagen_empresa.add_header("Content-ID", logo_empresa_cid)
        imagen_empresa.add_header("Content-Disposition", "inline", filename="empresa-logo.png")
        mensaje.attach(imagen_empresa)
    return mensaje


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
