from django.db import migrations, models


EMPRESAS_PRECIO_FINAL = {"hospital_mia", "medical_spa"}


def limitar_precios_finales(apps, schema_editor):
    Configuracion = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")
    Configuracion.objects.exclude(
        empresa__slug__in=EMPRESAS_PRECIO_FINAL
    ).update(precios_incluyen_impuesto=False)
    Configuracion.objects.filter(
        empresa__slug__in=EMPRESAS_PRECIO_FINAL
    ).update(precios_incluyen_impuesto=True)


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0052_lineafactura_costo_unitario_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="configuracionfacturacionempresa",
            name="precios_incluyen_impuesto",
            field=models.BooleanField(
                default=False,
                help_text="El precio del catalogo se interpreta como total final con impuesto incluido.",
            ),
        ),
        migrations.RunPython(limitar_precios_finales, migrations.RunPython.noop),
    ]
