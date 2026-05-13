from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0019_compra_inventario'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comprainventario',
            name='estado',
            field=models.CharField(choices=[('borrador', 'Borrador'), ('aplicada', 'Aplicada'), ('anulada', 'Anulada')], default='borrador', max_length=15),
        ),
        migrations.AlterField(
            model_name='movimientoinventario',
            name='tipo',
            field=models.CharField(choices=[('entrada', 'Entrada'), ('entrada_compra', 'Entrada por Compra'), ('salida_factura', 'Salida por Factura'), ('devolucion_nota_credito', 'Entrada por Nota de Credito'), ('ajuste_entrada', 'Ajuste Positivo'), ('ajuste_salida', 'Ajuste Negativo'), ('reversion_factura', 'Reversion de Factura'), ('reversion_nota_credito', 'Reversion de Nota de Credito'), ('reversion_compra', 'Reversion de Compra')], max_length=30),
        ),
    ]
