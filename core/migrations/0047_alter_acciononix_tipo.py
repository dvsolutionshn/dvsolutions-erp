from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0046_configuracion_clinica_inventario_fefo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="acciononix",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("crear_borrador_factura", "Crear borrador de factura"),
                    ("emitir_factura", "Validar y emitir factura"),
                ],
                max_length=50,
            ),
        ),
    ]
