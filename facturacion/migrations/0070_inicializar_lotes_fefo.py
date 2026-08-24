from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


EMPRESAS_FEFO = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}


def inicializar_lotes_fefo(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    Producto = apps.get_model("facturacion", "Producto")
    InventarioProducto = apps.get_model("facturacion", "InventarioProducto")
    BodegaInventario = apps.get_model("facturacion", "BodegaInventario")
    LoteInventario = apps.get_model("facturacion", "LoteInventario")
    ExistenciaLoteBodega = apps.get_model("facturacion", "ExistenciaLoteBodega")
    MovimientoLoteBodega = apps.get_model("facturacion", "MovimientoLoteBodega")

    for empresa in Empresa.objects.filter(slug__in=EMPRESAS_FEFO):
        prefiere_vitrina = empresa.slug in {"hospital_mia", "medical_spa"}
        tipo_preferido = "vitrina" if prefiere_vitrina else "principal"
        nombre_preferido = "Vitrina" if prefiere_vitrina else "Bodega Principal"
        bodega = (
            BodegaInventario.objects.filter(
                empresa=empresa,
                tipo=tipo_preferido,
                activa=True,
            ).order_by("id").first()
            or BodegaInventario.objects.filter(empresa=empresa, activa=True).order_by("id").first()
        )
        if bodega is None:
            bodega = BodegaInventario.objects.create(
                empresa=empresa,
                nombre=nombre_preferido,
                tipo=tipo_preferido,
                activa=True,
            )

        productos = Producto.objects.filter(
            empresa=empresa,
            controla_inventario=True,
            eliminado=False,
        ).iterator()
        for producto in productos:
            inventario = InventarioProducto.objects.filter(producto=producto).first()
            existencia_general = Decimal(inventario.existencias or 0) if inventario else Decimal("0.00")
            existencia_lotes = (
                ExistenciaLoteBodega.objects.filter(
                    empresa=empresa,
                    lote__producto=producto,
                ).aggregate(total=Sum("cantidad"))["total"]
                or Decimal("0.00")
            )
            diferencia = existencia_general - existencia_lotes
            if diferencia <= 0:
                continue

            lote, _ = LoteInventario.objects.get_or_create(
                empresa=empresa,
                producto=producto,
                numero_lote=f"SIN-LOTE-{producto.id}",
                defaults={"activo": True},
            )
            existencia, _ = ExistenciaLoteBodega.objects.get_or_create(
                empresa=empresa,
                bodega=bodega,
                lote=lote,
                defaults={"cantidad": Decimal("0.00")},
            )
            anterior = Decimal(existencia.cantidad or 0)
            existencia.cantidad = anterior + diferencia
            existencia.save(update_fields=["cantidad"])
            MovimientoLoteBodega.objects.create(
                empresa=empresa,
                bodega=bodega,
                lote=lote,
                tipo="ajuste",
                cantidad=diferencia,
                existencia_anterior=anterior,
                existencia_resultante=existencia.cantidad,
                referencia="Migracion inicial FEFO",
                observacion="Existencia previa distribuida en un lote tecnico sin duplicar el producto.",
            )


class Migration(migrations.Migration):
    dependencies = [
        ("facturacion", "0069_pagocomisionproveedor"),
    ]

    operations = [
        migrations.RunPython(inicializar_lotes_fefo, migrations.RunPython.noop),
    ]
