from django.core.management.base import BaseCommand

from crm.appointment_notifications import procesar_recordatorios_hospital_mia


class Command(BaseCommand):
    help = "Procesa confirmaciones, recordatorios WhatsApp de citas y saludos de cumpleaños."

    def handle(self, *args, **options):
        resultado = procesar_recordatorios_hospital_mia()
        self.stdout.write(self.style.SUCCESS(
            f"Citas procesadas: {resultado['procesadas']} · Enviadas: {resultado['enviadas']} · Errores: {resultado['errores']} | "
            f"Cumpleaños procesados: {resultado['cumpleanos_procesadas']} · Enviados: {resultado['cumpleanos_enviadas']} · Errores: {resultado['cumpleanos_errores']}"
        ))
