from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Cliente, PagoComisionProveedor, PagoFactura, ReciboPago
from .services_clientes_compartidos import sincronizar_cliente_compartido

EMPRESAS_CLIENTES_COMO_PACIENTES = frozenset({
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
})


@receiver(pre_delete, sender=PagoFactura)
@receiver(pre_delete, sender=ReciboPago)
@receiver(pre_delete, sender=PagoComisionProveedor)
def conservar_documentos_cobro_anulado(sender, instance, **kwargs):
    if sender is PagoFactura:
        anulado = PagoFactura.objects.filter(pk=instance.pk, anulado=True).exists()
    else:
        pago_id = instance.pago_id if sender is ReciboPago else instance.comision_origen_id
        anulado = PagoFactura.objects.filter(pk=pago_id, anulado=True).exists()
    if anulado:
        raise ValidationError('El pago anulado, su recibo y su compensación conservan el respaldo de la reversión y no se eliminan.')


@receiver(post_save, sender=Cliente)
def sincronizar_ficha_general_cliente(sender, instance, raw=False, **kwargs):
    if not raw:
        sincronizar_cliente_compartido(instance)


@receiver(post_save, sender=Cliente)
def asegurar_cliente_como_paciente_hospital_mia(sender, instance, raw=False, **kwargs):
    if raw or instance.empresa.slug not in EMPRESAS_CLIENTES_COMO_PACIENTES:
        return
    from clinica.services_pacientes import asegurar_paciente_desde_cliente

    asegurar_paciente_desde_cliente(instance)
