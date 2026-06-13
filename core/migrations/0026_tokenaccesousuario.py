import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_tokenrespaldoempresa"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TokenAccesoUsuario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "tipo",
                    models.CharField(
                        choices=[("invitacion", "Invitacion"), ("recuperacion", "Recuperacion")],
                        max_length=20,
                    ),
                ),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("token_preview", models.CharField(max_length=20)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("fecha_expiracion", models.DateTimeField()),
                ("fecha_uso", models.DateTimeField(blank=True, null=True)),
                ("revocado", models.BooleanField(default=False)),
                ("fecha_revocacion", models.DateTimeField(blank=True, null=True)),
                ("ip_solicitud", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "creado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tokens_acceso_creados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tokens_acceso",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Token de acceso de usuario",
                "verbose_name_plural": "Tokens de acceso de usuarios",
                "ordering": ["-fecha_creacion", "-id"],
            },
        ),
    ]
