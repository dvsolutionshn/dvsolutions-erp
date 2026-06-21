from django.db import migrations, models


def clasificar_empresas_existentes(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    for empresa in Empresa.objects.all().iterator():
        codigos = set(
            EmpresaModulo.objects.filter(empresa=empresa, activo=True)
            .values_list("modulo__codigo", flat=True)
        )
        if "tecnicentro" in codigos:
            tipo = "tecnicentro"
        elif "clinica_medica" in codigos:
            tipo = "clinica"
        else:
            tipo = "erp"
        Empresa.objects.filter(pk=empresa.pk).update(tipo_solucion=tipo)


class Migration(migrations.Migration):
    dependencies = [("core", "0031_tecnicentro_permisos_y_modulo")]

    operations = [
        migrations.AddField(
            model_name="empresa",
            name="tipo_solucion",
            field=models.CharField(
                choices=[
                    ("erp", "ERP Empresarial"),
                    ("clinica", "Clinica y Centro Medico"),
                    ("tecnicentro", "Tecnicentro Vehicular"),
                ],
                db_index=True,
                default="erp",
                max_length=20,
            ),
        ),
        migrations.RunPython(clasificar_empresas_existentes, migrations.RunPython.noop),
    ]
