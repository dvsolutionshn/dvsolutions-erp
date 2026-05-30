from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contabilidad", "0016_asientocontable_unique_asiento_numero_por_empresa"),
        ("facturacion", "0041_pagofactura_separar_isv"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="cuenta_contable",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clientes_cxc",
                to="contabilidad.cuentacontable",
            ),
        ),
        migrations.AddField(
            model_name="proveedor",
            name="cuenta_contable",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="proveedores_cxp",
                to="contabilidad.cuentacontable",
            ),
        ),
    ]