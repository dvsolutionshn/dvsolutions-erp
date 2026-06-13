from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_tokenaccesousuario"),
        ("facturacion", "0046_bodegas_hospital_mia_medical_spa"),
    ]

    operations = [
        migrations.CreateModel(
            name="CorreccionNumeroFactura",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero_anterior", models.CharField(max_length=20)),
                ("numero_nuevo", models.CharField(max_length=20)),
                ("cai_anterior", models.CharField(blank=True, max_length=50, null=True)),
                ("cai_nuevo", models.CharField(blank=True, max_length=50, null=True)),
                ("motivo", models.TextField()),
                ("fecha", models.DateTimeField(auto_now_add=True)),
                (
                    "empresa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="correcciones_numero_factura",
                        to="core.empresa",
                    ),
                ),
                (
                    "factura",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="correcciones_numero",
                        to="facturacion.factura",
                    ),
                ),
                (
                    "realizado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="correcciones_numero_factura",
                        to="core.usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "Correccion de numero de factura",
                "verbose_name_plural": "Correcciones de numero de factura",
                "ordering": ["-fecha"],
            },
        ),
    ]
