from django.db import migrations, models


EMPRESAS_CON_MANUALES_PDF = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}


def habilitar_manuales_pdf_clinicas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    Configuracion = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")
    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_CON_MANUALES_PDF):
        configuracion, _ = Configuracion.objects.get_or_create(empresa=empresa)
        if not configuracion.manuales_pdf_habilitados:
            configuracion.manuales_pdf_habilitados = True
            configuracion.save(update_fields=["manuales_pdf_habilitados"])


def conservar_configuracion(apps, schema_editor):
    # No se revierte una configuracion que pudo modificarse despues del despliegue.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0052_enfermera_manuales_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionavanzadaempresa",
            name="manuales_pdf_habilitados",
            field=models.BooleanField(
                default=False,
                help_text="Muestra la biblioteca operativa de Manuales PDF en el sistema clínico.",
            ),
        ),
        migrations.RunPython(habilitar_manuales_pdf_clinicas, conservar_configuracion),
    ]
