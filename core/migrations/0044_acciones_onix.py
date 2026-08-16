import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def habilitar_acciones_demo(apps, schema_editor):
    ConfiguracionOnix = apps.get_model("core", "ConfiguracionOnix")
    ConfiguracionOnix.objects.filter(empresa__slug="demo_1").update(
        herramientas_accion_activas=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_configuraciononix_conversaciononix_consumoonix_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuraciononix",
            name="herramientas_accion_activas",
            field=models.BooleanField(
                default=False,
                help_text="Permite que Onix prepare acciones que requieren confirmacion explicita del usuario.",
            ),
        ),
        migrations.CreateModel(
            name="AccionOnix",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tipo", models.CharField(choices=[("crear_borrador_factura", "Crear borrador de factura")], max_length=50)),
                ("estado", models.CharField(choices=[("pendiente", "Pendiente de confirmacion"), ("ejecutada", "Ejecutada"), ("cancelada", "Cancelada"), ("expirada", "Expirada"), ("error", "Error")], db_index=True, default="pendiente", max_length=20)),
                ("datos", models.JSONField(default=dict)),
                ("vista_previa", models.JSONField(default=dict)),
                ("resultado", models.JSONField(blank=True, default=dict)),
                ("detalle_error", models.TextField(blank=True)),
                ("expira_en", models.DateTimeField(db_index=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("fecha_confirmacion", models.DateTimeField(blank=True, null=True)),
                ("fecha_actualizacion", models.DateTimeField(auto_now=True)),
                ("conversacion", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acciones", to="core.conversaciononix")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="acciones_onix", to="core.empresa")),
                ("usuario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acciones_onix", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Accion de Onix",
                "verbose_name_plural": "Acciones de Onix",
                "ordering": ["-fecha_creacion"],
            },
        ),
        migrations.AddIndex(
            model_name="acciononix",
            index=models.Index(fields=["empresa", "usuario", "estado"], name="core_accion_empresa_c8268d_idx"),
        ),
        migrations.RunPython(habilitar_acciones_demo, migrations.RunPython.noop),
    ]
