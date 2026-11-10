from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facturacion", "0083_anulacion_pago_factura"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="permitir_venta_lotes_vencidos",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
