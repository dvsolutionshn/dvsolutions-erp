from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0058_permisos_clinicos_v2")]

    operations = [
        migrations.AddField(model_name="rolsistema", name="solo_lectura_facturacion", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_inventario", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_contabilidad", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_rrhh", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_crm", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_agenda", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_clinica", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="rolsistema", name="solo_lectura_tecnicentro", field=models.BooleanField(default=False)),
    ]
