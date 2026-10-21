from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("contabilidad", "0024_cuentas_control_extracontable"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracioncontableempresa",
            name="catalogo_rumi_instalado",
            field=models.BooleanField(default=False, verbose_name="Catálogo propio de RUMI instalado"),
        ),
        migrations.CreateModel(
            name="VinculoCuentaOperativaRumi",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("clave", models.CharField(max_length=70)),
                ("cuenta", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="vinculos_operativos_rumi", to="contabilidad.cuentacontable",
                )),
                ("empresa", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="vinculos_cuentas_operativas_rumi", to="core.empresa",
                )),
            ],
            options={
                "verbose_name": "Enlace de cuenta operativa de RUMI",
                "verbose_name_plural": "Enlaces de cuentas operativas de RUMI",
                "ordering": ["clave"],
                "constraints": [models.UniqueConstraint(
                    fields=("empresa", "clave"), name="rumi_vinculo_empresa_clave_unico",
                )],
            },
        ),
        migrations.CreateModel(
            name="RegistroSustitucionCatalogoRumi",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("respaldo", models.JSONField(blank=True, default=dict)),
                ("resumen", models.JSONField(blank=True, default=dict)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="sustituciones_catalogo_rumi", to="core.empresa",
                )),
                ("usuario", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="sustituciones_catalogo_rumi_registradas", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Registro de sustitución del catálogo de RUMI",
                "verbose_name_plural": "Registros de sustitución del catálogo de RUMI",
                "ordering": ["-fecha_creacion", "-pk"],
            },
        ),
    ]
