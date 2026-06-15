from django.db import migrations, models


def configurar_plantilla_termica(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ConfiguracionFacturacionEmpresa = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")

    for empresa in Empresa.objects.filter(slug__in=["hospital_mia", "medical_spa"]):
        configuracion, _ = ConfiguracionFacturacionEmpresa.objects.get_or_create(empresa=empresa)
        configuracion.plantilla_factura_pdf = "termica_80mm"
        configuracion.save(update_fields=["plantilla_factura_pdf"])


def restaurar_plantilla_clasica(apps, schema_editor):
    ConfiguracionFacturacionEmpresa = apps.get_model("facturacion", "ConfiguracionFacturacionEmpresa")
    ConfiguracionFacturacionEmpresa.objects.filter(
        empresa__slug__in=["hospital_mia", "medical_spa"],
        plantilla_factura_pdf="termica_80mm",
    ).update(plantilla_factura_pdf="normal")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_activar_pos_empresas_medicas"),
        ("facturacion", "0047_correccionnumerofactura"),
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
                ],
                default="normal",
                max_length=20,
            ),
        ),
        migrations.RunPython(configurar_plantilla_termica, restaurar_plantilla_clasica),
    ]
