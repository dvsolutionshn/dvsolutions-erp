from django.db import migrations


def habilitar_manuales_enfermera(apps, schema_editor):
    RolSistema = apps.get_model("core", "RolSistema")
    RolSistema.objects.filter(codigo="clinico-enfermera").update(
        puede_ver_manuales_pdf=True,
        puede_enviar_manuales_pdf=True,
    )


def conservar_configuracion(apps, schema_editor):
    # No se revocan permisos en reversa porque el rol puede haber sido personalizado.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_rolsistema_es_rol_clinico_and_more"),
    ]

    operations = [
        migrations.RunPython(habilitar_manuales_enfermera, conservar_configuracion),
    ]
