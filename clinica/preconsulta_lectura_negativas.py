"""Lectura de declaraciones negativas explícitas, sin inferirlas de históricos."""

from copy import copy


def negativas_guardadas(preconsulta):
    datos = preconsulta.datos_generales
    if not isinstance(datos, dict):
        return {}
    negativas = datos.get("negativas_explicitas")
    if (not isinstance(negativas, dict) or type(negativas.get("version")) is not int
            or negativas["version"] != 1 or not isinstance(negativas.get("respuestas"), dict)):
        return {}
    return negativas["respuestas"]


def texto_negativa_guardada(negativas, nombre, valor):
    declaracion = negativas.get(nombre)
    if not isinstance(declaracion, dict):
        return None
    codigo, texto = declaracion.get("valor"), declaracion.get("texto")
    if not isinstance(codigo, str) or not codigo or not isinstance(texto, str) or not texto.strip():
        return None
    if isinstance(valor, (tuple, list)):
        if len(valor) != 1:
            return None
        valor = valor[0]
    if isinstance(valor, bool):
        coincide = valor is False and codigo == "no"
    else:
        coincide = isinstance(valor, str) and (
            valor.strip().casefold() == codigo.casefold() or valor == texto
        )
    return texto if coincide else None


def respuestas_para_lectura(preconsulta, general):
    # La app usa un dict que valida la estructura al leer cada campo.
    # Conservar su tipo evita eludir esa validación al adaptar las etiquetas.
    respuestas = copy(general)
    negativas = negativas_guardadas(preconsulta)
    for nombre, valor in general.items():
        texto = texto_negativa_guardada(negativas, nombre, valor)
        if texto:
            respuestas[nombre] = [texto] if isinstance(valor, (list, tuple)) else texto
    return respuestas


def valor_modelo_para_lectura(preconsulta, nombre, valor):
    texto = texto_negativa_guardada(negativas_guardadas(preconsulta), nombre, valor)
    if texto:
        return [texto] if isinstance(valor, (list, tuple)) else texto
    return valor
