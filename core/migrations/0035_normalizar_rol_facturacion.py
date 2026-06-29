from django.db import migrations
from django.db.models import Q


def normalizar_rol_facturacion(apps, schema_editor):
    RolSistema = apps.get_model("core", "RolSistema")
    roles = RolSistema.objects.filter(
        Q(codigo__in=["facturacion", "facturador"])
        | Q(nombre__iexact="Facturacion")
        | Q(nombre__iexact="Facturación")
        | Q(nombre__iexact="Facturador")
    )
    roles.update(
        activo=True,
        puede_punto_venta=True,
        puede_cierres_caja=True,
        puede_facturas=True,
        puede_clientes=True,
        puede_productos=True,
        puede_recibos=True,
        puede_crear_facturas=True,
        puede_editar_facturas=True,
        puede_registrar_pagos_clientes=True,
        puede_crear_clientes=True,
        puede_editar_clientes=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_usuario_empresas_acceso"),
    ]

    operations = [
        migrations.RunPython(
            normalizar_rol_facturacion,
            migrations.RunPython.noop,
        ),
    ]
