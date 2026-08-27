from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0044_acciones_onix"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SesionOnixMovil",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("dispositivo", models.CharField(blank=True, max_length=160)),
                ("direccion_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=300)),
                ("creada_en", models.DateTimeField(auto_now_add=True)),
                ("ultima_actividad", models.DateTimeField(auto_now=True)),
                ("expira_en", models.DateTimeField(db_index=True)),
                ("revocada_en", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sesiones_onix_movil", to="core.empresa")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sesiones_onix_movil", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Sesion de Onix Mobile",
                "verbose_name_plural": "Sesiones de Onix Mobile",
                "ordering": ["-ultima_actividad", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="sesiononixmovil",
            index=models.Index(fields=["usuario", "empresa", "revocada_en"], name="onix_mob_sesion_activa_idx"),
        ),
    ]

