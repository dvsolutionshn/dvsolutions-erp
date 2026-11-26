from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0026_bloqueo_disponibilidad_fecha_fin"),
    ]

    operations = [
        migrations.AddField(
            model_name="citacliente",
            name="virtual",
            field=models.BooleanField(default=False, db_default=False, verbose_name="Cita virtual"),
        ),
    ]
