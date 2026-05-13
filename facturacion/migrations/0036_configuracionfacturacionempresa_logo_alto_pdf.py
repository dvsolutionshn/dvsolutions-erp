from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0035_configuracionfacturacionempresa_logo_ancho_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionfacturacionempresa",
            name="logo_alto_pdf",
            field=models.PositiveIntegerField(
                default=60,
                validators=[
                    django.core.validators.MinValueValidator(30),
                    django.core.validators.MaxValueValidator(160),
                ],
            ),
        ),
    ]
