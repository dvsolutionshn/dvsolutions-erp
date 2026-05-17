from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contabilidad", "0016_asientocontable_unique_asiento_numero_por_empresa"),
        ("facturacion", "0040_pagofactura_retenciones_y_desglose"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagofactura",
            name="cuenta_financiera_impuesto",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pagos_facturas_impuesto", to="contabilidad.cuentafinanciera"),
        ),
        migrations.AddField(
            model_name="pagofactura",
            name="separar_isv",
            field=models.BooleanField(default=False),
        ),
    ]
