import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0022_regularizacion_moneda_factura"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RevisionConversionAsiento",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha", models.DateField()),
                ("partidas", models.JSONField()),
                ("firma_fuente", models.CharField(max_length=64)),
                ("motivo", models.TextField()),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("ajuste", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="revision_conversion_aplicada", to="contabilidad.asientocontable")),
                ("cuenta_contrapartida", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="contabilidad.cuentacontable")),
                ("creado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.empresa")),
                ("origen", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="revision_conversion", to="contabilidad.asientocontable")),
            ],
            options={
                "verbose_name": "Revisión integral de conversión histórica",
                "verbose_name_plural": "Revisiones integrales de conversión histórica",
            },
        ),
    ]
