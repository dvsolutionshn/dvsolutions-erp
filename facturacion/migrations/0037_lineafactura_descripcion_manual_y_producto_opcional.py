from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0036_configuracionfacturacionempresa_logo_alto_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="lineafactura",
            name="descripcion_manual",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="lineafactura",
            name="producto",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.PROTECT, to="facturacion.producto"),
        ),
    ]
