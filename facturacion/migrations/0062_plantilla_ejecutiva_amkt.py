from django.db import migrations, models


def configurar_plantilla_amkt(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ConfiguracionFacturacionEmpresa = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")

    empresas_amkt = [
        empresa
        for empresa in Empresa.objects.all()
        if "amkt" in f"{empresa.slug or ''} {empresa.nombre or ''}".lower()
    ]
    for empresa in empresas_amkt:
        configuracion, _ = ConfiguracionFacturacionEmpresa.objects.get_or_create(empresa=empresa)
        configuracion.plantilla_factura_pdf = "ejecutiva_amkt"
        configuracion.save(update_fields=["plantilla_factura_pdf"])


def restaurar_plantilla_clasica(apps, schema_editor):
    ConfiguracionFacturacionEmpresa = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")
    for configuracion in ConfiguracionFacturacionEmpresa.objects.filter(plantilla_factura_pdf="ejecutiva_amkt").select_related("empresa"):
        empresa = configuracion.empresa
        if "amkt" in f"{empresa.slug or ''} {empresa.nombre or ''}".lower():
            configuracion.plantilla_factura_pdf = "normal"
            configuracion.save(update_fields=["plantilla_factura_pdf"])


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0061_precio_final_luque_aestetic"),
    ]

    operations = [
        migrations.AlterField(
            model_name="configuracionfacturacionempresa",
            name="plantilla_factura_pdf",
            field=models.CharField(
                choices=[
                    ("normal", "Factura clasica"),
                    ("alternativa", "Factura alternativa"),
                    ("notas_extensas", "Factura notas extensas"),
                    ("independiente", "Factura independiente"),
                    ("termica_80mm", "Factura termica 80 mm"),
                    ("ejecutiva_amkt", "Factura ejecutiva AMKT"),
                ],
                default="normal",
                max_length=20,
            ),
        ),
        migrations.RunPython(configurar_plantilla_amkt, restaurar_plantilla_clasica),
    ]
