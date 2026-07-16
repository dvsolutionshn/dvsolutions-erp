from django.db import migrations


def crear_modulo_cotizaciones(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")

    modulo, _ = Modulo.objects.update_or_create(
        codigo="cotizaciones",
        defaults={
            "nombre": "Cotizaciones",
            "es_comercial": True,
        },
    )

    for empresa in Empresa.objects.filter(slug__in=["iss"]):
        EmpresaModulo.objects.update_or_create(
            empresa=empresa,
            modulo=modulo,
            defaults={"activo": True},
        )


def revertir_modulo_cotizaciones(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.filter(codigo="cotizaciones").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_rol_eliminar_facturas_emitidas"),
    ]

    operations = [
        migrations.RunPython(crear_modulo_cotizaciones, revertir_modulo_cotizaciones),
    ]
