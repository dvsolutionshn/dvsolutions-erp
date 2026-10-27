"""Anulación atómica de cobros, conservando el documento y su contabilidad original."""
from datetime import date, datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from contabilidad.models import (
    AsientoContable, MovimientoBancario, RegularizacionMonedaFactura,
    bloquear_empresas_contables, validar_fecha_contable_abierta,
)
from contabilidad.proteccion_conversion import asiento_tiene_revision
from contabilidad.services import registrar_reversion_documento
from core.models import RegistroAuditoria
from core.clinical_permissions import rol_clinico_granular

from .models import CierreCaja, Factura, PagoFactura


def puede_anular_pago(usuario, empresa):
    rol = rol_clinico_granular(usuario, empresa)
    permiso = "puede_anular_pagos_clientes" if rol and rol.usa_permisos_clinicos_v2 else "puede_anular_facturas"
    return bool(
        getattr(usuario, 'is_authenticated', False)
        and usuario.is_active
        and usuario.puede_acceder_empresa(empresa)
        and usuario.tiene_permiso_erp(permiso, empresa)
    )


@transaction.atomic
def anular_pago_factura(pago, *, usuario, motivo, fecha=None):
    empresa = pago.factura.empresa
    if not puede_anular_pago(usuario, empresa):
        raise PermissionDenied('Tu usuario no tiene permiso para anular pagos en esta empresa.')

    bloquear_empresas_contables([empresa.pk])
    factura = Factura.objects.select_for_update().get(pk=pago.factura_id, empresa=empresa)
    pago = PagoFactura.objects.select_for_update().select_related(
        'factura__empresa', 'proveedor_comision',
    ).get(pk=pago.pk, factura=factura)
    # Una repetición del POST no genera otra reversión ni reemplaza su motivo.
    if pago.anulado:
        return pago
    motivo = str(motivo or '').strip()
    if not 5 <= len(motivo) <= 500:
        raise ValidationError('Describe el motivo de la anulación entre 5 y 500 caracteres.')
    fecha = fecha if fecha is not None else timezone.localdate()
    if isinstance(fecha, datetime) or not isinstance(fecha, date):
        raise ValidationError('Indica una fecha válida para la anulación.')
    if fecha < pago.fecha or fecha > timezone.localdate():
        raise ValidationError('La fecha de anulación debe estar entre la fecha del cobro y hoy.')
    if factura.estado == 'anulada':
        raise ValidationError('La factura está anulada. Revisa su anulación antes de corregir sus cobros.')
    validar_fecha_contable_abierta(empresa.pk, pago.fecha)
    validar_fecha_contable_abierta(empresa.pk, fecha)
    if RegularizacionMonedaFactura.objects.filter(factura=factura).exists():
        raise ValidationError('La factura tiene una regularización monetaria documentada. Requiere un ajuste contable que conserve su respaldo.')
    cierres = CierreCaja.objects.filter(empresa=empresa, fecha=pago.fecha, estado__in=['cerrado', 'revisado'])
    if pago.cajero_id:
        cierres = cierres.filter(cajero_id=pago.cajero_id)
    if cierres.exists():
        raise ValidationError('El cobro pertenece a un cierre de caja cerrado o revisado. Revisa el cierre antes de anularlo.')

    pagos_comision = list(pago.pagos_comision_proveedor.select_for_update().all())
    if any(p.metodo != 'compensacion' for p in pagos_comision):
        raise ValidationError('La comisión ya tiene pagos al proveedor. Revierte primero esos pagos desde Cuentas por pagar y vuelve a anular el cobro.')

    asientos = list(AsientoContable.objects.select_for_update().filter(
        empresa=empresa, documento_tipo='pago_factura', documento_id=pago.pk,
    ).prefetch_related('lineas'))
    if any(a.evento != 'cobro' for a in asientos):
        raise ValidationError('El cobro ya tiene ajustes o reversiones contables. Revisa sus asientos antes de anularlo.')
    if AsientoContable.objects.filter(
        empresa=empresa, documento_tipo='asiento_contable',
        documento_id__in=[a.pk for a in asientos], evento='reversion',
    ).exists():
        raise ValidationError('El asiento del cobro ya tiene una reversión manual. Revisa esa corrección antes de anular el pago.')
    for asiento in asientos:
        validar_fecha_contable_abierta(empresa.pk, asiento.fecha)
        if asiento_tiene_revision(asiento.pk):
            raise ValidationError('El cobro tiene una revisión integral de moneda documentada. Requiere un nuevo ajuste para conservar su respaldo.')
        if asiento.estado == 'contabilizado' and not asiento.esta_balanceado:
            raise ValidationError('El asiento original del cobro no está balanceado. Revisa su contabilidad antes de anularlo.')

    movimientos = list(MovimientoBancario.objects.select_for_update().filter(
        Q(pago_factura_id=pago.pk) | Q(asiento_id__in=[a.pk for a in asientos]),
    ))
    for movimiento in movimientos:
        if movimiento.empresa_id != empresa.pk or movimiento.pago_factura_id not in (None, pago.pk):
            raise ValidationError('El vínculo bancario del cobro no coincide con su empresa o documento. Revisa la conciliación.')
        if movimiento.asiento_id and movimiento.asiento_id not in {a.pk for a in asientos}:
            raise ValidationError('El movimiento bancario está vinculado a otro asiento. Revisa su aplicación antes de anular el cobro.')
        if movimiento.conciliado:
            raise ValidationError('El cobro está conciliado con un movimiento bancario. Desconcilia el movimiento antes de anular el pago.')
        validar_fecha_contable_abierta(empresa.pk, movimiento.fecha)

    reversion = registrar_reversion_documento(
        empresa=empresa, documento_tipo='pago_factura', documento_id=pago.pk,
        evento_origen='cobro', evento_reversion='anulacion', fecha=fecha,
        descripcion=f'Anulación cobro factura {factura.numero_factura or factura.pk}',
        referencia=pago.referencia or str(factura.numero_factura or factura.pk),
        origen_modulo='facturacion', creado_por=usuario,
    )
    if any(a.estado == 'contabilizado' for a in asientos) and (
        not reversion or reversion.estado != 'contabilizado' or not reversion.esta_balanceado
    ):
        raise ValidationError('No se pudo completar la reversión contable del cobro.')
    for asiento in asientos:
        if asiento.estado == 'borrador':
            asiento.estado = 'anulado'
            asiento.save(update_fields=['estado'])
    # El extracto bancario se conserva para su aplicación correcta; no se crea
    # un egreso ficticio por la compensación retenida ni se elimina el extracto.
    for movimiento in movimientos:
        movimiento.pago_factura = None
        movimiento.asiento = None
        movimiento.estado = 'clasificado' if movimiento.clasificacion_id else 'pendiente'
        movimiento.save(update_fields=['pago_factura', 'asiento', 'estado'])

    PagoFactura.objects.filter(pk=pago.pk, anulado=False).update(
        anulado=True, fecha_anulacion=fecha, anulado_el=timezone.now(),
        anulado_por=usuario, motivo_anulacion=motivo,
    )
    pago.refresh_from_db()
    factura.actualizar_estado_pago()
    RegistroAuditoria.objects.create(
        empresa=empresa, usuario=usuario, accion='modificar', modulo='Facturacion',
        app_label='facturacion', modelo='factura', objeto_id=str(factura.pk),
        objeto_representacion=str(factura)[:300], motivo=motivo,
        cambios={
            'accion_factura': {'anterior': None, 'nuevo': 'anular_pago'},
            'pago_id': {'anterior': pago.pk, 'nuevo': pago.pk},
            'anulado': {'anterior': False, 'nuevo': True},
            'total_aplicado': {'anterior': str(pago.total_aplicado), 'nuevo': '0.00'},
            'fecha_anulacion': {'anterior': None, 'nuevo': fecha.isoformat()},
            'asiento_reversion_id': {'anterior': None, 'nuevo': reversion.pk if reversion else None},
        },
    )
    return pago
