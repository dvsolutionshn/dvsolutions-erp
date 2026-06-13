import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_respaldoempresa"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TokenRespaldoEmpresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("token_preview", models.CharField(max_length=24)),
                ("referencia_pago", models.CharField(blank=True, max_length=160)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("fecha_expiracion", models.DateTimeField()),
                ("fecha_uso", models.DateTimeField(blank=True, null=True)),
                ("revocado", models.BooleanField(default=False)),
                ("fecha_revocacion", models.DateTimeField(blank=True, null=True)),
                (
                    "creado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tokens_respaldo_creados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "empresa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tokens_respaldo",
                        to="core.empresa",
                    ),
                ),
                (
                    "usado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tokens_respaldo_usados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Token de respaldo empresarial",
                "verbose_name_plural": "Tokens de respaldo empresarial",
                "ordering": ["-fecha_creacion", "-id"],
            },
        ),
    ]
