from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_modulo_clinica_medica"),
        ("facturacion", "0044_producto_foto"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="eliminado",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="producto",
            name="fecha_eliminacion",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="motivo_eliminacion",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="eliminado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="productos_eliminados",
                to="core.usuario",
            ),
        ),
        migrations.CreateModel(
            name="BitacoraProductoEliminado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("motivo", models.TextField()),
                ("nombre_producto", models.CharField(max_length=200)),
                ("codigo_producto", models.CharField(blank=True, max_length=50, null=True)),
                ("tipo_item", models.CharField(max_length=15)),
                ("precio", models.DecimalField(decimal_places=2, max_digits=12)),
                ("controlaba_inventario", models.BooleanField(default=False)),
                ("stock_al_momento", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("fecha_eliminacion", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "empresa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bitacora_productos_eliminados",
                        to="core.empresa",
                    ),
                ),
                (
                    "producto",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bitacora_eliminaciones",
                        to="facturacion.producto",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="core.usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "Bitacora de producto eliminado",
                "verbose_name_plural": "Bitacora de productos eliminados",
                "ordering": ["-fecha_eliminacion"],
            },
        ),
    ]
