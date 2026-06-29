from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_restaurar_perfiles_clinicos"),
    ]

    operations = [
        migrations.AddField(
            model_name="usuario",
            name="empresas_acceso",
            field=models.ManyToManyField(
                blank=True,
                help_text="Empresas adicionales a las que este usuario puede ingresar con la misma cuenta.",
                related_name="usuarios_con_acceso",
                to="core.empresa",
            ),
        ),
    ]
