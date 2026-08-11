from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_habilitar_daniel_permisos_clinicos"),
    ]

    operations = [
        migrations.AddField(
            model_name="usuario",
            name="fecha_retiro_control",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="usuario",
            name="motivo_retiro_control",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="usuario",
            name="retirado_control",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Oculta al usuario del control operativo sin eliminar su identidad ni el historial asociado a cierres de caja y otras operaciones.",
            ),
        ),
    ]
