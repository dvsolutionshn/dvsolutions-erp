"""Presentación móvil de la auditoría existente, sin modificar sus registros."""

from datetime import datetime


FIELD_LABELS = {
    "cliente": "Cliente", "fecha_emision": "Fecha de factura",
    "fecha_vencimiento": "Vencimiento", "numero_factura": "Número fiscal",
    "numero_anterior": "Número anterior", "numero_nuevo": "Número nuevo",
    "moneda": "Moneda", "tipo_cambio": "Tipo de cambio", "estado": "Estado",
    "estado_pago": "Estado de pago", "subtotal": "Subtotal", "impuesto": "Impuesto",
    "total": "Total", "total_lempiras": "Total en lempiras", "vendedor": "Atendido por",
    "producto": "Producto", "descripcion_manual": "Descripción", "cantidad": "Cantidad",
    "precio_unitario": "Precio unitario", "descuento_porcentaje": "Descuento (%)",
    "precio_incluye_impuesto": "Precio incluye impuesto", "comentario": "Nota",
    "costo_unitario": "Costo unitario", "motivo": "Motivo", "monto": "Monto",
    "orden_compra_exenta": "Orden de compra exenta", "registro_exonerado": "Registro exonerado",
    "registro_sag": "Registro SAG", "cai_numero": "CAI", "cai": "CAI asignado",
}
ACTION_LABELS = {"editar": "Factura editada", "cambiar_fecha": "Fecha corregida", "anular": "Factura anulada"}
MODEL_LABELS = {"factura": "factura", "lineafactura": "ítem", "pagofactura": "pago", "correccionnumerofactura": "corrección fiscal"}
VALUE_LABELS = {
    "borrador": "Borrador", "emitida": "Emitida", "anulada": "ANULADA",
    "pendiente": "Pendiente", "parcial": "Pago parcial", "pagado": "Pagado",
    "HNL": "Lempiras", "USD": "Dólares",
}
SKIPPED_FIELDS = {"factura", "accion_factura", "empresa", "id"}


def _display_value(campo, valor):
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, dict):
        return f"{len(valor)} datos registrados"
    if isinstance(valor, list):
        return f"{len(valor)} elementos"
    texto = str(valor)
    if campo.startswith("fecha") or campo.endswith("fecha_limite"):
        try:
            return datetime.fromisoformat(texto).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return VALUE_LABELS.get(texto, texto)


def _line_changes(anterior, nuevo):
    anteriores = {linea.get("id", index): linea for index, linea in enumerate(anterior or []) if isinstance(linea, dict)}
    nuevos = {linea.get("id", index): linea for index, linea in enumerate(nuevo or []) if isinstance(linea, dict)}
    campos = ("producto", "descripcion_manual", "cantidad", "precio_unitario", "descuento_porcentaje", "impuesto", "comentario")
    for index, linea_id in enumerate(dict.fromkeys([*anteriores, *nuevos]), start=1):
        antes, despues = anteriores.get(linea_id, {}), nuevos.get(linea_id, {})
        nombre = (despues or antes).get("descripcion_manual") or f"Ítem {index}"
        for campo in campos:
            if antes.get(campo) != despues.get(campo):
                etiqueta = "Tipo de impuesto" if campo == "impuesto" else FIELD_LABELS[campo]
                yield {
                    "campo": f"{nombre} · {etiqueta}",
                    "anterior": _display_value(campo, antes.get(campo)),
                    "nuevo": _display_value(campo, despues.get(campo)),
                }


def _change_rows(cambios, modelo="factura"):
    for campo, cambio in cambios.items():
        if campo in SKIPPED_FIELDS or not isinstance(cambio, dict):
            continue
        anterior, nuevo = cambio.get("anterior"), cambio.get("nuevo")
        if campo == "lineas" and (isinstance(anterior, list) or isinstance(nuevo, list)):
            yield from _line_changes(anterior, nuevo)
        elif campo == "registro_eliminado" and isinstance(anterior, dict):
            yield from _change_rows({nombre: {"anterior": valor, "nuevo": None} for nombre, valor in anterior.items()}, modelo)
        else:
            yield {
                "campo": "Tipo de impuesto" if campo == "impuesto" and modelo == "lineafactura" else FIELD_LABELS.get(campo, campo.replace("_", " ").capitalize()),
                "anterior": _display_value(campo, anterior),
                "nuevo": _display_value(campo, nuevo),
            }


def preparar_historial_factura_mobile(eventos):
    historial = []
    for evento in eventos:
        cambios = evento.cambios or {}
        marcador_accion = cambios.get("accion_factura") or {}
        accion = marcador_accion.get("nuevo") if isinstance(marcador_accion, dict) else None
        usuario = evento.usuario
        historial.append({
            "accion": ACTION_LABELS.get(accion, f"{evento.get_accion_display()} de {MODEL_LABELS.get(evento.modelo, evento.modelo.replace('_', ' '))}"),
            "usuario": (usuario.get_full_name() or usuario.username) if usuario else "Sistema",
            "fecha": evento.fecha,
            "motivo": evento.motivo,
            "cambios": list(_change_rows(cambios, evento.modelo)),
        })
    return historial
