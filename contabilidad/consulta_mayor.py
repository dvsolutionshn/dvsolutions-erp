"""Selección del Mayor sin modificar sus saldos ni movimientos calculados."""

import unicodedata

from .reportes import CERO, datos_mayor


def _texto_busqueda(valor):
    return "".join(
        letra for letra in unicodedata.normalize("NFKD", valor.casefold())
        if not unicodedata.combining(letra)
    )


def bloques_mayor_consulta(empresa, filtros, *, cuenta=None, excluir_cierres=False):
    bloques = datos_mayor(
        empresa,
        fecha_inicio=filtros.get("fecha_inicio", ""),
        fecha_fin=filtros.get("fecha_fin", ""),
        tipo=filtros.get("tipo", ""),
        cuenta=cuenta,
        excluir_cierres=excluir_cierres,
    )
    # Los enlaces al Mayor de una cuenta conservan la apertura y el contrato
    # anterior, incluso cuando esa cuenta no tiene movimientos en el período.
    if cuenta is not None:
        return bloques
    consulta = _texto_busqueda(filtros.get("q", "").strip())
    return [
        bloque for bloque in bloques
        if bloque["movimientos"] and (
            not consulta or consulta in _texto_busqueda(
                f"{bloque['cuenta'].codigo} {bloque['cuenta'].nombre}"
            )
        )
    ]


def resumen_mayor_general(bloques, *, vista_parcial=False):
    # Los saldos naturales de activos y pasivos tienen signos distintos. Se
    # reutiliza la orientación del Mayor General para separar deudor/acreedor.
    saldos_contables = [
        bloque["saldo"] if bloque["cuenta"].tipo in ("activo", "costo", "gasto")
        else -bloque["saldo"]
        for bloque in bloques
    ]
    return {
        "cuentas": len(bloques),
        "movimientos": sum(len(bloque["movimientos"]) for bloque in bloques),
        "debe_total": sum((bloque["debe"] for bloque in bloques), CERO),
        "haber_total": sum((bloque["haber"] for bloque in bloques), CERO),
        "saldo_deudor_total": sum((max(saldo, CERO) for saldo in saldos_contables), CERO),
        "saldo_acreedor_total": sum((max(-saldo, CERO) for saldo in saldos_contables), CERO),
        "vista_parcial": bool(vista_parcial),
    }
