import hashlib
import secrets

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import TokenAccesoUsuario


def hash_token_acceso(token):
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def emitir_token_acceso(usuario, tipo, creado_por=None, request=None, horas=24):
    ahora = timezone.now()
    TokenAccesoUsuario.objects.filter(
        usuario=usuario,
        tipo=tipo,
        revocado=False,
        fecha_uso__isnull=True,
    ).update(revocado=True, fecha_revocacion=ahora)

    token_raw = secrets.token_urlsafe(32)
    token = TokenAccesoUsuario.objects.create(
        usuario=usuario,
        tipo=tipo,
        token_hash=hash_token_acceso(token_raw),
        token_preview=f"{token_raw[:8]}...{token_raw[-4:]}",
        creado_por=creado_por,
        fecha_expiracion=ahora + timezone.timedelta(hours=horas),
        ip_solicitud=_client_ip(request) if request else "",
    )
    return token_raw, token


def enviar_correo_acceso(request, usuario, token_raw, token, tipo):
    url = request.build_absolute_uri(
        reverse("establecer_acceso", args=[token_raw])
    )
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
    mensaje.send(fail_silently=False)
    return url


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
