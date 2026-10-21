import hashlib
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

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
    es_invitacion = tipo == TokenAccesoUsuario.TIPO_INVITACION
    asunto = (
        f"Activa tu acceso a {usuario.empresa.nombre if usuario.empresa else 'DV Solutions ERP'}"
        if es_invitacion
        else "Restablece tu acceso a DV Solutions ERP"
    )
    contexto = {
        "usuario": usuario,
        "empresa": usuario.empresa,
        "url_acceso": url,
        "fecha_expiracion": token.fecha_expiracion,
        "es_invitacion": es_invitacion,
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
    if mensaje.send(fail_silently=False) != 1:
        raise RuntimeError("El servidor de correo no acepto el mensaje.")


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
