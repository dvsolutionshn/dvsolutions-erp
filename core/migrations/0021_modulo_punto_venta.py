from django.db import migrations


def crear_modulo_punto_venta(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.update_or_create(
        codigo="punto_venta",
        defaults={
            "nombre": "Punto de Venta",
            "es_comercial": True,
        },
    )


def eliminar_modulo_punto_venta(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.filter(codigo="punto_venta").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_solicitudcomercial_rtn_empresa"),
    ]

    operations = [
        migrations.RunPython(crear_modulo_punto_venta, eliminar_modulo_punto_venta),
    ]
