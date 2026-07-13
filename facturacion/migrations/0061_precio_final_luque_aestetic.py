from django.db import migrations


def activar_precio_final_luque_aestetic(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ConfiguracionFacturacionEmpresa = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")
    empresa = Empresa.objects.filter(slug="luque_aestetic").first()
    if not empresa:
        return
    configuracion, _ = ConfiguracionFacturacionEmpresa.objects.get_or_create(empresa=empresa)
    if not configuracion.precios_incluyen_impuesto:
        configuracion.precios_incluyen_impuesto = True
        configuracion.save(update_fields=["precios_incluyen_impuesto"])


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0060_promocionpuntoventa_productopromocionpuntoventa"),
    ]

    operations = [
        migrations.RunPython(activar_precio_final_luque_aestetic, migrations.RunPython.noop),
    ]
