from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Empresa
from clinica.models import Paciente
from clinica.views import _sincronizar_cliente_facturacion_paciente


class Command(BaseCommand):
    help = "Sincroniza pacientes clinicos con clientes de facturacion cuando falte el cliente asociado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa",
            default="hospital_mia",
            help="Slug de la empresa a sincronizar. Por defecto: hospital_mia.",
        )
        parser.add_argument(
            "--nombre",
            action="append",
            default=[],
            help="Filtra por nombre o apellido. Puede repetirse.",
        )
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Aplica los cambios. Sin esta bandera solo muestra lo que haria.",
        )

    def handle(self, *args, **options):
        empresa = Empresa.objects.get(slug=options["empresa"])
        pacientes = Paciente.objects.filter(empresa=empresa).order_by("nombre", "id")
        nombres = [nombre.strip() for nombre in options["nombre"] if nombre and nombre.strip()]
        if nombres:
            filtro_nombres = Q()
            for nombre in nombres:
                filtro_nombres |= Q(nombre__icontains=nombre)
            pacientes = pacientes.filter(filtro_nombres)

        revisados = pacientes.count()
        pendientes = pacientes.filter(cliente__isnull=True)
        if nombres:
            pendientes = pacientes

        creados_o_vinculados = 0
        self.stdout.write(f"Empresa: {empresa.nombre} ({empresa.slug})")
        self.stdout.write(f"Pacientes revisados: {revisados}")

        for paciente in pendientes:
            cliente_actual = paciente.cliente_id
            if options["aplicar"]:
                cliente = _sincronizar_cliente_facturacion_paciente(paciente)
                paciente.refresh_from_db(fields=["cliente"])
                if paciente.cliente_id and paciente.cliente_id != cliente_actual:
                    creados_o_vinculados += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK {paciente.id} · {paciente.nombre} -> cliente {cliente.id}"
                    )
                )
            else:
                self.stdout.write(
                    f"PENDIENTE {paciente.id} · {paciente.nombre} · identidad {paciente.identidad or '-'} · cliente {cliente_actual or '-'}"
                )

        if options["aplicar"]:
            self.stdout.write(self.style.SUCCESS(f"Pacientes sincronizados: {creados_o_vinculados}"))
        else:
            self.stdout.write(self.style.WARNING("Vista previa solamente. Use --aplicar para guardar cambios."))
