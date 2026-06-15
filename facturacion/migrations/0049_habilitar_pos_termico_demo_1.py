from django.db import migrations


def habilitar_pos_termico_demo(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    Modulo = apps.get_model("core", "Modulo")
    ConfiguracionFacturacionEmpresa = apps.get_model(
        "facturacion",
        "ConfiguracionFacturacionEmpresa",
    )

    empresa = Empresa.objects.filter(slug="demo_1").first()
    if not empresa:
        return

    modulos = [
        ("facturacion", "Facturacion"),
        ("punto_venta", "Punto de Venta"),
    ]
    for codigo, nombre in modulos:
        modulo, _ = Modulo.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "es_comercial": True},
        )
        EmpresaModulo.objects.update_or_create(
            empresa=empresa,
            modulo=modulo,
            defaults={"activo": True},
        )

    configuracion, _ = ConfiguracionFacturacionEmpresa.objects.get_or_create(
        empresa=empresa,
    )
    configuracion.plantilla_factura_pdf = "termica_80mm"
    configuracion.save(update_fields=["plantilla_factura_pdf"])


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0048_plantilla_termica_empresas_medicas"),
    ]

    operations = [
        migrations.RunPython(habilitar_pos_termico_demo, migrations.RunPython.noop),
    ]
