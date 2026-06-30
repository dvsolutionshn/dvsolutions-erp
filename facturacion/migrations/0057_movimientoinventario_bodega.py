from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0056_cliente_perfil_compartido_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientoinventario",
            name="bodega",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movimientos_generales",
                to="facturacion.bodegainventario",
            ),
        ),
    ]
