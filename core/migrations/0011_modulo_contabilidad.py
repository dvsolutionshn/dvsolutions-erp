from django.db import migrations


def crear_modulo_contabilidad(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.update_or_create(
        codigo="contabilidad",
        defaults={
            "nombre": "Contabilidad",
            "es_comercial": True,
        },
    )


def eliminar_modulo_contabilidad(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.filter(codigo="contabilidad").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_rolsistema_puede_catalogo_cuentas_and_more"),
    ]

    operations = [
        migrations.RunPython(crear_modulo_contabilidad, eliminar_modulo_contabilidad),
    ]
