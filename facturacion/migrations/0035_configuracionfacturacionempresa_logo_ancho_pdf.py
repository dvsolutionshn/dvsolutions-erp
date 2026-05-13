from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0034_cai_fecha_activacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionfacturacionempresa",
            name="logo_ancho_pdf",
            field=models.PositiveIntegerField(
                default=110,
                validators=[
                    django.core.validators.MinValueValidator(40),
                    django.core.validators.MaxValueValidator(260),
                ],
            ),
        ),
    ]
