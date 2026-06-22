from django.db import migrations


def restaurar_perfiles_clinicos(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")

    ids_con_clinica = EmpresaModulo.objects.filter(
        activo=True,
        modulo__codigo="clinica_medica",
    ).values_list("empresa_id", flat=True)

    Empresa.objects.filter(id__in=ids_con_clinica).update(tipo_solucion="clinica")
    Empresa.objects.filter(slug__in=["hospital_mia", "medical_spa"]).update(tipo_solucion="clinica")


class Migration(migrations.Migration):
    dependencies = [("core", "0032_empresa_tipo_solucion")]

    operations = [
        migrations.RunPython(restaurar_perfiles_clinicos, migrations.RunPython.noop),
    ]
