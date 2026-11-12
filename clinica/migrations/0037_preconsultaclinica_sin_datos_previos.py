from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clinica", "0036_paciente_no_agendar"),
    ]

    operations = [
        migrations.AddField(
            model_name="preconsultaclinica",
            name="sin_datos_previos",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
