from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0062_activar_permisos_acciones_v3")]

    operations = [
        migrations.AddField(
            model_name="usuarioempresapermiso",
            name="permisos_personalizados",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
