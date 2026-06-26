from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0054_producto_costo_real_inventario'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistorialCostoRealProducto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('costo_anterior', models.DecimalField(decimal_places=4, default=0, max_digits=12)),
                ('costo_nuevo', models.DecimalField(decimal_places=4, max_digits=12)),
                ('fecha', models.DateTimeField(default=django.utils.timezone.now)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='historial_costos_reales_producto', to='core.empresa')),
                ('producto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='historial_costos_reales', to='facturacion.producto')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cambios_costos_reales_producto', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Historial de costo real de producto',
                'verbose_name_plural': 'Historial de costos reales de productos',
                'ordering': ['-fecha', '-id'],
            },
        ),
    ]
