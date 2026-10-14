"""Revisión documentada de todas las partidas USD sin alterar el asiento original."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from core.audit_context import audit_scope

from .models import (
    AsientoContable, CuentaContable, RegularizacionMonedaFactura,
    RevisionConversionAsiento, bloquear_empresas_contables, validar_fecha_contable_abierta,
)
from .moneda import convertir_a_lempiras, redondear_importe, validar_moneda_tipo_cambio


CERO = Decimal("0.00")
MAX_IMPORTE = Decimal("999999999999.99")


def _valor_json(valor):
    if isinstance(valor, (Decimal, date, datetime)):
        return str(valor)
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    return str(valor)


def _datos_objeto(objeto):
    return {
        campo.attname: _valor_json(getattr(objeto, campo.attname))
        for campo in objeto._meta.concrete_fields
    }


def _documento_origen(asiento):
    """Las fuentes conocidas se leen con el mismo límite empresarial del asiento."""
    from facturacion.models import Factura, NotaCredito, PagoComisionProveedor, PagoFactura

    fuentes = {
        "factura": (Factura, "empresa_id"),
        "nota_credito": (NotaCredito, "empresa_id"),
        "pago_factura": (PagoFactura, "factura__empresa_id"),
        "pago_comision_proveedor": (PagoComisionProveedor, "comision_origen__factura__empresa_id"),
    }
    if asiento.documento_tipo not in fuentes:
        return None
    modelo, campo_empresa = fuentes[asiento.documento_tipo]
    documento = modelo.objects.filter(
        pk=asiento.documento_id, **{campo_empresa: asiento.empresa_id},
    ).first()
    if documento is None:
        raise ValidationError("No se encuentra el documento original dentro de esta empresa. Revise su vínculo contable.")
    factura = (
        documento if asiento.documento_tipo == "factura" else
        documento.factura if asiento.documento_tipo == "pago_factura" else
        documento.comision_origen.factura if asiento.documento_tipo == "pago_comision_proveedor" else
        documento.factura_origen
    )
    cliente = getattr(documento, "cliente", None) or (factura.cliente if factura else None)
    if cliente and cliente.empresa_id != asiento.empresa_id:
        raise ValidationError("El cliente del documento original debe pertenecer a esta empresa.")
    if factura and factura.empresa_id != asiento.empresa_id:
        raise ValidationError("La factura vinculada al documento debe pertenecer a esta empresa.")
    if asiento.documento_tipo == "nota_credito" and factura and documento.moneda != factura.moneda:
        raise ValidationError("La moneda de la nota de crédito no coincide con su factura de origen. Aclare el documento original antes de revisar su conversión.")
    if asiento.documento_tipo in ("pago_factura", "pago_comision_proveedor"):
        proveedor = documento.proveedor_comision if asiento.documento_tipo == "pago_factura" else documento.proveedor
        if proveedor and proveedor.empresa_id != asiento.empresa_id:
            raise ValidationError("El proveedor de comisión debe pertenecer a esta empresa.")
        if (asiento.documento_tipo == "pago_comision_proveedor" or documento.monto_comision > 0) and not proveedor:
            raise ValidationError("La comisión debe conservar un proveedor de esta empresa.")
        for nombre in ("cuenta_financiera", "cuenta_financiera_impuesto"):
            cuenta_financiera = getattr(documento, nombre, None)
            if cuenta_financiera and (
                cuenta_financiera.empresa_id != asiento.empresa_id or not cuenta_financiera.cuenta_contable_id
                or cuenta_financiera.cuenta_contable.empresa_id != asiento.empresa_id
            ):
                raise ValidationError("Las cuentas financieras del documento y sus cuentas contables deben pertenecer a esta empresa.")
    return documento


def _datos_documento(asiento, documento):
    if documento is None:
        return None
    datos = {"modelo": documento._meta.label_lower, "documento": _datos_objeto(documento)}
    if hasattr(documento, "lineas"):
        datos["lineas"] = [_datos_objeto(linea) for linea in documento.lineas.order_by("pk")]
    if asiento.documento_tipo == "pago_factura":
        datos["factura"] = _datos_objeto(documento.factura)
    elif asiento.documento_tipo == "nota_credito" and documento.factura_origen_id:
        datos["factura"] = _datos_objeto(documento.factura_origen)
    elif asiento.documento_tipo == "pago_comision_proveedor":
        datos["obligacion"] = _datos_objeto(documento.comision_origen)
        datos["factura"] = _datos_objeto(documento.comision_origen.factura)
    return datos


def _validar_fuente(asiento, lineas):
    if asiento.estado != "contabilizado" or asiento.moneda_origen != "USD" or asiento.conversion_verificada:
        raise ValidationError("Seleccione un asiento histórico contabilizado en dólares pendiente de revisión integral.")
    if RevisionConversionAsiento.objects.filter(origen=asiento).exists():
        raise ValidationError("Este asiento ya tiene una revisión integral documentada.")
    if len(lineas) < 2:
        raise ValidationError("El asiento original debe tener al menos dos partidas válidas.")
    for linea in lineas:
        if linea.cuenta.empresa_id != asiento.empresa_id:
            raise ValidationError("Todas las cuentas del asiento original deben pertenecer a esta empresa.")
        if linea.debe < 0 or linea.haber < 0 or bool(linea.debe) == bool(linea.haber):
            raise ValidationError("El asiento original contiene partidas inválidas. Revise la incidencia antes de convertirlo.")
    debe = sum((linea.debe for linea in lineas), CERO)
    haber = sum((linea.haber for linea in lineas), CERO)
    if debe != haber or debe <= 0:
        raise ValidationError("El asiento original debe estar balanceado antes de revisar su conversión.")


def _regularizacion_parcial(asiento):
    revision = RegularizacionMonedaFactura.objects.filter(asiento_origen=asiento).select_related("asiento_ajuste").first()
    if not revision:
        return None, []
    if revision.empresa_id != asiento.empresa_id or revision.factura_id != asiento.documento_id:
        raise ValidationError("La revisión previa del saldo cliente no corresponde al documento de esta empresa.")
    if not revision.asiento_ajuste_id:
        return revision, []
    ajuste = revision.asiento_ajuste
    if ajuste.empresa_id != asiento.empresa_id or ajuste.estado != "contabilizado":
        raise ValidationError("El ajuste previo del saldo cliente necesita revisión antes de la conversión integral.")
    lineas = list(ajuste.lineas.select_related("cuenta").order_by("pk"))
    if len(lineas) < 2 or any(
        linea.cuenta.empresa_id != asiento.empresa_id or linea.debe < 0 or linea.haber < 0
        or bool(linea.debe) == bool(linea.haber) for linea in lineas
    ) or sum((linea.debe for linea in lineas), CERO) != sum((linea.haber for linea in lineas), CERO):
        raise ValidationError("Las partidas del ajuste previo del saldo cliente no son válidas.")
    return revision, lineas


def _validar_aplicacion_parcial(asiento, documento):
    if documento is None or asiento.documento_tipo not in ("pago_factura", "nota_credito"):
        return
    factura = documento.factura if asiento.documento_tipo == "pago_factura" else documento.factura_origen
    if factura is None:
        return
    parcial = RegularizacionMonedaFactura.objects.filter(factura=factura, empresa_id=asiento.empresa_id).first()
    if parcial and asiento.fecha_creacion <= parcial.fecha_creacion and not revision_conversion_vigente(parcial.asiento_origen):
        raise ValidationError(
            "Esta aplicación quedó incluida en una revisión anterior del saldo cliente. "
            "Revise primero todas las partidas de la factura para compensar su ajuste previo; "
            "después revise sus cobros y notas de crédito históricos antes de registrar nuevas aplicaciones."
        )


def _importe_confirmado(valor):
    try:
        importe = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Cada partida debe indicar su importe original respaldado.")
    if not importe.is_finite() or importe <= 0 or importe > MAX_IMPORTE:
        raise ValidationError("El importe original de cada partida debe ser positivo y estar dentro del límite contable.")
    if importe != redondear_importe(importe):
        raise ValidationError("Los importes originales deben tener como máximo dos decimales.")
    return redondear_importe(importe)


def _validar_partidas_operativas(asiento, documento, lineas, partidas):
    """Conserva el nominal USD de las cuentas que respaldan aplicaciones futuras."""
    if documento is None:
        return
    snapshots = {partida["linea_id"]: partida for partida in partidas}

    def validar_grupo(candidatas, nominal, tipo, concepto):
        if not candidatas or len({linea.cuenta_id for linea in candidatas}) != 1:
            raise ValidationError(f"Identifique una sola cuenta de {concepto} en las partidas originales antes de revisar la conversión.")
        confirmadas = [snapshots[linea.pk] for linea in candidatas]
        if any(linea.cuenta.tipo != tipo for linea in candidatas):
            raise ValidationError(f"La cuenta de {concepto} del documento original no tiene el tipo contable esperado.")
        if any(partida["moneda"] != "USD" for partida in confirmadas):
            raise ValidationError(f"Confirme el importe original de {concepto} en dólares y su tasa respaldada, aunque el saldo ya esté registrado correctamente en lempiras.")
        if len({partida["tipo_cambio"] for partida in confirmadas}) != 1:
            raise ValidationError(f"Las partidas de {concepto} de un mismo documento deben conservar el mismo tipo de cambio documentado.")
        if sum((Decimal(partida["importe_original"]) for partida in confirmadas), CERO) != redondear_importe(nominal):
            raise ValidationError(f"Los importes originales de {concepto} deben sumar el nominal en dólares del documento.")

    if asiento.documento_tipo == "factura" and asiento.evento == "emision":
        candidatas = [linea for linea in lineas if linea.debe > 0 and (linea.detalle or "").startswith("Cobro a cliente")]
        if not candidatas:
            candidatas = [linea for linea in lineas if linea.debe > 0 and linea.cuenta_id == documento.cliente.cuenta_contable_id]
        validar_grupo(candidatas, documento.total, "activo", "cuentas por cobrar")
    elif asiento.documento_tipo == "pago_factura" and asiento.evento == "cobro":
        candidatas = [linea for linea in lineas if linea.haber > 0 and (linea.detalle or "").startswith("Aplicacion a cliente")]
        if not candidatas:
            candidatas = [linea for linea in lineas if linea.haber > 0 and linea.cuenta_id == documento.factura.cliente.cuenta_contable_id]
        validar_grupo(candidatas, documento.total_aplicado, "activo", "aplicaciones a clientes")
        if documento.monto_comision > 0:
            candidatas = [linea for linea in lineas if linea.haber > 0 and (linea.detalle or "").startswith("Comision por pagar")]
            if not candidatas and documento.proveedor_comision_id:
                candidatas = [linea for linea in lineas if linea.haber > 0 and linea.cuenta_id == documento.proveedor_comision.cuenta_contable_id]
            validar_grupo(candidatas, documento.monto_comision, "pasivo", "comisiones por pagar")
    elif asiento.documento_tipo == "nota_credito" and asiento.evento == "emision":
        candidatas = [linea for linea in lineas if linea.haber > 0 and (linea.detalle or "").startswith("Disminucion de cuenta por cobrar")]
        if not candidatas:
            candidatas = [linea for linea in lineas if linea.haber > 0 and linea.cuenta_id == documento.cliente.cuenta_contable_id]
        validar_grupo(candidatas, documento.total, "activo", "notas de crédito a clientes")
    elif asiento.documento_tipo == "pago_comision_proveedor" and asiento.evento == "egreso":
        candidatas = [linea for linea in lineas if linea.debe > 0 and (linea.detalle or "").startswith("Cancelación de comisión")]
        if not candidatas:
            candidatas = [linea for linea in lineas if linea.debe > 0 and linea.cuenta_id == documento.proveedor.cuenta_contable_id]
        validar_grupo(candidatas, documento.monto, "pasivo", "pagos de comisión")


def _cuenta_contrapartida(asiento, cuenta, cuentas_usd, lineas):
    if cuenta is None:
        return None
    cuenta_id = getattr(cuenta, "pk", cuenta)
    cuenta = CuentaContable.objects.filter(pk=cuenta_id, empresa_id=asiento.empresa_id).first()
    if not cuenta or not cuenta.activa or not cuenta.acepta_movimientos:
        raise ValidationError("La contrapartida debe ser una cuenta activa de esta empresa que acepte movimientos.")
    # Las cuentas ya incluidas deben corregirse por su partida y evidencia original,
    # nunca ocultar una diferencia adicional dentro del mismo saldo revisado.
    if cuenta.pk in cuentas_usd or cuenta.pk in {linea.cuenta_id for linea in lineas}:
        raise ValidationError("La contrapartida debe ser distinta de las cuentas del asiento revisado, especialmente clientes y proveedores.")
    return cuenta


def vista_previa_revision(asiento, partidas, cuenta_contrapartida=None):
    """Devuelve el ajuste completo propuesto; ninguna tasa ni importe se supone."""
    asiento = AsientoContable.objects.select_related("empresa").get(pk=asiento.pk, empresa_id=asiento.empresa_id)
    lineas = list(asiento.lineas.select_related("cuenta").order_by("pk"))
    _validar_fuente(asiento, lineas)
    documento = _documento_origen(asiento)
    _validar_aplicacion_parcial(asiento, documento)
    revision_parcial, previas = _regularizacion_parcial(asiento)
    if not isinstance(partidas, (list, tuple)) or len(partidas) != len(lineas):
        raise ValidationError("Revise exactamente todas las partidas del asiento, sin omitir ni repetir ninguna.")
    por_id = {}
    for partida in partidas:
        if not isinstance(partida, dict):
            raise ValidationError("Cada partida revisada debe identificar una línea del asiento original.")
        try:
            linea_id = int(partida.get("linea_id"))
        except (ValueError, TypeError):
            raise ValidationError("Cada partida revisada debe identificar una línea del asiento original.")
        if linea_id in por_id:
            raise ValidationError("No se puede revisar dos veces la misma partida.")
        por_id[linea_id] = partida
    if set(por_id) != {linea.pk for linea in lineas}:
        raise ValidationError("Todas las partidas deben corresponder al asiento original de esta empresa.")
    snapshots, cuentas_usd = [], set()
    diferencias = defaultdict(lambda: CERO)
    for linea in lineas:
        partida = por_id[linea.pk]
        moneda = partida.get("moneda")
        if moneda not in ("HNL", "USD"):
            raise ValidationError("Confirme si cada partida está documentada en lempiras o en dólares.")
        original = _importe_confirmado(partida.get("importe_original"))
        try:
            tasa_confirmada = Decimal(str(partida.get("tipo_cambio")))
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError("Cada partida debe indicar el tipo de cambio respaldado; en lempiras corresponde a uno.")
        if not tasa_confirmada.is_finite() or tasa_confirmada <= 0:
            raise ValidationError("El tipo de cambio de cada partida debe ser mayor que cero.")
        if moneda == "HNL" and tasa_confirmada != 1:
            raise ValidationError("Una partida documentada en lempiras debe conservar el tipo de cambio uno.")
        _moneda, tasa = validar_moneda_tipo_cambio(moneda, tasa_confirmada)
        if tasa != tasa_confirmada or tasa > Decimal("99999999.999999"):
            raise ValidationError("El tipo de cambio debe tener como máximo seis decimales y estar dentro del límite contable.")
        convertido = convertir_a_lempiras(original, moneda, tasa)
        if convertido <= 0 or convertido > MAX_IMPORTE:
            raise ValidationError("El importe convertido debe ser mayor que cero y estar dentro del límite contable de una partida.")
        lado = "debe" if linea.debe else "haber"
        debe, haber = (convertido, CERO) if lado == "debe" else (CERO, convertido)
        snapshots.append({
            "linea_id": linea.pk, "cuenta_id": linea.cuenta_id, "moneda": moneda,
            "importe_original": str(original), "tipo_cambio": str(tasa),
            "debe_hnl": str(debe), "haber_hnl": str(haber),
            "debe_anterior": str(linea.debe), "haber_anterior": str(linea.haber),
            "lado": lado, "detalle": linea.detalle or "", "cuenta_tipo": linea.cuenta.tipo,
        })
        diferencias[linea.cuenta_id] += debe - haber - linea.debe + linea.haber
        if moneda == "USD":
            cuentas_usd.add(linea.cuenta_id)
    _validar_partidas_operativas(asiento, documento, lineas, snapshots)
    for linea in previas:
        diferencias[linea.cuenta_id] -= linea.debe - linea.haber
    cuenta = _cuenta_contrapartida(asiento, cuenta_contrapartida, cuentas_usd, lineas)
    propuestas = [
        {"cuenta_id": cuenta_id, "detalle": "Ajuste de conversión histórica documentada",
         "debe": str(max(diferencia, CERO)), "haber": str(max(-diferencia, CERO))}
        for cuenta_id, diferencia in sorted(diferencias.items()) if diferencia
    ]
    diferencia_debe = sum((Decimal(linea["debe"]) for linea in propuestas), CERO)
    diferencia_haber = sum((Decimal(linea["haber"]) for linea in propuestas), CERO)
    desequilibrio = diferencia_debe - diferencia_haber
    if desequilibrio and cuenta:
        existente = next((linea for linea in propuestas if linea["cuenta_id"] == cuenta.pk), None)
        if existente:
            neto = Decimal(existente["debe"]) - Decimal(existente["haber"]) - desequilibrio
            existente.update(debe=str(max(neto, CERO)), haber=str(max(-neto, CERO)),
                             detalle="Compensación del ajuste previo y contrapartida autorizada")
            if not neto:
                propuestas.remove(existente)
        else:
            propuestas.append({
                "cuenta_id": cuenta.pk, "detalle": "Contrapartida autorizada de la revisión de conversión",
                "debe": str(max(-desequilibrio, CERO)), "haber": str(max(desequilibrio, CERO)),
            })
    cuentas_ajuste = CuentaContable.objects.filter(
        pk__in={linea["cuenta_id"] for linea in propuestas} | ({cuenta.pk} if cuenta else set()),
    ).order_by("pk")
    snapshots[0]["respaldo_cuentas_ajuste"] = [
        {"cuenta_id": actual.pk, "empresa_id": actual.empresa_id, "tipo": actual.tipo}
        for actual in cuentas_ajuste
    ]
    fecha_minima = max(asiento.fecha, revision_parcial.fecha) if revision_parcial else asiento.fecha
    if revision_parcial and revision_parcial.asiento_ajuste_id:
        fecha_minima = max(fecha_minima, revision_parcial.asiento_ajuste.fecha)
    anteriores = []
    if revision_parcial:
        anteriores.append({
            "revision_id": revision_parcial.pk, "asiento_id": revision_parcial.asiento_ajuste_id,
            "fecha": str(revision_parcial.fecha), "motivo": revision_parcial.motivo,
            "partidas": [_datos_objeto(linea) for linea in previas],
        })
    respaldo = {
        "origen": _datos_objeto(asiento), "documento": _datos_documento(asiento, documento),
        "lineas": [{"partida": _datos_objeto(linea), "cuenta": _datos_objeto(linea.cuenta)} for linea in lineas],
        "revision_parcial": _datos_objeto(revision_parcial) if revision_parcial else None,
        "ajuste_parcial": _datos_objeto(revision_parcial.asiento_ajuste) if revision_parcial and revision_parcial.asiento_ajuste_id else None,
        "lineas_previas": [{"partida": _datos_objeto(linea), "cuenta": _datos_objeto(linea.cuenta)} for linea in previas],
        "contrapartida": _datos_objeto(cuenta) if cuenta else None,
    }
    firma = hashlib.sha256(json.dumps(respaldo, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    advertencias = []
    if anteriores:
        advertencias.append("La revisión integral compensa todas las partidas del ajuste anterior del saldo cliente; el respaldo original se conserva.")
    if asiento.documento_tipo == "factura" and documento:
        pendientes = AsientoContable.objects.filter(empresa_id=asiento.empresa_id, estado="contabilizado", conversion_verificada=False).filter(
            Q(documento_tipo="pago_factura", documento_id__in=documento.pagos_facturacion.values("pk"))
            | Q(documento_tipo="nota_credito", documento_id__in=documento.notas_credito.values("pk"))
        ).filter(revision_conversion__isnull=True).count()
        if pendientes:
            advertencias.append(f"Hay {pendientes} aplicaciones históricas pendientes de revisión integral. Revise también sus cobros y notas de crédito antes de continuar operaciones.")
    return {
        "partidas": snapshots, "firma_fuente": firma, "fecha_minima": fecha_minima,
        "diferencia_debe": diferencia_debe, "diferencia_haber": diferencia_haber,
        "requiere_contrapartida": bool(desequilibrio), "cuenta_contrapartida_id": cuenta.pk if cuenta else None,
        "lineas_ajuste": propuestas, "ajustes_previos": anteriores, "advertencias": advertencias,
        "total_debe_hnl": sum((Decimal(partida["debe_hnl"]) for partida in snapshots), CERO),
        "total_haber_hnl": sum((Decimal(partida["haber_hnl"]) for partida in snapshots), CERO),
        "total_debe_ajuste": sum((Decimal(linea["debe"]) for linea in propuestas), CERO),
        "total_haber_ajuste": sum((Decimal(linea["haber"]) for linea in propuestas), CERO),
    }


def revision_conversion_vigente(asiento, fecha_corte=None):
    """Lee sólo revisiones completas con respaldo publicado y efecto al corte."""
    revision = RevisionConversionAsiento.objects.filter(origen_id=asiento.pk).select_related("origen", "ajuste").first()
    if revision is None:
        return None
    if fecha_corte not in (None, ""):
        if isinstance(fecha_corte, str):
            try:
                fecha_corte = date.fromisoformat(fecha_corte)
            except ValueError:
                raise ValidationError("Indique una fecha de corte válida para consultar la revisión.")
        if not isinstance(fecha_corte, date):
            raise ValidationError("Indique una fecha de corte válida para consultar la revisión.")
        if revision.fecha > fecha_corte:
            return None
    if (revision.empresa_id != asiento.empresa_id or revision.origen.empresa_id != revision.empresa_id
            or revision.origen.estado != "contabilizado" or revision.origen.moneda_origen != "USD"
            or revision.origen.conversion_verificada):
        raise ValidationError("El asiento original de la revisión de conversión requiere revisión contable.")
    if revision.ajuste_id and (
        revision.ajuste_id == revision.origen_id or revision.ajuste.empresa_id != revision.empresa_id
        or revision.ajuste.estado != "contabilizado" or revision.ajuste.fecha != revision.fecha
        or revision.ajuste.documento_tipo != "revision_conversion" or revision.ajuste.documento_id != revision.origen_id
        or revision.ajuste.evento != "revision_integral" or revision.ajuste.moneda_origen != "HNL"
        or revision.ajuste.tipo_cambio != 1 or not revision.ajuste.conversion_verificada
    ):
        raise ValidationError("El ajuste que respalda la conversión histórica debe estar contabilizado en la fecha y empresa autorizadas.")
    if not isinstance(revision.partidas, list) or not revision.partidas or not revision.firma_fuente:
        raise ValidationError("La revisión histórica no conserva el respaldo completo de sus partidas.")
    originales = {linea.pk: linea for linea in revision.origen.lineas.select_related("cuenta")}
    diferencias = defaultdict(lambda: CERO)
    try:
        ids = [partida["linea_id"] for partida in revision.partidas]
        if len(ids) != len(set(ids)) or set(ids) != set(originales):
            raise ValueError
        for partida in revision.partidas:
            linea = originales[partida["linea_id"]]
            if (partida["cuenta_id"] != linea.cuenta_id or linea.cuenta.empresa_id != revision.empresa_id
                    or partida["cuenta_tipo"] != linea.cuenta.tipo):
                raise ValueError
            if Decimal(partida["debe_anterior"]) != linea.debe or Decimal(partida["haber_anterior"]) != linea.haber:
                raise ValueError
            if linea.debe < 0 or linea.haber < 0 or bool(linea.debe) == bool(linea.haber):
                raise ValueError
            moneda = partida["moneda"]
            if moneda not in ("HNL", "USD"):
                raise ValueError
            original = _importe_confirmado(partida["importe_original"])
            tasa = Decimal(partida["tipo_cambio"])
            _moneda, normalizada = validar_moneda_tipo_cambio(moneda, tasa)
            if not tasa.is_finite() or tasa <= 0 or normalizada != tasa:
                raise ValueError
            convertido = convertir_a_lempiras(original, moneda, tasa)
            debe = Decimal(partida["debe_hnl"])
            haber = Decimal(partida["haber_hnl"])
            if convertido <= 0 or convertido > MAX_IMPORTE:
                raise ValueError
            if partida["lado"] != ("debe" if linea.debe else "haber"):
                raise ValueError
            if (debe, haber) != ((convertido, CERO) if linea.debe else (CERO, convertido)):
                raise ValueError
            diferencias[linea.cuenta_id] += debe - haber - linea.debe + linea.haber
        respaldo_cuentas = revision.partidas[0]["respaldo_cuentas_ajuste"]
        if not isinstance(respaldo_cuentas, list):
            raise ValueError
        cuentas = {cuenta.pk: cuenta for cuenta in CuentaContable.objects.filter(pk__in=[item["cuenta_id"] for item in respaldo_cuentas])}
        if len(cuentas) != len(respaldo_cuentas):
            raise ValueError
        for item in respaldo_cuentas:
            cuenta = cuentas[item["cuenta_id"]]
            if cuenta.empresa_id != revision.empresa_id or item["empresa_id"] != revision.empresa_id or item["tipo"] != cuenta.tipo:
                raise ValueError
        if revision.cuenta_contrapartida_id and revision.cuenta_contrapartida_id not in cuentas:
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValidationError("Las partidas originales no coinciden con el respaldo de la revisión histórica.")
    documento = _documento_origen(revision.origen)
    _validar_partidas_operativas(revision.origen, documento, list(originales.values()), revision.partidas)
    parcial, previas = _regularizacion_parcial(revision.origen)
    if revision.fecha < revision.origen.fecha or (parcial and revision.fecha < parcial.fecha):
        raise ValidationError("La fecha de la revisión histórica no conserva la secuencia del documento y sus ajustes previos.")
    for linea in previas:
        diferencias[linea.cuenta_id] -= linea.debe - linea.haber
    desequilibrio = sum(diferencias.values(), CERO)
    if desequilibrio:
        if revision.cuenta_contrapartida_id is None:
            raise ValidationError("La revisión histórica necesita la contrapartida que respalda sus importes confirmados.")
        diferencias[revision.cuenta_contrapartida_id] -= desequilibrio
    esperadas = {cuenta_id: diferencia for cuenta_id, diferencia in diferencias.items() if diferencia}
    if bool(esperadas) != bool(revision.ajuste_id):
        raise ValidationError("La revisión histórica no conserva el ajuste completo que respalda sus diferencias.")
    if revision.ajuste_id:
        publicadas = {}
        for linea in revision.ajuste.lineas.select_related("cuenta"):
            if (linea.cuenta_id in publicadas or linea.cuenta_id not in cuentas
                    or linea.cuenta.empresa_id != revision.empresa_id or linea.debe < 0 or linea.haber < 0
                    or bool(linea.debe) == bool(linea.haber)):
                raise ValidationError("Las partidas del ajuste de conversión no coinciden con el respaldo confirmado.")
            publicadas[linea.cuenta_id] = linea.debe - linea.haber
        if publicadas != esperadas or not revision.ajuste.esta_balanceado:
            raise ValidationError("Las partidas del ajuste de conversión no coinciden con el respaldo confirmado.")
    return revision


@transaction.atomic
def registrar_revision_conversion(*, asiento, partidas, fecha, cuenta_contrapartida=None, motivo, usuario, vista_previa_esperada=None):
    bloquear_empresas_contables([asiento.empresa_id])
    asiento = AsientoContable.objects.select_for_update().select_related("empresa").get(pk=asiento.pk, empresa_id=asiento.empresa_id)
    existente = RevisionConversionAsiento.objects.filter(origen=asiento).first()
    if existente:
        return revision_conversion_vigente(asiento)
    validar_fecha_contable_abierta(asiento.empresa_id, fecha)
    motivo = (motivo or "").strip()
    if len(motivo) < 10 or len(motivo) > 2000:
        raise ValidationError("Describa el motivo y respaldo de la revisión entre 10 y 2,000 caracteres.")
    ids_cuentas = set(asiento.lineas.values_list("cuenta_id", flat=True))
    if cuenta_contrapartida is not None:
        ids_cuentas.add(getattr(cuenta_contrapartida, "pk", cuenta_contrapartida))
    list(CuentaContable.objects.select_for_update().filter(pk__in=ids_cuentas).order_by("pk"))
    previa = vista_previa_revision(asiento, partidas, cuenta_contrapartida)
    if vista_previa_esperada is not None:
        campos = ("partidas", "firma_fuente", "fecha_minima", "cuenta_contrapartida_id", "lineas_ajuste")
        if any(previa[campo] != vista_previa_esperada.get(campo) for campo in campos):
            raise ValidationError("El documento o sus partidas cambiaron. Revise nuevamente la propuesta antes de confirmarla.")
    if fecha < previa["fecha_minima"]:
        raise ValidationError("La revisión no puede anticipar la fecha del asiento original ni sus ajustes previos.")
    if previa["requiere_contrapartida"] and previa["cuenta_contrapartida_id"] is None:
        raise ValidationError("Las partidas revisadas requieren una contrapartida autorizada para balancear la diferencia. Revise su respaldo antes de registrar.")
    ajuste = None
    from .services import registrar_asiento_documento

    with audit_scope(user=usuario, reason=motivo):
        if previa["lineas_ajuste"]:
            cuentas = {cuenta.pk: cuenta for cuenta in CuentaContable.objects.filter(pk__in=[linea["cuenta_id"] for linea in previa["lineas_ajuste"]])}
            if any(not cuenta.activa or not cuenta.acepta_movimientos or cuenta.empresa_id != asiento.empresa_id for cuenta in cuentas.values()):
                raise ValidationError("Las cuentas que recibirán el ajuste deben estar activas y aceptar movimientos dentro de esta empresa.")
            ajuste = registrar_asiento_documento(
                empresa=asiento.empresa, documento_tipo="revision_conversion", documento_id=asiento.pk,
                evento="revision_integral", fecha=fecha,
                descripcion=f"Revisión integral de conversión histórica {asiento.numero or asiento.pk}",
                referencia=asiento.referencia or str(asiento.pk), origen_modulo="contabilidad", creado_por=usuario,
                lineas=[{"cuenta": cuentas[linea["cuenta_id"]], "detalle": linea["detalle"],
                         "debe": Decimal(linea["debe"]), "haber": Decimal(linea["haber"])} for linea in previa["lineas_ajuste"]],
            )
        return RevisionConversionAsiento.objects.create(
            empresa=asiento.empresa, origen=asiento, ajuste=ajuste, fecha=fecha,
            partidas=previa["partidas"], firma_fuente=previa["firma_fuente"],
            cuenta_contrapartida_id=previa["cuenta_contrapartida_id"], motivo=motivo, creado_por=usuario,
        )
