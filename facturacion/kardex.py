"""Autores del Kardex a partir del movimiento o de su auditoria de creacion."""

from django.db.models import Q
from django.utils.dateparse import parse_datetime

from core.models import RegistroAuditoria


def anotar_usuarios_kardex(movimientos, empresa):
    pendientes = {}
    for movimiento in movimientos:
        movimiento.usuario_kardex = movimiento.usuario
        if movimiento.usuario_id is None and movimiento.empresa_id == empresa.pk:
            pendientes[(movimiento._meta.model_name, str(movimiento.pk))] = movimiento
    if not pendientes:
        return

    filtro = Q()
    for modelo in {modelo for modelo, _ in pendientes}:
        filtro |= Q(modelo=modelo, objeto_id__in=[pk for nombre, pk in pendientes if nombre == modelo])
    registros = RegistroAuditoria.objects.filter(
        filtro, empresa=empresa, app_label="facturacion", accion=RegistroAuditoria.ACCION_CREAR,
    ).select_related("usuario")
    autores = {}
    for registro in registros:
        clave = (registro.modelo, registro.objeto_id)
        movimiento = pendientes[clave]
        cambios = registro.cambios
        if not isinstance(cambios, dict):
            continue
        datos_empresa = cambios.get("empresa", {})
        datos_fecha = cambios.get("fecha_creacion", {})
        if not isinstance(datos_empresa, dict) or not isinstance(datos_fecha, dict):
            continue
        fecha = datos_fecha.get("nuevo")
        if datos_empresa.get("nuevo") != empresa.pk or not isinstance(fecha, str):
            continue
        try:
            fecha_creacion = parse_datetime(fecha)
        except ValueError:
            continue
        # La fecha de creacion distingue el registro incluso si se reutilizo un ID.
        if fecha_creacion != movimiento.fecha_creacion:
            continue
        if clave not in autores:
            autores[clave] = registro.usuario
        elif autores[clave] != registro.usuario:
            autores[clave] = None
    for clave, usuario in autores.items():
        pendientes[clave].usuario_kardex = usuario
