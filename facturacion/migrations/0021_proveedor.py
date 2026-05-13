from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0020_compra_anulada_y_reversion'),
    ]

    operations = [
        migrations.CreateModel(
            name='Proveedor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=200)),
                ('rtn', models.CharField(blank=True, max_length=20, null=True)),
                ('contacto', models.CharField(blank=True, max_length=150, null=True)),
                ('telefono', models.CharField(blank=True, max_length=50, null=True)),
                ('correo', models.EmailField(blank=True, max_length=254, null=True)),
                ('direccion', models.TextField(blank=True, null=True)),
                ('ciudad', models.CharField(blank=True, max_length=100, null=True)),
                ('activo', models.BooleanField(default=True)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.empresa')),
            ],
        ),
        migrations.AddField(
            model_name='comprainventario',
            name='proveedor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='facturacion.proveedor'),
        ),
    ]
