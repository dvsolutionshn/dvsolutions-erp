from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clinica", "0038_preconsulta_cita_trazabilidad"),
    ]

    operations = [
        migrations.AddField(
            model_name="citaclinica",
            name="virtual",
            field=models.BooleanField(default=False, db_default=False, verbose_name="Cita virtual"),
        ),
    ]
