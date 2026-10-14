"""Exporta incidencias monetarias de una empresa; no registra ajustes ni modifica documentos."""
import csv
from contextlib import nullcontext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Empresa
from contabilidad.historicos_usd import ENCABEZADOS_CSV, iterar_diagnostico, valores_csv
from contabilidad.revision_conversion_forms import FiltrosHistoricosUSDForm


class Command(BaseCommand):
    help = "Diagnóstico de lectura de registros históricos USD de una sola empresa."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", required=True, help="Slug exacto de la empresa a revisar.")
        parser.add_argument("--desde", default="", help="Fecha inicial AAAA-MM-DD.")
        parser.add_argument("--hasta", default="", help="Fecha final AAAA-MM-DD.")
        parser.add_argument("--fuente", default="", help="factura, pago_factura, nota_credito o pago_comision_proveedor.")
        parser.add_argument("--estado", default="", help="pendiente, parcial, integral, inconsistente o sin_asiento.")
        parser.add_argument("--salida", help="Archivo CSV nuevo. Si se omite, escribe CSV en la terminal.")

    def handle(self, *args, **options):
        empresa = Empresa.objects.filter(slug=options["empresa"], activa=True).first()
        if not empresa:
            raise CommandError("No existe una empresa activa con ese identificador.")
        filtros = FiltrosHistoricosUSDForm({clave: options[clave] for clave in ("desde", "hasta", "fuente", "estado")})
        if not filtros.is_valid():
            raise CommandError("Filtros inválidos: " + "; ".join(str(error) for errores in filtros.errors.values() for error in errores))
        try:
            archivo = Path(options["salida"]).open("x", newline="", encoding="utf-8-sig") if options.get("salida") else None
            contexto = archivo if archivo else nullcontext(self.stdout)
            with contexto as destino:
                writer = csv.writer(destino)
                writer.writerow(ENCABEZADOS_CSV)
                cantidad = 0
                for fila in iterar_diagnostico(empresa, filtros.cleaned_data):
                    writer.writerow(valores_csv(empresa, fila))
                    cantidad += 1
        except OSError as error:
            raise CommandError("No se pudo crear el diagnóstico CSV: " + str(error))
        if options.get("salida"):
            self.stdout.write(self.style.SUCCESS(f"Diagnóstico de {empresa.slug}: {cantidad} registros. No se modificaron datos contables."))
