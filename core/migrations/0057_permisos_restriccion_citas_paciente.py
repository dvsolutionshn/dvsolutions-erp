from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0056_seguridad_envio_invitaciones")]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_restringir_citas_paciente",
            field=models.BooleanField(default=False, verbose_name="Puede marcar paciente como No Agendar"),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_habilitar_citas_paciente",
            field=models.BooleanField(default=False, verbose_name="Puede quitar restricción de citas"),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_ver_motivo_restriccion_paciente",
            field=models.BooleanField(default=False, verbose_name="Puede ver motivo de restricción"),
        ),
    ]
