from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0023_recordatorio_tres_dias"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_numero_conectado",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]
