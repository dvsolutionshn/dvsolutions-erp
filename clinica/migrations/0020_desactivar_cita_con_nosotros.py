import unicodedata

from django.db import migrations


EMPRESAS_OBJETIVO = {"hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"}


def _normalizar(texto):
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower().strip()


def desactivar_cita_con_nosotros(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ServicioClinico = apps.get_model("clinica", "ServicioClinico")
    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_OBJETIVO):
        for servicio in ServicioClinico.objects.filter(empresa=empresa, activo=True):
            if _normalizar(servicio.nombre) == "cita con nosotros":
                servicio.activo = False
                servicio.save(update_fields=["activo"])


class Migration(migrations.Migration):

    dependencies = [
        ("clinica", "0019_separar_servicios_agenda_base"),
    ]

    operations = [
        migrations.RunPython(desactivar_cita_con_nosotros, migrations.RunPython.noop),
    ]
