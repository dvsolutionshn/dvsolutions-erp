from collections import defaultdict

from django.core.management.base import BaseCommand

from facturacion.models import Cliente
from facturacion.services_clientes_compartidos import (
    EMPRESAS_CLIENTES_COMPARTIDOS,
    sincronizar_cliente_compartido,
)


def _normalizar(valor):
    return "".join(
        caracter.casefold()
        for caracter in str(valor or "").strip()
        if caracter.isalnum()
    )


class Command(BaseCommand):
    help = "Audita y sincroniza las fichas generales de clientes de las empresas medicas compartidas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Crea o actualiza las fichas faltantes. Nunca elimina clientes.",
        )

    def handle(self, *args, **options):
        clientes = list(
            Cliente.objects.filter(empresa__slug__in=EMPRESAS_CLIENTES_COMPARTIDOS)
            .exclude(nombre__iexact="Consumidor Final")
            .select_related("empresa")
            .order_by("empresa__slug", "id")
        )
        por_rtn = defaultdict(list)
        por_telefono = defaultdict(list)
        por_nombre = defaultdict(list)
        for cliente in clientes:
            por_rtn[_normalizar(cliente.rtn)].append(cliente)
            por_telefono[_normalizar(cliente.telefono)].append(cliente)
            por_nombre[_normalizar(cliente.nombre)].append(cliente)

        self.stdout.write(f"Clientes revisados: {len(clientes)}")
        self._mostrar_coincidencias("Identidad/RTN", por_rtn)
        self._mostrar_coincidencias("Telefono", por_telefono)
        self._mostrar_coincidencias("Nombre", por_nombre)

        if not options["aplicar"]:
            self.stdout.write(self.style.WARNING(
                "Modo auditoria: no se modifico ningun registro. Usa --aplicar para sincronizar."
            ))
            return

        creados = actualizados = 0
        conflictos = []
        perfiles_procesados = set()
        for cliente in clientes:
            cliente.refresh_from_db()
            if cliente.perfil_compartido_id in perfiles_procesados:
                continue
            resultado = sincronizar_cliente_compartido(cliente)
            perfiles_procesados.add(cliente.perfil_compartido_id)
            creados += resultado["creados"]
            actualizados += resultado["actualizados"]
            conflictos.extend(resultado["conflictos"])

        self.stdout.write(self.style.SUCCESS(
            f"Sincronizacion terminada: {creados} creados, {actualizados} actualizados."
        ))
        if conflictos:
            self.stdout.write(self.style.WARNING(
                f"Conflictos omitidos sin borrar datos: {len(conflictos)}"
            ))
            for conflicto in conflictos:
                self.stdout.write(
                    f"- {conflicto['empresa']} cliente #{conflicto['cliente_id']}: {conflicto['motivo']}"
                )

        from clinica.services_pacientes import asegurar_paciente_desde_cliente

        pacientes_creados = 0
        clientes_compartidos = Cliente.objects.filter(
            empresa__slug__in=EMPRESAS_CLIENTES_COMPARTIDOS,
        ).exclude(nombre__iexact="Consumidor Final")
        for cliente in clientes_compartidos.select_related("empresa"):
            _paciente, creado = asegurar_paciente_desde_cliente(cliente)
            pacientes_creados += int(creado)
        self.stdout.write(self.style.SUCCESS(
            f"Empresas compartidas: {pacientes_creados} pacientes creados desde clientes existentes."
        ))

    def _mostrar_coincidencias(self, etiqueta, grupos):
        coincidencias = [items for clave, items in grupos.items() if clave and len(items) > 1]
        self.stdout.write(f"Coincidencias por {etiqueta}: {len(coincidencias)}")
        for items in coincidencias:
            detalle = ", ".join(
                f"{cliente.empresa.slug} #{cliente.id} {cliente.nombre}"
                for cliente in items
            )
            self.stdout.write(f"- {detalle}")
