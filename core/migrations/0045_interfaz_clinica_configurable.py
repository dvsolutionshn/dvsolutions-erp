from django.db import migrations, models


EMPRESAS_CLINICAS_INICIALES = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}


def configurar_perfiles_clinicos(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    Modulo = apps.get_model("core", "Modulo")
    Configuracion = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")

    empresas = Empresa.objects.filter(slug__in=EMPRESAS_CLINICAS_INICIALES)
    empresas.update(tipo_solucion="clinica")

    hospital = empresas.filter(slug="hospital_mia").first()
    crm = Modulo.objects.filter(codigo="crm_marketing").first()
    if hospital and crm:
        configuracion, _ = Configuracion.objects.get_or_create(empresa=hospital)
        configuracion.modulos_adicionales_visibles_clinica.add(crm)


class Migration(migrations.Migration):
    dependencies = [("core", "0044_acciones_onix")]

    operations = [
        migrations.AddField(
            model_name="configuracionavanzadaempresa",
            name="modulos_adicionales_visibles_clinica",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Modulos ERP que tambien deben aparecer en la navegacion limpia de una "
                    "empresa con perfil clinico. No activa ni desactiva modulos."
                ),
                related_name="configuraciones_clinicas_visibles",
                to="core.modulo",
            ),
        ),
        migrations.RunPython(configurar_perfiles_clinicos, migrations.RunPython.noop),
    ]
