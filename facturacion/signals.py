from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Cliente
from .services_clientes_compartidos import sincronizar_cliente_compartido

EMPRESAS_CLIENTES_COMO_PACIENTES = frozenset({
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
})


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
