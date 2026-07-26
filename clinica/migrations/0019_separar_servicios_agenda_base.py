import unicodedata

from django.db import migrations


EMPRESAS_OBJETIVO = {"hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"}

SERVICIOS = [
    ("Cita con nosotros", "consulta", 30),
    ("Tratamientos", "tratamiento", 60),
    ("Camara hiperbarica", "tratamiento", 60),
    ("Terapias", "tratamiento", 60),
    ("Spa", "spa", 60),
]


def separar_servicios_agenda(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ServicioClinico = apps.get_model("clinica", "ServicioClinico")
    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_OBJETIVO):
        for servicio in ServicioClinico.objects.filter(empresa=empresa, activo=True):
            nombre = unicodedata.normalize("NFKD", servicio.nombre or "").encode("ascii", "ignore").decode("ascii").lower()
            if "terapia" in nombre and ("camara" in nombre or "hiperbar" in nombre):
                servicio.activo = False
                servicio.save(update_fields=["activo"])
        for nombre, categoria, duracion in SERVICIOS:
            servicio = (
                ServicioClinico.objects.filter(empresa=empresa, nombre=nombre, activo=True).first()
                or ServicioClinico.objects.filter(empresa=empresa, nombre=nombre).first()
            )
            if servicio:
                servicio.categoria = categoria
                servicio.duracion_minutos = duracion
                servicio.activo = True
                servicio.save(update_fields=["categoria", "duracion_minutos", "activo"])
            else:
                ServicioClinico.objects.create(
                    empresa=empresa,
                    nombre=nombre,
                    categoria=categoria,
                    duracion_minutos=duracion,
                    precio_referencia=0,
                    requiere_consentimiento=False,
                    activo=True,
                )


class Migration(migrations.Migration):

    dependencies = [
        ("clinica", "0018_documentoclinicopaciente"),
    ]

    operations = [
        migrations.RunPython(separar_servicios_agenda, migrations.RunPython.noop),
    ]
