"""Validaciones compartidas para el registro de lotes de inventario."""

import calendar
import re
from datetime import date, datetime

from django.core.exceptions import ValidationError


LOT_DUPLICATE_MESSAGE = "Este número de lote ya está registrado para este producto"
LOT_EXPIRY_REQUIRED_MESSAGE = "Ingresa la fecha de vencimiento del lote."
LOT_QUICK_EXPIRY_INVALID_MESSAGE = (
    "Ingresa un vencimiento rápido válido, por ejemplo ago-27 o junio/15/2031."
)

_MESES = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agos": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "set": 9, "setiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}


def _anio_vencimiento(valor):
    if not re.fullmatch(r"[0-9]{1,4}", valor):
        raise ValidationError("El año del vencimiento rápido no es válido.")
    anio = int(valor)
    if len(valor) == 2:
        anio += 2000
    if not 1 <= anio <= 9999:
        raise ValidationError("El año del vencimiento rápido debe estar entre 1 y 9999.")
    return anio


def parse_vencimiento_lote_rapido(valor):
    """Convierte mes-año (último día) o una fecha completa; vacío devuelve None.

    Los años de dos dígitos corresponden a 2000-2099. Una entrada inválida
    siempre genera ValidationError, incluso fechas imposibles o años enormes.
    """
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise ValidationError(LOT_QUICK_EXPIRY_INVALID_MESSAGE)
    valor = valor.strip().lower()
    if not valor:
        return None
    if len(valor) > 80:
        raise ValidationError(LOT_QUICK_EXPIRY_INVALID_MESSAGE)

    try:
        # También admite las mismas fechas completas del campo principal.
        for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(valor, formato).date()
            except ValueError:
                continue

        partes = re.split(r"\s*[-/]\s*|\s+", valor.replace(".", ""))
        if len(partes) not in (2, 3) or any(not parte for parte in partes):
            raise ValidationError(LOT_QUICK_EXPIRY_INVALID_MESSAGE)

        mes = _MESES.get(partes[0])
        if not mes:
            raise ValidationError("El mes del vencimiento rápido no es válido.")
        anio = _anio_vencimiento(partes[-1])
        if len(partes) == 2:
            dia = calendar.monthrange(anio, mes)[1]
        else:
            if not re.fullmatch(r"[0-9]{1,2}", partes[1]):
                raise ValidationError("El día del vencimiento rápido no es válido.")
            dia = int(partes[1])
        return date(anio, mes, dia)
    except (ValueError, OverflowError) as exc:
        raise ValidationError("La fecha del vencimiento rápido no existe. Revisa el día, mes y año.") from exc
