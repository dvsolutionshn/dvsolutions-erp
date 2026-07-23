from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0037_modulo_cotizaciones"),
        ("crm", "0013_mensajes_automaticos_citas_editables"),
    ]

    operations = [
        migrations.AddField(
            model_name="citacliente",
            name="cirugia_detalle",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="citacliente",
            name="cirugia_fin_estimada",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CitaCirugiaFoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("imagen", models.ImageField(upload_to="crm/citas/cirugias/")),
                ("descripcion", models.CharField(blank=True, max_length=180, null=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                (
                    "cita",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fotos_cirugia", to="crm.citacliente"),
                ),
                (
                    "creado_por",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
                ),
                (
                    "empresa",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fotos_cirugia_citas", to="core.empresa"),
                ),
            ],
            options={
                "verbose_name": "Foto de cirugia agendada",
                "verbose_name_plural": "Fotos de cirugias agendadas",
                "ordering": ["-fecha_creacion", "-id"],
            },
        ),
    ]
