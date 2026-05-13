from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_configuracionavanzadaempresa_permite_cai_historico"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionPowerBIEmpresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("activo", models.BooleanField(default=False)),
                ("mostrar_en_reportes", models.BooleanField(default=True)),
                ("titulo_panel", models.CharField(default="Dashboard ejecutivo", max_length=160)),
                ("descripcion_panel", models.TextField(blank=True, null=True)),
                ("url_embed", models.URLField(blank=True, null=True)),
                ("alto_iframe", models.PositiveIntegerField(default=760)),
                ("usa_token_seguro", models.BooleanField(default=False)),
                ("workspace_id", models.CharField(blank=True, max_length=160, null=True)),
                ("report_id", models.CharField(blank=True, max_length=160, null=True)),
                ("fecha_actualizacion", models.DateTimeField(auto_now=True)),
                (
                    "empresa",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configuracion_power_bi",
                        to="core.empresa",
                    ),
                ),
            ],
            options={
                "verbose_name": "Configuracion Power BI por empresa",
                "verbose_name_plural": "Configuraciones Power BI por empresa",
                "ordering": ["empresa__nombre"],
            },
        ),
    ]
