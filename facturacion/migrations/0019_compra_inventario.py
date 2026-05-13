from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0018_entradainventariodocumento_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompraInventario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero_compra', models.CharField(blank=True, max_length=20, null=True, unique=True)),
                ('proveedor_nombre', models.CharField(max_length=200)),
                ('referencia_documento', models.CharField(blank=True, max_length=120, null=True)),
                ('fecha_documento', models.DateField(default=django.utils.timezone.now)),
                ('observacion', models.TextField(blank=True, null=True)),
                ('estado', models.CharField(choices=[('borrador', 'Borrador'), ('aplicada', 'Aplicada')], default='borrador', max_length=15)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.empresa')),
            ],
            options={
                'ordering': ['-fecha_documento', '-id'],
            },
        ),
        migrations.CreateModel(
            name='LineaCompraInventario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad', models.DecimalField(decimal_places=2, max_digits=12)),
                ('costo_unitario', models.DecimalField(decimal_places=2, max_digits=12)),
                ('comentario', models.CharField(blank=True, max_length=150, null=True)),
                ('compra', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lineas', to='facturacion.comprainventario')),
                ('producto', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='facturacion.producto')),
            ],
        ),
        migrations.AddField(
            model_name='movimientoinventario',
            name='compra_documento',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='facturacion.comprainventario'),
        ),
        migrations.AlterField(
            model_name='movimientoinventario',
            name='tipo',
            field=models.CharField(choices=[('entrada', 'Entrada'), ('entrada_compra', 'Entrada por Compra'), ('salida_factura', 'Salida por Factura'), ('devolucion_nota_credito', 'Entrada por Nota de Credito'), ('ajuste_entrada', 'Ajuste Positivo'), ('ajuste_salida', 'Ajuste Negativo'), ('reversion_factura', 'Reversion de Factura'), ('reversion_nota_credito', 'Reversion de Nota de Credito')], max_length=30),
        ),
    ]
