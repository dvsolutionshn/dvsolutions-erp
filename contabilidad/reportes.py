"""Cálculos compartidos por los reportes en pantalla y sus exportaciones.

Los asientos guardan importes en la moneda funcional de la empresa (HNL).
La moneda del documento se conserva en el asiento; aquí no se convierte otra vez.
"""

from collections import defaultdict
from decimal import Decimal
import unicodedata

from django.db.models import Q, Sum

from .alcance_catalogo import es_empresa_rumi
from .models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable


CERO = Decimal("0.00")
EVENTOS_CIERRE_RESULTADOS = ("cierre_anual", "cierre_ejercicio", "traspaso_resultados")


def saldo_natural(cuenta, debe, haber):
    debe, haber = debe or CERO, haber or CERO
    return debe - haber if cuenta.tipo in ("activo", "costo", "gasto") else haber - debe


def partidas_empresa(empresa):
    return LineaAsientoContable.objects.filter(
        asiento__empresa=empresa, asiento__estado="contabilizado", cuenta__empresa=empresa,
    )


def _sumas_por_cuenta(partidas):
    return {
        fila["cuenta_id"]: (fila["debe"] or CERO, fila["haber"] or CERO)
        for fila in partidas.values("cuenta_id").annotate(debe=Sum("debe"), haber=Sum("haber"))
    }


def saldos_financieros(empresa, tipos, fecha_inicio="", fecha_fin="", fecha_corte="", excluir_cierres=False):
    partidas = partidas_empresa(empresa).filter(cuenta__tipo__in=tipos)
    if fecha_inicio:
        partidas = partidas.filter(asiento__fecha__gte=fecha_inicio)
    if fecha_fin:
        partidas = partidas.filter(asiento__fecha__lte=fecha_fin)
    if fecha_corte:
        partidas = partidas.filter(asiento__fecha__lte=fecha_corte)
    if excluir_cierres:
        partidas = partidas.exclude(asiento__evento__in=EVENTOS_CIERRE_RESULTADOS)
    sumas = _sumas_por_cuenta(partidas)
    filas = []
    for cuenta in CuentaContable.objects.filter(empresa=empresa, tipo__in=tipos).order_by("tipo", "codigo", "nombre"):
        debe, haber = sumas.get(cuenta.pk, (CERO, CERO))
        if debe or haber:
            filas.append({"cuenta": cuenta, "debe": debe, "haber": haber, "saldo": saldo_natural(cuenta, debe, haber)})
    return filas


def datos_estado_resultados(empresa, fecha_inicio="", fecha_fin="", excluir_cierres=True):
    filas = saldos_financieros(empresa, ("ingreso", "costo", "gasto"), fecha_inicio, fecha_fin, excluir_cierres=excluir_cierres)
    ingresos = [fila for fila in filas if fila["cuenta"].tipo == "ingreso"]
    costos = [fila for fila in filas if fila["cuenta"].tipo == "costo"]
    gastos = [fila for fila in filas if fila["cuenta"].tipo == "gasto"]
    total_ingresos = sum((fila["saldo"] for fila in ingresos), CERO)
    total_costos = sum((fila["saldo"] for fila in costos), CERO)
    total_gastos = sum((fila["saldo"] for fila in gastos), CERO)
    utilidad = total_ingresos - total_costos - total_gastos
    jerarquia = _jerarquia_balance(empresa, filas, tipos=("ingreso", "costo", "gasto"))
    return {
        "ingresos": ingresos, "costos": costos, "gastos": gastos,
        # Las filas acumuladas son sólo para presentar el reporte. Los totales
        # y los consumidores existentes conservan las partidas directas.
        "ingresos_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "ingreso"],
        "costos_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "costo"],
        "gastos_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "gasto"],
        "resumen": {
            "total_ingresos": total_ingresos, "total_costos": total_costos,
            "total_gastos": total_gastos, "utilidad_bruta": total_ingresos - total_costos,
            "utilidad": utilidad, "resultado_positivo": utilidad >= 0,
            "cuentas_ingreso": len(ingresos), "cuentas_costo": len(costos), "cuentas_gasto": len(gastos),
        },
    }


def _jerarquia_balance(empresa, filas_directas, tipos=("activo", "pasivo", "patrimonio")):
    """Acumula la jerarquía visible sin volver a sumar sus filas en los totales.

    La relación padre es la fuente de la jerarquía, nunca el prefijo del código.
    Las relaciones históricas incompatibles se separan sólo para presentar el
    reporte: no cambian el catálogo ni trasladan saldos entre empresas o tipos.
    """
    cuentas = list(CuentaContable.objects.filter(
        empresa=empresa, tipo__in=tipos,
    ).order_by("codigo", "nombre", "pk"))
    por_id = {cuenta.pk: cuenta for cuenta in cuentas}
    directas = {fila["cuenta"].pk: fila for fila in filas_directas}
    padres = {}
    for cuenta in cuentas:
        padre = por_id.get(cuenta.cuenta_padre_id)
        padres[cuenta.pk] = padre.pk if padre and padre.tipo == cuenta.tipo else None

    # Un catálogo antiguo puede contener ciclos creados fuera del formulario.
    # Cortamos una sola arista por ciclo en memoria, con un orden estable.
    revisadas = set()
    for cuenta in cuentas:
        camino, posiciones = [], {}
        actual = cuenta.pk
        while actual is not None and actual not in revisadas:
            if actual in posiciones:
                padres[camino[posiciones[actual]]] = None
                break
            posiciones[actual] = len(camino)
            camino.append(actual)
            actual = padres[actual]
        revisadas.update(camino)

    hijas = defaultdict(list)
    for cuenta in cuentas:
        hijas[padres[cuenta.pk]].append(cuenta.pk)
    orden = []
    pendientes = [(pk, 0) for pk in reversed(hijas[None])]
    while pendientes:
        pk, nivel = pendientes.pop()
        orden.append((pk, nivel))
        pendientes.extend((hija, nivel + 1) for hija in reversed(hijas[pk]))

    acumuladas = {}
    for pk, nivel in reversed(orden):
        directa = directas.get(pk)
        debe_directo, haber_directo = (
            (directa["debe"], directa["haber"]) if directa else (CERO, CERO)
        )
        descendientes = [acumuladas[hija] for hija in hijas[pk] if hija in acumuladas]
        if directa is None and not descendientes:
            continue
        debe = debe_directo + sum((fila["debe"] for fila in descendientes), CERO)
        haber = haber_directo + sum((fila["haber"] for fila in descendientes), CERO)
        cuenta = por_id[pk]
        acumuladas[pk] = {
            "cuenta": cuenta, "debe": debe, "haber": haber,
            "saldo": saldo_natural(cuenta, debe, haber),
            "debe_directo": debe_directo, "haber_directo": haber_directo,
            "saldo_directo": saldo_natural(cuenta, debe_directo, haber_directo),
            "nivel": nivel, "sangria_px": nivel * 18,
            "es_agrupadora": bool(descendientes),
        }
    return [acumuladas[pk] for pk, _nivel in orden if pk in acumuladas]


def datos_balance_general(empresa, fecha_corte=""):
    filas = saldos_financieros(empresa, ("activo", "pasivo", "patrimonio"), fecha_corte=fecha_corte)
    activos = [fila for fila in filas if fila["cuenta"].tipo == "activo"]
    pasivos = [fila for fila in filas if fila["cuenta"].tipo == "pasivo"]
    patrimonio = [fila for fila in filas if fila["cuenta"].tipo == "patrimonio"]
    jerarquia = _jerarquia_balance(empresa, filas)
    # Incluye los cierres efectivos: después del traspaso, las cuentas de resultados
    # quedan sin saldo y el importe ya está registrado en patrimonio.
    resultado_pendiente = datos_estado_resultados(empresa, fecha_fin=fecha_corte, excluir_cierres=False)["resumen"]["utilidad"]
    total_activos = sum((fila["saldo"] for fila in activos), CERO)
    total_pasivos = sum((fila["saldo"] for fila in pasivos), CERO)
    patrimonio_contabilizado = sum((fila["saldo"] for fila in patrimonio), CERO)
    total_patrimonio = patrimonio_contabilizado + resultado_pendiente
    diferencia = total_activos - total_pasivos - total_patrimonio
    return {
        "activos": activos, "pasivos": pasivos, "patrimonio": patrimonio,
        "activos_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "activo"],
        "pasivos_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "pasivo"],
        "patrimonio_jerarquia": [fila for fila in jerarquia if fila["cuenta"].tipo == "patrimonio"],
        "resumen": {
            "total_activos": total_activos, "total_pasivos": total_pasivos,
            "patrimonio_contabilizado": patrimonio_contabilizado,
            "resultado_no_traspasado": resultado_pendiente,
            "total_patrimonio": total_patrimonio, "total_pasivo_patrimonio": total_pasivos + total_patrimonio,
            "diferencia": diferencia, "cuadrado": diferencia == 0,
        },
    }


def datos_mayor(empresa, fecha_inicio="", fecha_fin="", tipo="", cuenta=None):
    partidas = partidas_empresa(empresa)
    anteriores = _sumas_por_cuenta(partidas.filter(asiento__fecha__lt=fecha_inicio)) if fecha_inicio else {}
    periodo = partidas
    if fecha_inicio:
        periodo = periodo.filter(asiento__fecha__gte=fecha_inicio)
    if fecha_fin:
        periodo = periodo.filter(asiento__fecha__lte=fecha_fin)
    cuentas = CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "nombre")
    if tipo:
        cuentas = cuentas.filter(tipo=tipo)
    if cuenta:
        cuentas = cuentas.filter(pk=cuenta.pk)
        periodo = periodo.filter(cuenta=cuenta)
    movimientos_por_cuenta = defaultdict(list)
    for linea in periodo.select_related("asiento", "cuenta").order_by("asiento__fecha", "asiento__numero", "asiento_id", "id"):
        movimientos_por_cuenta[linea.cuenta_id].append(linea)
    bloques = []
    for cuenta_actual in cuentas:
        apertura_debe, apertura_haber = anteriores.get(cuenta_actual.pk, (CERO, CERO))
        saldo_inicial = saldo_natural(cuenta_actual, apertura_debe, apertura_haber)
        lineas = movimientos_por_cuenta.get(cuenta_actual.pk, [])
        if not lineas and not saldo_inicial and cuenta is None:
            continue
        saldo = saldo_inicial
        movimientos = []
        for linea in lineas:
            saldo += saldo_natural(cuenta_actual, linea.debe, linea.haber)
            movimientos.append({"linea": linea, "saldo": saldo})
        bloques.append({
            "cuenta": cuenta_actual, "saldo_inicial": saldo_inicial,
            "movimientos": movimientos, "debe": sum((linea.debe for linea in lineas), CERO),
            "haber": sum((linea.haber for linea in lineas), CERO), "saldo": saldo,
        })
    return bloques


def datos_balance_comprobacion(empresa, fecha_inicio="", fecha_fin="", tipo="", incluir_ceros=False):
    partidas = partidas_empresa(empresa)
    anteriores = _sumas_por_cuenta(partidas.filter(asiento__fecha__lt=fecha_inicio)) if fecha_inicio else {}
    if fecha_inicio:
        partidas = partidas.filter(asiento__fecha__gte=fecha_inicio)
    if fecha_fin:
        partidas = partidas.filter(asiento__fecha__lte=fecha_fin)
    periodo = _sumas_por_cuenta(partidas)
    cuentas = CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "nombre")
    if tipo:
        cuentas = cuentas.filter(tipo=tipo)
    filas = []
    for cuenta in cuentas:
        apertura_debe, apertura_haber = anteriores.get(cuenta.pk, (CERO, CERO))
        apertura = apertura_debe - apertura_haber
        debe, haber = periodo.get(cuenta.pk, (CERO, CERO))
        saldo = apertura + debe - haber
        if not incluir_ceros and not (apertura or debe or haber):
            continue
        filas.append({
            "cuenta": cuenta, "saldo_inicial": apertura,
            "apertura_deudora": max(apertura, CERO), "apertura_acreedora": max(-apertura, CERO),
            "debe": debe, "haber": haber, "saldo_deudor": max(saldo, CERO), "saldo_acreedor": max(-saldo, CERO),
        })
    resumen = {
        "cuentas": len(filas),
        "vista_parcial": bool(tipo),
        "debe_total": sum((fila["debe"] for fila in filas), CERO),
        "haber_total": sum((fila["haber"] for fila in filas), CERO),
        "apertura_deudora_total": sum((fila["apertura_deudora"] for fila in filas), CERO),
        "apertura_acreedora_total": sum((fila["apertura_acreedora"] for fila in filas), CERO),
        "saldo_deudor_total": sum((fila["saldo_deudor"] for fila in filas), CERO),
        "saldo_acreedor_total": sum((fila["saldo_acreedor"] for fila in filas), CERO),
    }
    resumen["cuadrado"] = resumen["debe_total"] == resumen["haber_total"] and resumen["saldo_deudor_total"] == resumen["saldo_acreedor_total"]
    return {"filas": filas, "resumen": resumen}


def cuentas_efectivo_ids(empresa):
    ids = set()
    try:
        configuracion = empresa.configuracion_contable
    except ConfiguracionContableEmpresa.DoesNotExist:
        configuracion = None
    if configuracion:
        ids.update(id_cuenta for id_cuenta in (configuracion.cuenta_caja_id, configuracion.cuenta_bancos_id) if id_cuenta)
    ids.update(CuentaFinanciera.objects.filter(
        empresa=empresa, tipo__in=("caja", "banco"), cuenta_contable__empresa=empresa, cuenta_contable__tipo="activo",
    ).values_list("cuenta_contable_id", flat=True))
    explicitas = ids.copy()
    # Incluye cuentas históricas inactivas: desactivarlas no elimina su efectivo.
    criterios_efectivo = Q(nombre__icontains="caja") | Q(nombre__icontains="banco") | Q(nombre__icontains="efectivo")
    if not es_empresa_rumi(empresa):
        criterios_efectivo |= Q(codigo__startswith="1101") | Q(codigo__startswith="1102")
    ids.update(CuentaContable.objects.filter(empresa=empresa, tipo="activo").filter(
        criterios_efectivo
    ).values_list("id", flat=True))
    tarjetas = set(CuentaFinanciera.objects.filter(empresa=empresa, tipo="tarjeta_credito").values_list("cuenta_contable_id", flat=True))
    # Un vínculo antiguo incorrecto de tarjeta no elimina un banco configurado:
    # su saldo de activo sigue siendo efectivo y se muestra una advertencia.
    return set(CuentaContable.objects.filter(empresa=empresa, tipo="activo", id__in=ids).exclude(id__in=tarjetas - explicitas).values_list("id", flat=True))


def _texto_normalizado(texto):
    return "".join(letra for letra in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(letra))


def clasificar_contrapartida(cuenta, *, empresa=None):
    actual, visitadas = cuenta, set()
    while actual is not None and actual.pk not in visitadas:
        visitadas.add(actual.pk)
        categoria = getattr(actual, "actividad_flujo", "auto")
        if categoria in ("operacion", "inversion", "financiamiento"):
            return categoria
        actual = actual.cuenta_padre
    nombre = _texto_normalizado(cuenta.nombre)
    if cuenta.tipo == "patrimonio":
        return "financiamiento"
    usar_prefijos_honduras = not es_empresa_rumi(
        empresa if empresa is not None and empresa.pk == cuenta.empresa_id else cuenta.empresa
    )
    if cuenta.tipo == "pasivo" and (
        (usar_prefijos_honduras and cuenta.codigo.startswith(("2104", "2201")))
        or any(palabra in nombre for palabra in ("prestamo", "tarjeta", "deuda financiera", "obligacion financiera", "financiamiento", "hipoteca", "bonos por pagar"))
    ):
        return "financiamiento"
    if cuenta.tipo == "activo" and (
        (usar_prefijos_honduras and cuenta.codigo.startswith("12"))
        or any(palabra in nombre for palabra in ("equipo", "mobiliario", "vehiculo", "propiedad", "planta", "maquinaria", "activo fijo", "terreno", "edificio", "inversion financiera"))
    ):
        return "inversion"
    return "operacion"


def datos_flujo_efectivo(empresa, fecha_inicio="", fecha_fin=""):
    from .models import RevisionConversionAsiento
    ids = cuentas_efectivo_ids(empresa)
    grupos = {clave: {"entradas": CERO, "salidas": CERO, "neto": CERO, "movimientos": []} for clave in ("operacion", "inversion", "financiamiento")}
    partidas = partidas_empresa(empresa).filter(cuenta_id__in=ids)
    apertura = partidas.filter(asiento__fecha__lt=fecha_inicio).aggregate(debe=Sum("debe"), haber=Sum("haber")) if fecha_inicio else {}
    cierre = partidas.filter(asiento__fecha__lte=fecha_fin) if fecha_fin else partidas
    cierre = cierre.aggregate(debe=Sum("debe"), haber=Sum("haber"))
    saldo_inicial = (apertura.get("debe") or CERO) - (apertura.get("haber") or CERO)
    saldo_final = (cierre.get("debe") or CERO) - (cierre.get("haber") or CERO)
    asientos = AsientoContable.objects.filter(empresa=empresa, estado="contabilizado", lineas__cuenta_id__in=ids).distinct()
    if fecha_inicio:
        asientos = asientos.filter(fecha__gte=fecha_inicio)
    if fecha_fin:
        asientos = asientos.filter(fecha__lte=fecha_fin)
    diferencias_partidas = CERO
    ajustes_historicos = CERO
    detalle_ajustes = []
    ids_ajustes = set(RevisionConversionAsiento.objects.filter(
        empresa=empresa, origen__empresa=empresa, ajuste__empresa=empresa, ajuste__estado="contabilizado",
    ).values_list("ajuste_id", flat=True))
    for asiento in asientos.prefetch_related("lineas__cuenta").order_by("fecha", "numero", "id"):
        lineas = [linea for linea in asiento.lineas.all() if linea.cuenta.empresa_id == empresa.pk]
        efectivo = [linea for linea in lineas if linea.cuenta_id in ids]
        neto_efectivo = sum((linea.debe - linea.haber for linea in efectivo), CERO)
        if not neto_efectivo:
            continue  # Transferencias internas y operaciones sin variación de efectivo.
        if asiento.pk in ids_ajustes:
            ajustes_historicos += neto_efectivo
            detalle_ajustes.append({"asiento": asiento, "neto": neto_efectivo})
            continue
        categorias = defaultdict(lambda: CERO)
        for linea in lineas:
            if linea.cuenta_id not in ids:
                categorias[clasificar_contrapartida(linea.cuenta, empresa=empresa)] += linea.haber - linea.debe
        # Los asientos antiguos descuadrados conservan una diferencia visible.
        diferencias_partidas += neto_efectivo - sum(categorias.values(), CERO)
        for categoria, neto in categorias.items():
            if not neto:
                continue
            grupo = grupos[categoria]
            grupo["neto"] += neto
            grupo["entradas"] += max(neto, CERO)
            grupo["salidas"] += max(-neto, CERO)
            grupo["movimientos"].append({
                "asiento": asiento, "cuenta": efectivo[0].cuenta,
                "detalle": asiento.descripcion, "entrada": max(neto, CERO), "salida": max(-neto, CERO), "neto": neto,
            })
    total_neto = sum((grupo["neto"] for grupo in grupos.values()), CERO)
    diferencia = saldo_final - saldo_inicial - total_neto - ajustes_historicos
    incompatibles = CuentaFinanciera.objects.filter(empresa=empresa).filter(
        Q(tipo="tarjeta_credito") & ~Q(cuenta_contable__tipo="pasivo")
        | Q(tipo__in=("caja", "banco")) & ~Q(cuenta_contable__tipo="activo")
        | ~Q(cuenta_contable__empresa=empresa)
    ).count()
    return {
        "flujo": grupos,
        "ajustes_historicos": detalle_ajustes,
        "cuentas_efectivo": CuentaContable.objects.filter(empresa=empresa, id__in=ids).order_by("codigo", "nombre"),
        "resumen": {
            "total_neto": total_neto, "operacion": grupos["operacion"]["neto"],
            "inversion": grupos["inversion"]["neto"], "financiamiento": grupos["financiamiento"]["neto"],
            "saldo_inicial": saldo_inicial, "saldo_final": saldo_final, "diferencia": diferencia,
            "ajustes_historicos": ajustes_historicos,
            "diferencia_partidas": diferencias_partidas, "conciliado": diferencia == 0,
            "cuentas_financieras_incompatibles": incompatibles,
            "movimientos": sum((len(grupo["movimientos"]) for grupo in grupos.values()), 0),
        },
    }
