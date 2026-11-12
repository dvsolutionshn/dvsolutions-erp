from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0063_usuarioempresapermiso_permisos_personalizados")]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_generar_enlace_paciente_nuevo",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_enviar_enlace_historia_clinica",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
