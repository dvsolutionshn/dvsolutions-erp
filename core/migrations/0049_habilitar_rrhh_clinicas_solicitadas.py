from django.db import migrations


EMPRESAS_SOLICITADAS = {"hospital_mia", "medical_spa"}


def habilitar_rrhh_en_clinicas(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    Modulo = apps.get_model("core", "Modulo")
    Configuracion = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")

    rrhh, _ = Modulo.objects.get_or_create(
        codigo="rrhh",
        defaults={"nombre": "Recursos Humanos", "es_comercial": True},
    )
    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_SOLICITADAS):
        EmpresaModulo.objects.update_or_create(
            empresa=empresa,
            modulo=rrhh,
            defaults={"activo": True},
        )
        configuracion, _ = Configuracion.objects.get_or_create(empresa=empresa)
        configuracion.modulos_adicionales_visibles_clinica.add(rrhh)


class Migration(migrations.Migration):
    dependencies = [("core", "0048_ampliar_acciones_onix")]

    operations = [
        migrations.RunPython(habilitar_rrhh_en_clinicas, migrations.RunPython.noop),
    ]
