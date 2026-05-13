from django.db import migrations


def crear_modulo_citas(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")

    modulo, _ = Modulo.objects.get_or_create(
        codigo="agenda_citas",
        defaults={"nombre": "Citas", "es_comercial": True},
    )
    for empresa in Empresa.objects.filter(slug="hospital_mia"):
        EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_crm_permissions_and_module"),
    ]

    operations = [
        migrations.RunPython(crear_modulo_citas, migrations.RunPython.noop),
    ]
