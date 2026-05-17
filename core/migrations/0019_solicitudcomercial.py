from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_configuracionavanzadaempresa_permite_plantilla_factura_independiente"),
    ]

    operations = [
        migrations.CreateModel(
            name="SolicitudComercial",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre_contacto", models.CharField(max_length=160)),
                ("empresa_interesada", models.CharField(blank=True, max_length=180, null=True)),
                ("correo", models.EmailField(max_length=254)),
                ("telefono", models.CharField(blank=True, max_length=30, null=True)),
                ("servicio_interes", models.CharField(choices=[("erp", "ERP empresarial"), ("web", "Sitio web corporativo"), ("app", "Aplicacion movil"), ("software", "Software a medida"), ("integracion", "Integracion y automatizacion"), ("branding", "Diseno digital y branding"), ("otro", "Otro proyecto")], default="software", max_length=20)),
                ("mensaje", models.TextField()),
                ("solicita_prueba", models.BooleanField(default=False)),
                ("estado", models.CharField(choices=[("nueva", "Nueva"), ("contactado", "Contactado"), ("demo", "Demo programada"), ("propuesta", "Propuesta enviada"), ("cerrada", "Cerrada")], default="nueva", max_length=20)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Solicitud comercial",
                "verbose_name_plural": "Solicitudes comerciales",
                "ordering": ["-fecha_creacion"],
            },
        ),
    ]
