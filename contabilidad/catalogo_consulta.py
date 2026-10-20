"""Jerarquía del catálogo cargada una vez y limitada a la empresa consultada."""

from collections import defaultdict


def ordenar_catalogo(cuentas):
    cuentas = list(cuentas)
    por_id = {cuenta.pk: cuenta for cuenta in cuentas}
    hijos = defaultdict(list)
    for cuenta in cuentas:
        padre = por_id.get(cuenta.cuenta_padre_id)
        if padre and (padre.empresa_id != cuenta.empresa_id or padre.pk == cuenta.pk):
            padre = None
        cuenta.padre_catalogo = padre
        hijos[padre.pk if padre else None].append(cuenta)

    ordenadas, visitadas = [], set()

    def recorrer(raiz):
        pendientes = [(raiz, 0)]
        while pendientes:
            cuenta, nivel = pendientes.pop()
            if cuenta.pk in visitadas:
                continue
            visitadas.add(cuenta.pk)
            cuenta.nivel_catalogo = nivel
            cuenta.sangria_catalogo = nivel * 18
            cuenta.tiene_subcuentas_catalogo = bool(hijos.get(cuenta.pk))
            ordenadas.append(cuenta)
            pendientes.extend((hijo, nivel + 1) for hijo in reversed(hijos.get(cuenta.pk, [])))

    for cuenta in hijos[None]:
        recorrer(cuenta)
    # Un vínculo histórico circular no debe impedir consultar el resto del catálogo.
    for cuenta in cuentas:
        if cuenta.pk not in visitadas:
            recorrer(cuenta)
    return ordenadas
