"""Conserva la pareja de asientos que respalda un cobro anulado."""
from django.core.exceptions import ValidationError


def validar_asiento_id_cobro_anulado(asiento_id, using='default'):
    if not asiento_id:
        return
    from .models import AsientoContable

    fuente = AsientoContable.objects.using(using).filter(pk=asiento_id).values(
        'documento_tipo', 'documento_id',
    ).first()
    if fuente:
        validar_fuente_cobro_anulado(fuente['documento_tipo'], fuente['documento_id'], using)


def validar_fuente_cobro_anulado(tipo, documento_id, using='default'):
    if tipo != 'pago_factura' or not documento_id:
        return
    from facturacion.models import PagoFactura

    if PagoFactura.objects.using(using).filter(pk=documento_id, anulado=True).exists():
        raise ValidationError('El cobro está anulado. Su asiento original y su reversión conservan sus partidas sin modificaciones.')


def validar_asiento_cobro_anulado(asiento, using='default'):
    validar_asiento_id_cobro_anulado(asiento.pk, using)
    validar_fuente_cobro_anulado(asiento.documento_tipo, asiento.documento_id, using)
