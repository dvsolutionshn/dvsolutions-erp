import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rrhh", "0004_periodoplanilla_metodo_pago"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionrrhhempresa",
            name="editores_planilla",
            field=models.ManyToManyField(
                blank=True,
                help_text="Usuarios autorizados por Daniel Varela para corregir planillas abiertas.",
                related_name="empresas_donde_edita_planillas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="detalleplanilla",
            name="editado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="detalles_planilla_editados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="detalleplanilla",
            name="fecha_ultima_edicion",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
