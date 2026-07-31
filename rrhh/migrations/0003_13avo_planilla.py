from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ("rrhh", "0002_periodoplanilla_cuenta_financiera_pago"),
    ]

    operations = [
        migrations.AddField(
            model_name="periodoplanilla",
            name="incluir_13avo",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="detalleplanilla",
            name="decimo_tercero",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
    ]
