"""Autoría clínica tomada exclusivamente de la sesión autenticada."""

from django.core.exceptions import PermissionDenied

from .models import ProfesionalSalud


def profesional_del_usuario(empresa, usuario):
    """El nombre del usuario nunca sustituye un vínculo explícito por empresa."""
    if not empresa or not getattr(usuario, "is_authenticated", False):
        return None
    return ProfesionalSalud.objects.filter(
        empresa=empresa, usuario=usuario, activo=True,
    ).order_by("pk").first()


def asignar_autoria_clinica(registro, empresa, usuario):
    if (not empresa or not getattr(usuario, "is_authenticated", False)
            or not usuario.puede_acceder_empresa(empresa)):
        raise PermissionDenied("Se requiere un usuario autorizado para guardar el formulario clínico.")
    if registro.empresa_id and registro.empresa_id != empresa.pk:
        raise PermissionDenied("El formulario clínico no pertenece a esta empresa.")
    registro.empresa = empresa
    registro.profesional = profesional_del_usuario(empresa, usuario)
    campos = {campo.name for campo in registro._meta.fields}
    creadores = campos.intersection({"creado_por", "creada_por"})
    if registro._state.adding:
        for campo in creadores:
            setattr(registro, campo, usuario)
    elif creadores:
        original = type(registro).objects.filter(pk=registro.pk, empresa=empresa).values(
            *(f"{campo}_id" for campo in creadores),
        ).get()
        for campo in creadores:
            setattr(registro, f"{campo}_id", original[f"{campo}_id"])
    if "actualizado_por" in campos:
        registro.actualizado_por = usuario
    return registro
