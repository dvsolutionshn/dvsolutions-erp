"""Regulariza saldos de clientes del legado USD mediante un ajuste documentado."""
from decimal import Decimal
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q

from core.audit_context import audit_scope
from facturacion.models import Factura

from .models import AsientoContable, CuentaContable, RegularizacionMonedaFactura, RevisionConversionAsiento, bloquear_empresas_contables, validar_fecha_contable_abierta
from .moneda import convertir_a_lempiras, redondear_importe, validar_moneda_tipo_cambio
from .services import _aplicaciones_historicas_factura, registrar_asiento_documento


def vista_previa_regularizacion(asiento, tipo_cambio=None):
    if RevisionConversionAsiento.objects.filter(origen=asiento).exists():
        raise ValidationError('El asiento ya tiene una revisión integral de moneda. Consulte el respaldo de esa revisión.')
    if asiento.documento_tipo != 'factura' or asiento.evento != 'emision' or asiento.estado != 'contabilizado':
        raise ValidationError('Seleccione el asiento original contabilizado de una factura.')
    factura = Factura.objects.select_related('cliente').filter(pk=asiento.documento_id, empresa_id=asiento.empresa_id).first()
    if not factura or factura.moneda != 'USD' or factura.estado != 'emitida':
        raise ValidationError('La revisión corresponde únicamente a facturas emitidas en dólares de esta empresa.')
    if factura.cliente.empresa_id != asiento.empresa_id:
        raise ValidationError('El cliente del documento debe pertenecer a esta empresa.')
    if asiento.conversion_verificada:
        raise ValidationError('La conversión de este asiento ya está documentada.')
    if RegularizacionMonedaFactura.objects.filter(asiento_origen=asiento).exists():
        raise ValidationError('El saldo de esta factura ya fue regularizado.')
    _moneda, tasa = validar_moneda_tipo_cambio('USD', tipo_cambio if tipo_cambio is not None else factura.tipo_cambio)
    cargo = asiento.lineas.filter(detalle__startswith='Cobro a cliente').select_related('cuenta').first()
    if not cargo:
        cargo = asiento.lineas.filter(cuenta_id=factura.cliente.cuenta_contable_id, debe__gt=0).select_related('cuenta').first()
    if not cargo or cargo.cuenta.empresa_id != asiento.empresa_id or cargo.cuenta.tipo != 'activo':
        raise ValidationError('Identifique primero la cuenta por cobrar del asiento original.')
    existentes = set(AsientoContable.objects.filter(
        empresa_id=asiento.empresa_id, estado='contabilizado',
    ).values_list('documento_tipo', 'documento_id', 'evento'))
    pendientes = 0
    pagos_activos = factura.pagos_facturacion.filter(anulado=False)
    for pago in pagos_activos:
        if ('pago_factura', pago.pk, 'cobro') not in existentes:
            pendientes += 1
    for nota in factura.notas_credito.filter(estado='emitida'):
        if ('nota_credito', nota.pk, 'emision') not in existentes:
            pendientes += 1
    aplicado_original, aplicado_hnl = _aplicaciones_historicas_factura(factura, cargo.cuenta)
    notas_original, notas_hnl = _aplicaciones_historicas_factura(factura, cargo.cuenta, incluir_pagos=False)
    ultima_aplicacion = AsientoContable.objects.filter(
        Q(documento_tipo='pago_factura', documento_id__in=pagos_activos.values('pk'))
        | Q(documento_tipo='nota_credito', documento_id__in=factura.notas_credito.values('pk')),
        empresa_id=asiento.empresa_id, estado='contabilizado', lineas__cuenta=cargo.cuenta,
    ).aggregate(fecha=Max('fecha'))['fecha']
    total_original = redondear_importe(factura.total)
    if total_original <= 0:
        raise ValidationError('El documento original debe tener un total mayor que cero.')
    total_hnl = convertir_a_lempiras(total_original, 'USD', tasa)
    aplicado_corregido = redondear_importe(total_hnl * aplicado_original / total_original)
    notas_corregidas = redondear_importe(total_hnl * notas_original / total_original)
    saldo_anterior = redondear_importe(cargo.debe - cargo.haber - aplicado_hnl)
    saldo_corregido = total_hnl - aplicado_corregido
    fuentes = AsientoContable.objects.filter(
        Q(pk=asiento.pk)
        | Q(documento_tipo='pago_factura', documento_id__in=pagos_activos.values('pk'))
        | Q(documento_tipo='nota_credito', documento_id__in=factura.notas_credito.values('pk')),
        empresa_id=asiento.empresa_id,
    )
    respaldo = {
        'factura': list(Factura.objects.filter(pk=factura.pk).values()),
        'pagos': list(pagos_activos.order_by('pk').values()),
        'notas': list(factura.notas_credito.order_by('pk').values()),
        'asientos': list(fuentes.order_by('pk').values()),
        'partidas': list(asiento.lineas.model.objects.filter(asiento__in=fuentes).order_by('pk').values()),
    }
    firma_fuente = sha256(json.dumps(respaldo, default=str, sort_keys=True).encode('utf-8')).hexdigest()
    return {
        'firma_fuente': firma_fuente,
        'factura': factura, 'cuenta_cliente': cargo.cuenta, 'tipo_cambio': tasa,
        'total_original': total_original, 'total_hnl': total_hnl,
        'saldo_original': total_original - aplicado_original,
        'saldo_hnl_anterior': saldo_anterior, 'saldo_hnl_corregido': saldo_corregido,
        'diferencia': saldo_corregido - saldo_anterior,
        'aplicaciones_pendientes': pendientes,
        'fecha_minima': max(asiento.fecha, ultima_aplicacion) if ultima_aplicacion else asiento.fecha,
        'aplicado_original': aplicado_original, 'aplicado_hnl_anterior': aplicado_hnl,
        'aplicado_hnl_corregido': aplicado_corregido,
        'notas_original': notas_original, 'notas_hnl_anterior': notas_hnl, 'notas_hnl_corregido': notas_corregidas,
    }


@transaction.atomic
def regularizar_factura_historica(*, asiento, fecha, tipo_cambio, cuenta_contrapartida, motivo, usuario, vista_previa_esperada=None):
    bloquear_empresas_contables([asiento.empresa_id])
    asiento = AsientoContable.objects.select_for_update().get(pk=asiento.pk, empresa_id=asiento.empresa_id)
    existente = RegularizacionMonedaFactura.objects.filter(asiento_origen=asiento).first()
    if existente:
        return existente
    Factura.objects.select_for_update().get(pk=asiento.documento_id, empresa_id=asiento.empresa_id)
    validar_fecha_contable_abierta(asiento.empresa_id, fecha)
    cuenta_contrapartida = CuentaContable.objects.select_for_update().filter(
        pk=cuenta_contrapartida.pk, empresa_id=asiento.empresa_id,
    ).first()
    if cuenta_contrapartida is None:
        raise ValidationError('La contrapartida debe pertenecer a esta empresa.')
    if cuenta_contrapartida.empresa_id != asiento.empresa_id or not cuenta_contrapartida.activa or not cuenta_contrapartida.acepta_movimientos:
        raise ValidationError('La contrapartida debe ser una cuenta activa de esta empresa que acepte movimientos.')
    motivo = (motivo or '').strip()
    if len(motivo) < 10:
        raise ValidationError('Explique el motivo de la regularización con al menos 10 caracteres.')
    datos = vista_previa_regularizacion(asiento, tipo_cambio)
    if vista_previa_esperada is not None:
        campos = [
            'firma_fuente', 'cuenta_cliente',
            'total_original', 'total_hnl', 'saldo_original', 'saldo_hnl_anterior', 'saldo_hnl_corregido', 'diferencia',
            'aplicado_original', 'aplicado_hnl_anterior', 'aplicado_hnl_corregido',
            'notas_original', 'notas_hnl_anterior', 'notas_hnl_corregido', 'aplicaciones_pendientes', 'fecha_minima',
        ]
        if any(datos[campo] != vista_previa_esperada.get(campo) for campo in campos):
            raise ValidationError('El saldo cambió durante la confirmación. Revise la nueva vista previa antes de registrar el ajuste.')
    datos.pop('firma_fuente')
    if fecha < datos.pop('fecha_minima'):
        raise ValidationError('La fecha del ajuste debe ser igual o posterior al documento original y a sus aplicaciones contabilizadas.')
    if cuenta_contrapartida.pk == datos['cuenta_cliente'].pk:
        raise ValidationError('La contrapartida debe ser distinta de la cuenta por cobrar.')
    diferencia = datos.pop('diferencia')
    datos.pop('aplicaciones_pendientes')
    ajuste = None
    with audit_scope(user=usuario, reason=motivo):
        if diferencia:
            ajuste = registrar_asiento_documento(
                empresa=asiento.empresa, documento_tipo='factura', documento_id=datos['factura'].pk,
                evento='regularizacion_moneda', fecha=fecha,
                descripcion=f"Regularización del saldo USD de factura {datos['factura'].numero_factura or datos['factura'].pk}",
                referencia=datos['factura'].numero_factura, origen_modulo='contabilidad', creado_por=usuario,
                lineas=[
                    {'cuenta': datos['cuenta_cliente'], 'debe': max(diferencia, Decimal('0')), 'haber': max(-diferencia, Decimal('0')), 'detalle': 'Ajuste del saldo histórico del cliente'},
                    {'cuenta': cuenta_contrapartida, 'debe': max(-diferencia, Decimal('0')), 'haber': max(diferencia, Decimal('0')), 'detalle': motivo[:200]},
                ],
            )
        return RegularizacionMonedaFactura.objects.create(
            empresa=asiento.empresa, asiento_origen=asiento, asiento_ajuste=ajuste,
            fecha=fecha, creado_por=usuario, motivo=motivo, **datos,
        )
