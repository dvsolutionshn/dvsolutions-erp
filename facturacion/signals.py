from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Cliente
from .services_clientes_compartidos import sincronizar_cliente_compartido


@receiver(post_save, sender=Cliente)
def sincronizar_ficha_general_cliente(sender, instance, raw=False, **kwargs):
    if not raw:
        sincronizar_cliente_compartido(instance)
