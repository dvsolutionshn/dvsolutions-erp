"""Resuelve las líneas solicitadas antes de guardar una factura emitida."""

from copy import copy
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .inventario_edicion import planificar_diferencias_inventario


CAMPOS_EDITABLES = (
    "producto_id", "descripcion_manual", "cantidad", "precio_unitario",
    "descuento_porcentaje", "comentario", "impuesto_id",
)


@dataclass
class EdicionLineasFactura:
    guardar: list
    eliminar: list
    inventario: dict


def _mismo_producto(original, solicitada):
    if original.producto_id != solicitada.producto_id:
        return False
    return bool(original.producto_id) or (
        (original.descripcion_manual or "").strip()
        == (solicitada.descripcion_manual or "").strip()
    )


def _preferencia(original, solicitada):
    # El impuesto puede corregirse sin convertir la línea en una venta nueva.
    return (
        original.cantidad != solicitada.cantidad,
        original.precio_unitario != solicitada.precio_unitario,
        original.descuento_porcentaje != solicitada.descuento_porcentaje,
        original.impuesto_id != solicitada.impuesto_id,
        original.pk,
    )


def _copiar_edicion(original, solicitada):
    linea = copy(original)
    linea._state = copy(original._state)
    linea._state.fields_cache = original._state.fields_cache.copy()
    for campo in CAMPOS_EDITABLES:
        setattr(linea, campo, getattr(solicitada, campo))
    return linea


def preparar_lineas_factura_emitida(factura, originales, formset):
    """Reconoce líneas recreadas y reenvíos sin anexarlos a la venta anterior.

    INITIAL_FORMS=0 sin IDs representa una colección recreada completa. Cuando
    sí hay IDs se conservan las líneas omitidas, como en el formset habitual;
    una fila sin ID puede corresponder a una adición ya guardada por un reenvío.
    Cada línea original sólo puede utilizarse una vez.
    """
    por_id = {linea.pk: linea for linea in originales}
    formularios = [form for form in formset.forms if form.cleaned_data]
    reservados = set()
    identificadores = []
    for form in formularios:
        # Django omite los errores de las filas marcadas DELETE. Por eso se
        # comprueba también el ID recibido, aunque haya salido de cleaned_data.
        recibido = (form.data.get(form.add_prefix("id")) or "").strip()
        identificador = form.cleaned_data.get("id")
        try:
            pk = int(recibido) if recibido else (identificador.pk if identificador is not None else None)
        except (TypeError, ValueError):
            raise ValidationError("Una de las lineas enviadas no pertenece a esta factura.")
        identificadores.append(pk)
        if pk is None:
            continue
        if pk not in por_id:
            raise ValidationError("Una de las lineas enviadas no pertenece a esta factura.")
        if pk in reservados:
            raise ValidationError("La misma linea de factura fue enviada mas de una vez.")
        reservados.add(pk)

    recreadas = formset.initial_form_count() == 0 and not reservados
    disponibles = {pk: linea for pk, linea in por_id.items() if pk not in reservados}
    correspondencias = {}
    # Resolver primero todas las coincidencias por producto y cantidad. Una
    # fila nueva al principio del formulario no debe apropiarse de la identidad
    # de un producto original que aparece más adelante.
    for cantidad_exacta in (True, False):
        for indice, form in enumerate(formularios):
            if indice in correspondencias or identificadores[indice] is not None:
                continue
            solicitada = form.instance
            candidatas = [
                linea for linea in disponibles.values()
                if _mismo_producto(linea, solicitada)
                and (not cantidad_exacta or linea.cantidad == solicitada.cantidad)
            ]
            if candidatas:
                original = min(candidatas, key=lambda linea: _preferencia(linea, solicitada))
                correspondencias[indice] = original
                disponibles.pop(original.pk)
    if recreadas:
        for indice, form in enumerate(formularios):
            if indice in correspondencias or not disponibles:
                continue
            original = min(disponibles.values(), key=lambda linea: _preferencia(linea, form.instance))
            correspondencias[indice] = original
            disponibles.pop(original.pk)

    utilizadas = set()
    guardar, eliminar = [], []
    for indice, form in enumerate(formularios):
        identificador = identificadores[indice]
        original = por_id[identificador] if identificador is not None else correspondencias.get(indice)
        solicitada = form.instance
        if original is not None:
            utilizadas.add(original.pk)
        if form.cleaned_data.get("DELETE"):
            if original is not None:
                eliminar.append(original)
            continue
        linea = _copiar_edicion(original, solicitada) if original is not None else solicitada
        linea.factura = factura
        guardar.append(linea)

    omitidas = [linea for linea in originales if linea.pk not in utilizadas]
    if recreadas:
        eliminar.extend(omitidas)
        actuales = guardar
    else:
        actuales = guardar + omitidas

    # Comparar antes de guardar, reemplazar o eliminar cualquier línea.
    inventario = planificar_diferencias_inventario(originales, actuales)
    return EdicionLineasFactura(guardar=guardar, eliminar=eliminar, inventario=inventario)
