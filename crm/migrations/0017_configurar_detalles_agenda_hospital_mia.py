from django.db import migrations


def configurar_agenda_hospital_mia(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ServicioClinico = apps.get_model("clinica", "ServicioClinico")
    OpcionServicioAgenda = apps.get_model("crm", "OpcionServicioAgenda")

    empresa = Empresa.objects.filter(slug="hospital_mia").first()
    if not empresa:
        return

    ServicioClinico.objects.filter(
        empresa=empresa,
        nombre__in=["Hidrofacial", "Hydrofacial"],
    ).update(activo=False)
    OpcionServicioAgenda.objects.get_or_create(
        empresa=empresa,
        categoria="tratamientos",
        nombre="Hidrofacial",
        defaults={"orden": 10, "activo": True},
    )


def revertir_configuracion(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    OpcionServicioAgenda = apps.get_model("crm", "OpcionServicioAgenda")

    empresa = Empresa.objects.filter(slug="hospital_mia").first()
    if empresa:
        OpcionServicioAgenda.objects.filter(
            empresa=empresa,
            categoria="tratamientos",
            nombre="Hidrofacial",
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0016_citacliente_fase_servicio_citacliente_grupo_atencion_and_more"),
    ]

    operations = [
        migrations.RunPython(configurar_agenda_hospital_mia, revertir_configuracion),
    ]
