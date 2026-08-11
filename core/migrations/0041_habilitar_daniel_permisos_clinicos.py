from django.db import migrations
from django.db.models import Q


def habilitar_daniel_permisos_clinicos(apps, schema_editor):
    Usuario = apps.get_model("core", "Usuario")
    Usuario.objects.filter(
        Q(email__iexact="dannyvarela25@gmail.com")
        | Q(username__iexact="dannyvarela25@gmail.com")
        | Q(username__iexact="dannyvarela25")
        | (Q(first_name__iexact="Daniel") & Q(last_name__iexact="Varela"))
    ).update(puede_administrar_usuarios_clinicos=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0040_usuario_administra_usuarios_clinicos")]

    operations = [
        migrations.RunPython(
            habilitar_daniel_permisos_clinicos,
            migrations.RunPython.noop,
        ),
    ]
