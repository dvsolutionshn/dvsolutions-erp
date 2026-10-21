"""Bind access links to the current identity and credentials of their owner."""

import json

from django.utils.crypto import constant_time_compare, salted_hmac

from .models import TokenAccesoUsuario


def firma_estado_usuario(usuario):
    estado = json.dumps(
        [
            usuario.pk,
            (usuario.email or "").strip().lower(),
            usuario.password,
            usuario.is_active,
            usuario.retirado_control,
        ],
        separators=(",", ":"),
    )
    return salted_hmac("core.access_tokens.estado_usuario", estado, algorithm="sha256").hexdigest()


def token_acceso_valido(token, usuario):
    if not token or token.usuario_id != usuario.pk or not token.vigente or usuario.retirado_control:
        return False
    if not token.estado_usuario_hash or not constant_time_compare(
        token.estado_usuario_hash, firma_estado_usuario(usuario)
    ):
        return False
    if token.tipo == TokenAccesoUsuario.TIPO_RECUPERACION:
        return usuario.is_active
    if token.tipo == TokenAccesoUsuario.TIPO_INVITACION:
        return not usuario.is_active and not usuario.has_usable_password()
    return False
