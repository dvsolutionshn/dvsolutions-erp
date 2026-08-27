from django.db import migrations, models


EMPRESAS_CLINICAS_INICIALES = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}


def conservar_control_fefo_actual(apps, schema_editor):
    Configuracion = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")
    Configuracion.objects.filter(
        empresa__slug__in=EMPRESAS_CLINICAS_INICIALES,
    ).update(usa_control_lotes_fefo=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0045_interfaz_clinica_configurable")]

    operations = [
        migrations.AddField(
            model_name="configuracionavanzadaempresa",
            name="usa_control_lotes_fefo",
            field=models.BooleanField(
                default=False,
                help_text="Muestra el control de lotes, vencimientos y salida por FEFO en Inventario.",
            ),
        ),
        migrations.RunPython(conservar_control_fefo_actual, migrations.RunPython.noop),
    ]
