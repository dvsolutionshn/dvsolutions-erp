from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0065_rolsistema_buscar_pacientes_por_identidad")]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="solo_tarjetas_pacientes",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
