from django.db import migrations, models


def crear_modulo_tecnicentro(apps, schema_editor):
    Modulo = apps.get_model("core", "Modulo")
    Modulo.objects.update_or_create(
        codigo="tecnicentro",
        defaults={"nombre": "Tecnicentro Vehicular", "es_comercial": True},
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0030_permisos_configuracion_y_cierres_caja")]

    operations = [
        migrations.AddField(model_name="rolsistema", name="puede_tecnicentro", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_recepcion_taller", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_diagnostico_taller", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_operacion_taller", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="puede_configuracion_taller", field=models.BooleanField(default=False)),
        migrations.RunPython(crear_modulo_tecnicentro, migrations.RunPython.noop),
    ]
