from django.db import migrations


EMPRESAS_CON_GASTOS_ADICIONALES = {
    "hospital_mia", "medical_spa", "serviciosmedicos", "luque_aestetic",
}


def habilitar_gastos_adicionales(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    modulo, _ = Modulo.objects.update_or_create(
        codigo="gastos_adicionales",
        defaults={"nombre": "Gastos Adicionales", "es_comercial": True},
    )
    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_CON_GASTOS_ADICIONALES):
        EmpresaModulo.objects.update_or_create(
            empresa=empresa, modulo=modulo, defaults={"activo": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0054_permisos_gastos_adicionales"),
    ]

    operations = [
        migrations.RunPython(habilitar_gastos_adicionales, migrations.RunPython.noop),
    ]
