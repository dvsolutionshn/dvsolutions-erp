from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0064_permisos_enlaces_pacientes")]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_buscar_pacientes_por_identidad",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
