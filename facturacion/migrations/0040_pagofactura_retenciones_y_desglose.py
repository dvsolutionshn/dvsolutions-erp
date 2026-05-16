from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0039_alter_configuracionfacturacionempresa_plantilla_factura_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagofactura",
            name="impuesto_aplicado",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="retencion_isr",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="retencion_isv",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="subtotal_aplicado",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
