import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_modulo_clinica_medica"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RespaldoEmpresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "estado",
                    models.CharField(
                        choices=[("generando", "Generando"), ("exitoso", "Exitoso"), ("fallido", "Fallido")],
                        default="generando",
                        max_length=20,
                    ),
                ),
                ("nombre_archivo", models.CharField(blank=True, max_length=255)),
                ("registros_incluidos", models.PositiveBigIntegerField(default=0)),
                ("archivos_incluidos", models.PositiveIntegerField(default=0)),
                ("tamano_bytes", models.PositiveBigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("detalle_error", models.TextField(blank=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("fecha_finalizacion", models.DateTimeField(blank=True, null=True)),
                (
                    "empresa",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="respaldos", to="core.empresa"),
                ),
                (
                    "generado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="respaldos_generados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Respaldo de empresa",
                "verbose_name_plural": "Respaldos de empresas",
                "ordering": ["-fecha_creacion", "-id"],
            },
        ),
    ]
