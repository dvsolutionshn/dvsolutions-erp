"""Conversión de documentos a la moneda funcional, el lempira (HNL)."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError


CENTAVO = Decimal("0.01")
PRECISION_TASA = Decimal("0.000001")


def redondear_importe(monto):
    try:
        valor = Decimal(str(monto or 0))
        if not valor.is_finite():
            raise InvalidOperation
        return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("El importe debe ser un número válido.")


def validar_moneda_tipo_cambio(moneda, tipo_cambio):
    moneda = (moneda or "HNL").upper().strip()
    if moneda not in {"HNL", "USD"}:
        raise ValidationError({"moneda": "Selecciona lempiras o dólares estadounidenses."})
    if moneda == "HNL":
        return moneda, Decimal("1.000000")
    try:
        tasa = Decimal(str(tipo_cambio)).quantize(PRECISION_TASA, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"tipo_cambio": "Indica cuántos lempiras equivalen a un dólar."})
    if not tasa.is_finite() or tasa <= 0:
        raise ValidationError({"tipo_cambio": "El tipo de cambio debe ser mayor que cero."})
    return moneda, tasa


def convertir_a_lempiras(monto, moneda="HNL", tipo_cambio=1):
    _, tasa = validar_moneda_tipo_cambio(moneda, tipo_cambio)
    return redondear_importe(redondear_importe(monto) * tasa)


def distribuir_conversion(importes, moneda="HNL", tipo_cambio=1, total_lempiras=None):
    """Distribuye los centavos para que los componentes sumen el total convertido.

    El componente de mayor importe absorbe diferencias de redondeo. Nunca se
    convierte un cero en un movimiento ni se produce un importe negativo.
    """
    _, tasa = validar_moneda_tipo_cambio(moneda, tipo_cambio)
    originales = [redondear_importe(importe) for importe in importes]
    if any(importe < 0 for importe in originales):
        raise ValidationError("Los importes que se convierten no pueden ser negativos.")
    convertidos = [redondear_importe(importe * tasa) for importe in originales]
    objetivo = (
        redondear_importe(sum(originales, Decimal("0")) * tasa)
        if total_lempiras is None else redondear_importe(total_lempiras)
    )
    diferencia = objetivo - sum(convertidos, Decimal("0"))
    candidatos = sorted(range(len(originales)), key=lambda i: (-originales[i], i))
    for indice in candidatos:
        if originales[indice] <= 0:
            continue
        ajuste = max(diferencia, -convertidos[indice])
        convertidos[indice] += ajuste
        diferencia -= ajuste
        if not diferencia:
            break
    if diferencia:
        raise ValidationError("No se pudo distribuir el redondeo de la conversión monetaria.")
    return convertidos


def convertir_partidas_balanceadas(lineas, moneda="HNL", tipo_cambio=1, *, total_lempiras=None):
    """Convierte un grupo balanceado en una única moneda; conserva su respaldo."""
    moneda, tasa = validar_moneda_tipo_cambio(moneda, tipo_cambio)
    originales = [
        {**linea, "debe": redondear_importe(linea.get("debe", 0)),
         "haber": redondear_importe(linea.get("haber", 0))}
        for linea in lineas
    ]
    total_debe = sum((linea["debe"] for linea in originales), Decimal("0"))
    total_haber = sum((linea["haber"] for linea in originales), Decimal("0"))
    if total_debe != total_haber:
        raise ValidationError("El documento de origen no está balanceado antes de convertirlo a lempiras.")
    debe = distribuir_conversion([linea["debe"] for linea in originales], moneda, tasa, total_lempiras)
    haber = distribuir_conversion([linea["haber"] for linea in originales], moneda, tasa, total_lempiras)
    return [
        {**linea, "debe": debe[indice], "haber": haber[indice],
         "moneda_origen": moneda, "tipo_cambio": tasa,
         "debe_origen": linea["debe"], "haber_origen": linea["haber"]}
        for indice, linea in enumerate(originales)
    ]


def importe_historico_parcial(*, total_origen, total_lempiras, aplicado_origen,
                              aplicado_lempiras, importe_origen):
    """Cancela la parte histórica y deja el último centavo para la última cuota."""
    total_origen = redondear_importe(total_origen)
    total_lempiras = redondear_importe(total_lempiras)
    aplicado_origen = redondear_importe(aplicado_origen)
    aplicado_lempiras = redondear_importe(aplicado_lempiras)
    importe_origen = redondear_importe(importe_origen)
    if total_origen <= 0 or importe_origen <= 0:
        raise ValidationError("El documento y el importe aplicado deben ser mayores que cero.")
    if aplicado_origen < 0 or aplicado_origen + importe_origen > total_origen:
        raise ValidationError("La aplicación supera el saldo original del documento.")
    acumulado = redondear_importe(
        total_lempiras * (aplicado_origen + importe_origen) / total_origen
    )
    importe = acumulado - aplicado_lempiras
    saldo = total_lempiras - aplicado_lempiras
    if importe < 0 or importe > saldo:
        raise ValidationError("El saldo histórico requiere revisión antes de registrar la aplicación.")
    return redondear_importe(importe)
