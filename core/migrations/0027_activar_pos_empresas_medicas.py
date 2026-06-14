from django.db import migrations


def activar_pos_empresas_medicas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    Modulo = apps.get_model("core", "Modulo")

    modulo, _ = Modulo.objects.get_or_create(
        codigo="punto_venta",
        defaults={
            "nombre": "Punto de Venta",
            "es_comercial": True,
        },
    )

    for empresa in Empresa.objects.filter(slug__in=["hospital_mia", "medical_spa"]):
        EmpresaModulo.objects.update_or_create(
            empresa=empresa,
            modulo=modulo,
            defaults={"activo": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_tokenaccesousuario"),
    ]

    operations = [
        migrations.RunPython(activar_pos_empresas_medicas, migrations.RunPython.noop),
    ]
