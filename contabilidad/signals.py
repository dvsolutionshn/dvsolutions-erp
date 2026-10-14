"""Conserva el historial cerrado también al operar desde facturación y nómina."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from .models import (
    AsientoContable, LineaAsientoContable, MovimientoBancario, PeriodoContable,
    bloquear_empresas_contables, validar_fecha_contable_abierta,
)


@receiver(pre_delete, sender=AsientoContable)
@receiver(pre_delete, sender=LineaAsientoContable)
@receiver(pre_delete, sender=MovimientoBancario)
@receiver(pre_delete, sender=PeriodoContable)
def proteger_eliminacion_contable(sender, instance, using, **kwargs):
    # El recolector de Django no llama al método delete() durante las cascadas.
    instance.validar_eliminacion(using=using)


DOCUMENTOS_CONTABLES = {
    "Factura": ("factura", "empresa_id"),
    "NotaCredito": ("nota_credito", "empresa_id"),
    "CompraInventario": ("compra", "empresa_id"),
    "PagoFactura": ("pago_factura", "factura__empresa_id"),
    "PagoCompra": ("pago_compra", "compra__empresa_id"),
    "PagoComisionProveedor": ("pago_comision_proveedor", "comision_origen__factura__empresa_id"),
    "PeriodoPlanilla": ("planilla", "empresa_id"),
}


def _validar_documento_sin_asientos_cerrados(sender, instance, using):
    tipo, campo_empresa = DOCUMENTOS_CONTABLES[sender.__name__]
    empresa_id = sender.objects.using(using).filter(pk=instance.pk).values_list(campo_empresa, flat=True).first()
    if empresa_id is None:
        return
    bloquear_empresas_contables([empresa_id], using=using)
    for asiento in AsientoContable.objects.using(using).filter(
        empresa_id=empresa_id, documento_tipo=tipo, documento_id=instance.pk
    ).values("empresa_id", "fecha"):
        validar_fecha_contable_abierta(asiento["empresa_id"], asiento["fecha"], using)


def proteger_eliminacion_documento(sender, instance, using, **kwargs):
    _validar_documento_sin_asientos_cerrados(sender, instance, using)


CAMPOS_ECONOMICOS_DOCUMENTO = {
    "empresa_id", "cliente_id", "factura_origen_id", "moneda", "tipo_cambio", "fecha_emision",
    "subtotal", "impuesto", "total", "total_lempiras", "estado", "numero_factura", "numero_nota",
    "cai_id", "cai_numero", "cai_establecimiento", "cai_punto_emision", "cai_tipo_documento",
    "cai_rango_inicial", "cai_rango_final", "cai_fecha_limite", "orden_compra_exenta",
    "registro_exonerado", "registro_sag",
}


def proteger_edicion_documento(sender, instance, using, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    campos = {
        campo.attname for campo in sender._meta.concrete_fields
        if campo.attname in CAMPOS_ECONOMICOS_DOCUMENTO
        and (update_fields is None or campo.name in update_fields or campo.attname in update_fields)
    }
    if not campos:
        # Cobrar en un mes abierto puede actualizar el estado de pago de una factura histórica.
        return
    anterior = sender.objects.using(using).filter(pk=instance.pk).values(
        "empresa_id", "fecha_emision", "estado", *campos
    ).first() if instance.pk else None
    if anterior and not any(anterior[campo] != getattr(instance, campo) for campo in campos):
        return
    with transaction.atomic(using=using):
        bloquear_empresas_contables(
            [instance.empresa_id, anterior["empresa_id"] if anterior else None], using=using
        )
        if anterior:
            _validar_documento_sin_asientos_cerrados(sender, instance, using)
            if anterior["estado"] == "emitida":
                validar_fecha_contable_abierta(anterior["empresa_id"], anterior["fecha_emision"], using)
            if anterior["empresa_id"] != instance.empresa_id:
                raise ValidationError("No se puede trasladar un documento contable a otra empresa.")
        if instance.estado == "emitida":
            validar_fecha_contable_abierta(instance.empresa_id, instance.fecha_emision, using)


LINEAS_DOCUMENTO = {
    "LineaFactura": ("factura", "factura"),
    "LineaNotaCredito": ("nota_credito", "nota_credito"),
}


CAMPOS_PAGO = {
    "fecha", "monto", "factura_id", "compra_id", "comision_origen_id", "metodo",
    "cuenta_financiera_id", "cuenta_financiera_impuesto_id", "retencion_isr", "retencion_isv",
    "porcentaje_comision", "monto_comision", "proveedor_comision_id", "separar_isv",
    "subtotal_aplicado", "impuesto_aplicado",
}


def proteger_edicion_pago(sender, instance, using, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    campos = {
        campo.attname for campo in sender._meta.concrete_fields
        if campo.attname in CAMPOS_PAGO
        and (update_fields is None or campo.name in update_fields or campo.attname in update_fields)
    }
    if not campos:
        return
    anterior = sender.objects.using(using).filter(pk=instance.pk).values("fecha", *campos).first() if instance.pk else None
    if anterior and not any(anterior[campo] != getattr(instance, campo) for campo in campos):
        return
    tipo, campo_empresa = DOCUMENTOS_CONTABLES[sender.__name__]
    empresa_id = {
        "pago_factura": lambda: instance.factura.empresa_id,
        "pago_compra": lambda: instance.compra.empresa_id,
        "pago_comision_proveedor": lambda: instance.comision_origen.factura.empresa_id,
    }[tipo]()
    with transaction.atomic(using=using):
        bloquear_empresas_contables([empresa_id], using=using)
        if anterior:
            _validar_documento_sin_asientos_cerrados(sender, instance, using)
            anterior_empresa = sender.objects.using(using).filter(pk=instance.pk).values_list(
                campo_empresa, flat=True
            ).first()
            if anterior_empresa != empresa_id:
                raise ValidationError("No se puede trasladar un pago a otra empresa.")
            validar_fecha_contable_abierta(anterior_empresa, anterior.get("fecha", instance.fecha), using)
        validar_fecha_contable_abierta(empresa_id, instance.fecha, using)


def proteger_linea_documento(sender, instance, using, raw=False, **kwargs):
    if raw:
        return
    campo, _tipo = LINEAS_DOCUMENTO[sender.__name__]
    anterior_id = sender.objects.using(using).filter(pk=instance.pk).values_list(
        f"{campo}_id", flat=True
    ).first() if instance.pk else None
    modelo_documento = sender._meta.get_field(campo).remote_field.model
    ids = {pk for pk in (anterior_id, getattr(instance, f"{campo}_id")) if pk}
    with transaction.atomic(using=using):
        documentos = list(modelo_documento.objects.using(using).filter(pk__in=ids))
        if len({documento.empresa_id for documento in documentos}) > 1:
            raise ValidationError("No se puede trasladar una línea de documento a otra empresa.")
        bloquear_empresas_contables([documento.empresa_id for documento in documentos], using=using)
        for documento in documentos:
            _validar_documento_sin_asientos_cerrados(modelo_documento, documento, using)
            if documento.estado == "emitida":
                validar_fecha_contable_abierta(documento.empresa_id, documento.fecha_emision, using)


def registrar_protecciones_documentos():
    from facturacion import models as facturacion
    from rrhh.models import PeriodoPlanilla

    for nombre in DOCUMENTOS_CONTABLES:
        modelo = PeriodoPlanilla if nombre == "PeriodoPlanilla" else getattr(facturacion, nombre)
        pre_delete.connect(
            proteger_eliminacion_documento, sender=modelo,
            dispatch_uid=f"contabilidad.proteger_eliminacion.{nombre}",
        )
    for nombre in ("Factura", "NotaCredito"):
        pre_save.connect(
            proteger_edicion_documento, sender=getattr(facturacion, nombre),
            dispatch_uid=f"contabilidad.proteger_edicion.{nombre}",
        )
    for nombre in ("PagoFactura", "PagoCompra", "PagoComisionProveedor"):
        pre_save.connect(
            proteger_edicion_pago, sender=getattr(facturacion, nombre),
            dispatch_uid=f"contabilidad.proteger_edicion.{nombre}",
        )
    for nombre in LINEAS_DOCUMENTO:
        for nombre_senal, senal in (("guardar", pre_save), ("eliminar", pre_delete)):
            senal.connect(
                proteger_linea_documento, sender=getattr(facturacion, nombre),
                dispatch_uid=f"contabilidad.proteger_linea.{nombre_senal}.{nombre}",
            )


registrar_protecciones_documentos()
