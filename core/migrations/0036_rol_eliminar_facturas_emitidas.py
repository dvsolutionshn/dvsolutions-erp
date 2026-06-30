from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_normalizar_rol_facturacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_eliminar_facturas",
            field=models.BooleanField(default=False),
        ),
    ]
