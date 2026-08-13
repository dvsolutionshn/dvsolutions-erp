from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0067_comprainventario_cuenta_financiera_pago"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagofactura",
            name="monto_comision",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="porcentaje_comision",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="proveedor_comision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="comisiones_pagos_factura",
                to="facturacion.proveedor",
            ),
        ),
    ]
