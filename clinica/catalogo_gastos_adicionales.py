"""Catálogo permitido exclusivamente en los documentos de Gastos Adicionales.

Se consultan los productos originales; compartir este selector no replica el
catálogo ni cambia el propietario de un producto.
"""
from django.db.models import Q

from core.access import EMPRESAS_CON_GASTOS_ADICIONALES
from facturacion.models import Producto


ORIGENES_CATALOGO_GASTOS = frozenset({"medical_spa", "hospital_mia"})


def producto_permitido_gasto(empresa, producto):
    """La excepción de origen es propia de las cuatro clínicas con GA."""
    return bool(
        empresa
        and producto
        and (
            producto.empresa_id == empresa.pk
            or (
                empresa.slug in EMPRESAS_CON_GASTOS_ADICIONALES
                and producto.empresa.slug in ORIGENES_CATALOGO_GASTOS
            )
        )
    )


def productos_gastos_adicionales(empresa, *, gasto=None, solo_disponibles=True):
    """Selector, validación POST y reconstrucción comparten la misma consulta.

    Al editar se conservan productos originales inactivos/eliminados, igual
    que al revisar sus líneas históricas, siempre dentro del catálogo permitido.
    """
    origenes = Q(empresa_id=empresa.pk)
    if empresa.slug in EMPRESAS_CON_GASTOS_ADICIONALES:
        origenes |= Q(empresa__slug__in=ORIGENES_CATALOGO_GASTOS)
    productos = Producto.objects.filter(origenes).select_related("empresa", "impuesto_predeterminado")
    if solo_disponibles:
        disponibles = Q(activo=True, eliminado=False)
        if gasto and gasto.empresa_id == empresa.pk:
            disponibles |= Q(pk__in=gasto.lineas.values_list("producto_id", flat=True))
        productos = productos.filter(disponibles)
    return productos
