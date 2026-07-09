from decimal import Decimal

from django.db import migrations


def normalizar_facturas_anuladas(apps, schema_editor):
    Factura = apps.get_model("facturacion", "Factura")
    Factura.objects.filter(estado="anulada").update(
        subtotal=Decimal("0.00"),
        impuesto=Decimal("0.00"),
        total=Decimal("0.00"),
        total_lempiras=Decimal("0.00"),
        estado_pago="pagado",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0058_bodegas_luque_aestetic"),
    ]

    operations = [
        migrations.RunPython(normalizar_facturas_anuladas, migrations.RunPython.noop),
    ]
