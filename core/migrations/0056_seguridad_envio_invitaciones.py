from django.db import migrations, models
from django.utils import timezone


def revocar_enlaces_sin_firma(apps, schema_editor):
    # Los enlaces antiguos no registraron el estado de la cuenta al emitirse.
    # No se puede garantizar que su correo o su estado no hayan cambiado.
    Token = apps.get_model("core", "TokenAccesoUsuario")
    Token.objects.using(schema_editor.connection.alias).filter(
        estado_usuario_hash="", fecha_uso__isnull=True, revocado=False,
    ).update(revocado=True, fecha_revocacion=timezone.now())


class Migration(migrations.Migration):
    dependencies = [("core", "0055_habilitar_gastos_adicionales")]

    operations = [
        migrations.AddField(
            model_name="tokenaccesousuario", name="estado_usuario_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="tokenaccesousuario", name="estado_envio",
            field=models.CharField(
                choices=[
                    ("desconocido", "Envio sin confirmar"),
                    ("pendiente", "Sin enviar"),
                    ("enviado", "Enviado al servidor de correo"),
                    ("error", "Error de envio"),
                ],
                default="desconocido", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="tokenaccesousuario", name="fecha_intento_envio",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tokenaccesousuario", name="fecha_envio",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(revocar_enlaces_sin_firma, migrations.RunPython.noop),
    ]
