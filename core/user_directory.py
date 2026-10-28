"""Datos de presentación del directorio; no concede ni modifica accesos."""

from .models import TokenAccesoUsuario
from .user_permissions import catalogo_permisos_empresa


_CODIGOS_INVITACION = {
    "Error de envio": "error",
    "Revocado": "revocado",
    "Utilizado": "utilizado",
    "Vencido": "vencido",
    "Sin enviar": "sin_enviar",
    "Envio sin confirmar": "sin_confirmar",
    "Pendiente": "pendiente",
}
_NOTAS_INVITACION = {
    "error": "No se envio esta invitacion. Revisa la configuración de correo y vuelve a intentarlo.",
    "revocado": "Este enlace de invitación ya no está disponible.",
    "utilizado": "El enlace de invitación ya fue utilizado.",
    "vencido": "El enlace de invitación venció.",
    "sin_enviar": "La invitación todavía no se ha enviado.",
    "sin_confirmar": "No se ha confirmado el envío del correo.",
    "pendiente": "Invitación pendiente de activar.",
}


def _estado_usuario(usuario):
    if usuario.retirado_control:
        return {"codigo": "retirado", "label": "Retirado", "tono": "neutral", "nota": "Su historial se conserva."}
    if usuario.is_active:
        return {"codigo": "activo", "label": "Activo", "tono": "success", "nota": "Cuenta activa."}

    invitaciones = getattr(usuario, "invitaciones_recientes", None)
    if invitaciones is None:
        invitacion = usuario.tokens_acceso.filter(tipo=TokenAccesoUsuario.TIPO_INVITACION).order_by("-fecha_creacion", "-id").first()
    else:
        invitacion = invitaciones[0] if invitaciones else None
    if invitacion:
        label = invitacion.estado_display
        codigo = _CODIGOS_INVITACION.get(label, "pendiente")
        tono = "danger" if invitacion.estado_envio == TokenAccesoUsuario.ENVIO_ERROR else (
            "warning" if invitacion.vigente else "neutral"
        )
        return {"codigo": codigo, "label": label, "tono": tono, "nota": _NOTAS_INVITACION[codigo]}
    return {"codigo": "inactivo", "label": "Inactivo", "tono": "danger", "nota": "Cuenta deshabilitada."}


def _empresas_usuario(usuario):
    empresas = {empresa.pk: empresa for empresa in usuario.empresas_acceso.all()}
    if usuario.empresa_id:
        empresas[usuario.empresa_id] = usuario.empresa
    asignaciones = getattr(usuario, "asignaciones_directorio", None)
    if asignaciones is None:
        asignaciones = usuario.permisos_por_empresa.select_related("rol_sistema", "empresa").all()
    por_empresa = {
        asignacion.empresa_id: asignacion
        for asignacion in asignaciones
        if asignacion.activo and asignacion.empresa_id in empresas
    }

    resultado = []
    for empresa in sorted(empresas.values(), key=lambda item: (item.pk != usuario.empresa_id, item.nombre.casefold(), item.pk)):
        asignacion = por_empresa.get(empresa.pk)
        rol = None
        permitidos = {campo for _, permisos in catalogo_permisos_empresa(empresa) for campo, _ in permisos}
        if asignacion and asignacion.permisos_personalizados is not None:
            modo = "personalizado"
            valores = asignacion.permisos_personalizados
            seleccionados = sum(valores.get(campo) is True for campo in permitidos) if isinstance(valores, dict) else 0
        else:
            rol = asignacion.rol_sistema if asignacion else usuario.rol_sistema
            modo = ("rol_empresa" if asignacion else "rol_global") if rol else "sin_rol"
            seleccionados = sum(bool(getattr(rol, campo, False)) for campo in permitidos) if rol else 0
        resultado.append({
            "empresa": empresa,
            "principal": empresa.pk == usuario.empresa_id,
            "modo": modo,
            "rol": rol,
            "rol_activo": bool(rol and rol.activo),
            "seleccionados": seleccionados,
        })
    return resultado


def construir_filas_usuarios(usuarios):
    filas = []
    for usuario in usuarios:
        nombre = usuario.get_full_name().strip() or usuario.username
        iniciales = "".join(parte.strip()[:1] for parte in (usuario.first_name, usuario.last_name) if parte.strip()).upper()
        empresas = _empresas_usuario(usuario)
        filas.append({
            "usuario": usuario,
            "nombre": nombre,
            "iniciales": iniciales or nombre[:2].upper(),
            "empresas": empresas,
            "empresa_ids": " ".join(str(empresa_id) for empresa_id in sorted(item["empresa"].pk for item in empresas)),
            "todas_empresas": bool(usuario.is_superuser),
            "estado": _estado_usuario(usuario),
            "tiene_personalizados": any(item["modo"] == "personalizado" for item in empresas),
            "tiene_roles_empresa": any(item["modo"] == "rol_empresa" for item in empresas),
            "puede_reenviar": bool(usuario.email and not usuario.is_active and not usuario.has_usable_password() and not usuario.retirado_control),
        })
    return filas
