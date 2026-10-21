"""Comparaciones mensuales construidas con los reportes contables existentes."""

import calendar
from datetime import date
import re

from django.utils import timezone
from django.utils.dateparse import parse_date

from .reportes import CERO, _jerarquia_balance, datos_balance_general, datos_estado_resultados


MESES = (
    ("Ene", "Enero"), ("Feb", "Febrero"), ("Mar", "Marzo"),
    ("Abr", "Abril"), ("May", "Mayo"), ("Jun", "Junio"),
    ("Jul", "Julio"), ("Ago", "Agosto"), ("Sep", "Septiembre"),
    ("Oct", "Octubre"), ("Nov", "Noviembre"), ("Dic", "Diciembre"),
)
MAX_MESES_COMPARACION = 120


class ErrorComparacionPeriodos(ValueError):
    """El rango mensual solicitado no puede utilizarse en una comparación."""


def _mes_valido(valor):
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", valor):
        raise ErrorComparacionPeriodos("Selecciona un mes y año válidos para el inicio y el final de la comparación.")
    try:
        return date(int(valor[:4]), int(valor[5:]), 1)
    except ValueError as error:
        raise ErrorComparacionPeriodos("Selecciona un mes y año válidos para el inicio y el final de la comparación.") from error


def periodos_comparacion(mes_inicio, mes_fin):
    inicio, fin = _mes_valido(mes_inicio), _mes_valido(mes_fin)
    cantidad = (fin.year - inicio.year) * 12 + fin.month - inicio.month + 1
    if cantidad < 1:
        raise ErrorComparacionPeriodos("El mes inicial no puede ser posterior al mes final.")
    if cantidad > MAX_MESES_COMPARACION:
        raise ErrorComparacionPeriodos(f"Puedes comparar hasta {MAX_MESES_COMPARACION} meses por consulta.")
    periodos = []
    for desplazamiento in range(cantidad):
        indice = inicio.year * 12 + inicio.month - 1 + desplazamiento
        anio, mes_cero = divmod(indice, 12)
        mes = mes_cero + 1
        primero = date(anio, mes, 1)
        ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
        periodos.append({
            "etiqueta": MESES[mes - 1][0], "anio": anio,
            "nombre": f"{MESES[mes - 1][1]} {anio}",
            "inicio": primero.isoformat(), "fin": ultimo.isoformat(), "es_acumulado": False,
        })
    return periodos


def formulario_comparacion(parametros, filtros=None):
    """Propone meses sin cambiar los filtros ni activar la comparación."""
    filtros = filtros or {}

    def fecha_filtro(nombre):
        try:
            return parse_date(filtros.get(nombre) or "")
        except (ValueError, TypeError):
            return None

    fin = fecha_filtro("fecha_fin") or fecha_filtro("fecha_corte") or timezone.localdate()
    inicio = fecha_filtro("fecha_inicio") or date(fin.year, 1, 1)
    return {
        "mes_inicio": parametros.get("mes_inicio", f"{inicio.year:04d}-{inicio.month:02d}"),
        "mes_fin": parametros.get("mes_fin", f"{fin.year:04d}-{fin.month:02d}"),
    }


def _grupos_comparativos(empresa, reportes, grupos, tipos):
    # Se usa la misma jerarquía y su protección frente a ciclos históricos. La
    # unión sólo determina las filas y el orden; los importes vienen de cada
    # reporte individual y los totales, de sus resúmenes originales.
    directas = {}
    for reporte in reportes:
        for clave, _titulo, _tono in grupos:
            for fila in reporte[clave]:
                directas.setdefault(fila["cuenta"].pk, fila)
    jerarquia = _jerarquia_balance(empresa, list(directas.values()), tipos=tipos)
    resultados = []
    for (clave, titulo, tono), tipo in zip(grupos, tipos):
        mapas = [
            {fila["cuenta"].pk: fila["saldo"] for fila in reporte[f"{clave}_jerarquia"]}
            for reporte in reportes
        ]
        filas = []
        for fila in jerarquia:
            if fila["cuenta"].tipo != tipo:
                continue
            filas.append({
                "cuenta": fila["cuenta"], "nivel": fila["nivel"],
                "sangria_px": fila["sangria_px"], "es_agrupadora": fila["es_agrupadora"],
                "saldos": [mapa.get(fila["cuenta"].pk, CERO) for mapa in mapas],
            })
        resultados.append({
            "clave": clave, "titulo": titulo, "tono": tono, "filas": filas,
            "totales": [reporte["resumen"][f"total_{clave}"] for reporte in reportes],
        })
    return resultados


def _comparacion(mes_inicio, mes_fin, columnas, grupos, reportes, resumen_claves, filtros):
    return {
        "mes_inicio": mes_inicio, "mes_fin": mes_fin,
        "etiqueta_inicio": columnas[0]["nombre"],
        "etiqueta_fin": next(columna["nombre"] for columna in reversed(columnas) if not columna["es_acumulado"]),
        "columnas": columnas, "grupos": grupos,
        "resumen_filas": [
            {"clave": clave, "etiqueta": etiqueta, "saldos": [reporte["resumen"][clave] for reporte in reportes]}
            for clave, etiqueta in resumen_claves
        ],
        "resumenes": [reporte["resumen"] for reporte in reportes],
        "datos_actuales": reportes[-1], "filtros": filtros,
    }


def datos_comparacion_balance(empresa, mes_inicio, mes_fin):
    columnas = periodos_comparacion(mes_inicio, mes_fin)
    reportes = [datos_balance_general(empresa, fecha_corte=columna["fin"]) for columna in columnas]
    grupos = _grupos_comparativos(
        empresa, reportes,
        (("activos", "Activos", "teal"), ("pasivos", "Pasivos", "amber"), ("patrimonio", "Patrimonio", "indigo")),
        ("activo", "pasivo", "patrimonio"),
    )
    grupos[-1]["filas"].append({
        "cuenta": None, "nombre": "Resultado acumulado no traspasado a patrimonio",
        "nivel": 0, "sangria_px": 0, "es_agrupadora": False,
        "saldos": [reporte["resumen"]["resultado_no_traspasado"] for reporte in reportes],
    })
    return _comparacion(
        mes_inicio, mes_fin, columnas, grupos, reportes,
        (("total_activos", "Total activos"), ("total_pasivos", "Total pasivos"),
         ("total_patrimonio", "Total patrimonio"), ("total_pasivo_patrimonio", "Pasivo + patrimonio"),
         ("diferencia", "Diferencia")),
        {"fecha_corte": columnas[-1]["fin"]},
    )


def datos_comparacion_resultados(empresa, mes_inicio, mes_fin):
    columnas = periodos_comparacion(mes_inicio, mes_fin)
    reportes = [
        datos_estado_resultados(empresa, fecha_inicio=columna["inicio"], fecha_fin=columna["fin"])
        for columna in columnas
    ]
    filtros = {"fecha_inicio": columnas[0]["inicio"], "fecha_fin": columnas[-1]["fin"]}
    # El acumulado también procede del reporte actual del rango completo, sin
    # sumar filas agrupadoras ni introducir una fórmula contable paralela.
    reportes.append(datos_estado_resultados(empresa, **filtros))
    columnas.append({
        "etiqueta": "Acumulado", "nombre": "Acumulado del período", "anio": "",
        "inicio": filtros["fecha_inicio"], "fin": filtros["fecha_fin"], "es_acumulado": True,
    })
    grupos = _grupos_comparativos(
        empresa, reportes,
        (("ingresos", "Ingresos", "teal"), ("costos", "Costos", "amber"), ("gastos", "Gastos", "slate")),
        ("ingreso", "costo", "gasto"),
    )
    return _comparacion(
        mes_inicio, mes_fin, columnas, grupos, reportes,
        (("utilidad_bruta", "Utilidad bruta"), ("utilidad", "Resultado neto")), filtros,
    )
