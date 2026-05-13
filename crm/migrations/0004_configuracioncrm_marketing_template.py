from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0003_configuracioncrm_whatsapp_api_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_plantilla_marketing",
            field=models.CharField(default="promo_general_imagen", max_length=80),
        ),
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_idioma_marketing",
            field=models.CharField(default="es", max_length=12),
        ),
    ]
