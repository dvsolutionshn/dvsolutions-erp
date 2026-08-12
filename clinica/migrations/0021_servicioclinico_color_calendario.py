from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clinica", "0020_desactivar_cita_con_nosotros"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicioclinico",
            name="color_calendario",
            field=models.CharField(blank=True, default="", max_length=7),
        ),
    ]
