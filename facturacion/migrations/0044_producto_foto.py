from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0043_recibopago_concepto"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="foto",
            field=models.ImageField(blank=True, null=True, upload_to="productos/fotos/"),
        ),
    ]
