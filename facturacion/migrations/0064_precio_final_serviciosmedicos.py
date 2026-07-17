from django.db import migrations


def activar_precio_final_serviciosmedicos(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    Configuracion = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")
    empresa = Empresa.objects.filter(slug="serviciosmedicos").first()
    if not empresa:
        return
    configuracion, _ = Configuracion.objects.get_or_create(empresa=empresa)
    if not configuracion.precios_incluyen_impuesto:
        configuracion.precios_incluyen_impuesto = True
        configuracion.save(update_fields=["precios_incluyen_impuesto"])


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0063_cotizacion_factura_cotizacion_origen_lineacotizacion"),
    ]

    operations = [
        migrations.RunPython(activar_precio_final_serviciosmedicos, migrations.RunPython.noop),
    ]
