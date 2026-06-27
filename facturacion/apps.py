from django.apps import AppConfig


class FacturacionConfig(AppConfig):
    name = 'facturacion'

    def ready(self):
        from . import signals  # noqa: F401
