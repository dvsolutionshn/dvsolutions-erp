from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0029_pagocompra_cuenta_financiera"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="acepta_promociones",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="cliente",
            name="canal_preferido",
            field=models.CharField(
                choices=[("whatsapp", "WhatsApp"), ("correo", "Correo"), ("telefono", "Telefono")],
                default="whatsapp",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="cliente",
            name="correo",
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="fecha_nacimiento",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="telefono",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="telefono_whatsapp",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="fecha_alerta",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="fecha_referencia",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="nota_fecha",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
