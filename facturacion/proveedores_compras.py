"""Identificación común de proveedores de Nordic, sin fusiones aproximadas."""
import re
import unicodedata
from django.core.exceptions import ValidationError


def nombre_normalizado(nombre):
    texto = unicodedata.normalize('NFKD', str(nombre or '')).casefold()
    return ''.join(c for c in texto if c.isalnum() and not unicodedata.combining(c))


def rtn_normalizado(rtn):
    texto = re.sub(r'[\s-]+', '', str(rtn or ''))
    if texto and not re.fullmatch(r'[0-9]{1,20}', texto):
        raise ValidationError('RTN inválido: usa solo dígitos, espacios o guiones.')
    return texto


def es_nordic(cliente):
    return bool(cliente and nombre_normalizado(cliente.nombre) == 'nordic'
                and cliente.empresa.slug == 'dubon_asociados')


def buscar_proveedor(proveedores, nombre, rtn):
    """RTN primero; el nombre solo resuelve ausencias, nunca RTN contradictorios."""
    clave, identidad = nombre_normalizado(nombre), rtn_normalizado(rtn)
    if not clave:
        raise ValidationError('Completa el nombre del proveedor.')
    def rtn_seguro(p):
        try:
            return rtn_normalizado(p.rtn)
        except ValidationError:
            return 'invalido'
    exactos = [p for p in proveedores if identidad and rtn_seguro(p) == identidad]
    por_nombre = [p for p in proveedores if nombre_normalizado(p.nombre) == clave]
    candidatos = exactos or [p for p in por_nombre if not identidad or not rtn_seguro(p)]
    if len(candidatos) > 1:
        raise ValidationError('Hay varios proveedores coincidentes. Revisa sus fichas o completa el RTN; no se fusionarán automáticamente.')
    if candidatos:
        if not candidatos[0].activo:
            raise ValidationError('El proveedor coincidente está inactivo. Revisa su ficha.')
        return candidatos[0]
    return None


def resolver_proveedor(empresa, cliente, nombre, rtn, usuario, proveedores=None):
    """Llamar dentro de una transacción con bloqueo de empresa."""
    from .models import Proveedor
    if proveedores is None:
        proveedores = list(Proveedor.objects.filter(empresa=empresa, cliente_contable=cliente))
    proveedor = buscar_proveedor(proveedores, nombre, rtn)
    if proveedor:
        if rtn_normalizado(rtn) and not proveedor.rtn:
            if not usuario.tiene_permiso_erp('puede_editar_proveedores', empresa):
                raise ValidationError('Completa el RTN del proveedor existente con un usuario autorizado antes de vincularlo.')
            proveedor.rtn = rtn_normalizado(rtn)
            proveedor.save(update_fields=['rtn'])
        return proveedor, False
    if not usuario.tiene_permiso_erp('puede_crear_proveedores', empresa):
        raise ValidationError('No tienes permiso para crear los proveedores faltantes.')
    proveedor = Proveedor.objects.create(empresa=empresa, cliente_contable=cliente,
        nombre=' '.join(nombre.split()), rtn=rtn_normalizado(rtn))
    proveedores.append(proveedor)
    return proveedor, True


def cuenta_sugerida(proveedor):
    cuenta = proveedor.cuenta_habitual if proveedor and proveedor.activo else None
    if cuenta and cuenta.activa and cuenta.cliente_contable_id == proveedor.cliente_contable_id:
        return cuenta
    return None
