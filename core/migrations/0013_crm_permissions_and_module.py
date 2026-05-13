from django.db import migrations, models


def crear_modulo_crm(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")

    modulo, _ = Modulo.objects.get_or_create(
        codigo="crm_marketing",
        defaults={"nombre": "CRM, Marketing y Agenda", "es_comercial": True},
    )
    for empresa in Empresa.objects.filter(slug="hospital_mia"):
        EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_rolsistema_puede_configuracion_rrhh_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_campanias",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_citas",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_configuracion_crm",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_crm",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(crear_modulo_crm, migrations.RunPython.noop),
    ]
