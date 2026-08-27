from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0047_alter_acciononix_tipo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="acciononix",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("crear_cliente", "Crear cliente"),
                    ("crear_producto", "Crear producto o servicio"),
                    ("crear_borrador_factura", "Crear borrador de factura"),
                    ("emitir_factura", "Validar y emitir factura"),
                ],
                max_length=50,
            ),
        ),
    ]
