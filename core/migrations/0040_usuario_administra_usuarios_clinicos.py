from django.db import migrations, models
from django.db.models import Q


def habilitar_doctora_candy(apps, schema_editor):
    Usuario = apps.get_model("core", "Usuario")
    Usuario.objects.filter(
        Q(username__icontains="candy")
        | Q(email__icontains="candy")
        | Q(first_name__icontains="candy")
        | (Q(first_name__icontains="candy") & Q(last_name__icontains="luque"))
    ).update(puede_administrar_usuarios_clinicos=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0039_usuario_modo_clinico_simple")]

    operations = [
        migrations.AddField(
            model_name="usuario",
            name="puede_administrar_usuarios_clinicos",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Permite revisar y asignar roles a los usuarios de las empresas clínicas autorizadas, "
                    "sin exponer el control maestro del ERP."
                ),
            ),
        ),
        migrations.RunPython(habilitar_doctora_candy, migrations.RunPython.noop),
    ]
