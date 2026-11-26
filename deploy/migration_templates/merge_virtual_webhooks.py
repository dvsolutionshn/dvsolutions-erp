from django.db import migrations


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ("crm", "0027_citacliente_virtual"),
        ("crm", "0027_whatsapp_webhooks"),
    ]
    operations = []
