from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0053_configurar_manuales_pdf_clinicas"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolsistema",
            name="puede_ver_gastos_adicionales",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_crear_gastos_adicionales",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_editar_gastos_adicionales",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_enviar_gastos_adicionales",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="rolsistema",
            name="puede_convertir_gastos_adicionales_factura",
            field=models.BooleanField(default=False),
        ),
    ]
