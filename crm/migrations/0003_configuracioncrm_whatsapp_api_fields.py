from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0002_plantillamensaje_imagen_promocional"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_api_version",
            field=models.CharField(default="v25.0", max_length=20),
        ),
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_idioma_plantilla",
            field=models.CharField(default="en_US", max_length=12),
        ),
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_numero_prueba",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name="configuracioncrm",
            name="whatsapp_plantilla_prueba",
            field=models.CharField(default="hello_world", max_length=80),
        ),
    ]
