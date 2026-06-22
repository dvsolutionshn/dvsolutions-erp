from django.core.management.base import BaseCommand

from crm.appointment_notifications import procesar_recordatorios_hospital_mia


class Command(BaseCommand):
    help = "Procesa confirmaciones y recordatorios WhatsApp de citas para hospital_mia."

    def handle(self, *args, **options):
        resultado = procesar_recordatorios_hospital_mia()
        self.stdout.write(self.style.SUCCESS(
            f"Procesadas: {resultado['procesadas']} · Enviadas: {resultado['enviadas']} · Errores: {resultado['errores']}"
        ))
