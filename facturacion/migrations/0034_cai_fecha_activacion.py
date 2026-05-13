from datetime import date
import django.utils.timezone
from django.db import migrations, models


def poblar_fecha_activacion_historica(apps, schema_editor):
    CAI = apps.get_model('facturacion', 'CAI')
    CAI.objects.filter(fecha_activacion__isnull=True).update(fecha_activacion=date(2000, 1, 1))


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0033_categoriaproductofarmaceutico_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cai',
            name='fecha_activacion',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(poblar_fecha_activacion_historica, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='cai',
            name='fecha_activacion',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]
