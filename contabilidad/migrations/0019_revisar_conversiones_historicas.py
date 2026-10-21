"""Documenta el legado sin reescribir importes ni reabrir períodos."""
from decimal import Decimal

from django.db import migrations
from django.db.models import F, Q


def documentar_moneda_historica(apps, schema_editor):
    alias = schema_editor.connection.alias
    Asiento = apps.get_model('contabilidad', 'AsientoContable')
    Linea = apps.get_model('contabilidad', 'LineaAsientoContable')
    Factura = apps.get_model('facturacion', 'Factura')
    Nota = apps.get_model('facturacion', 'NotaCredito')
    Pago = apps.get_model('facturacion', 'PagoFactura')
    Comision = apps.get_model('facturacion', 'PagoComisionProveedor')

    # En registros anteriores los importes publicados son la única evidencia.
    # Se conserva como origen HNL; los USD se señalan para revisión individual.
    Linea.objects.using(alias).update(debe_origen=F('debe'), haber_origen=F('haber'))

    notas_usd = (
        (pk, empresa_id, tasa_origen if moneda_origen == 'USD' else tasa_nota)
        for pk, empresa_id, tasa_nota, moneda_origen, tasa_origen in Nota.objects.using(alias)
        .filter(Q(moneda='USD') | Q(factura_origen__moneda='USD'))
        .values_list('pk', 'empresa_id', 'tipo_cambio', 'factura_origen__moneda', 'factura_origen__tipo_cambio').iterator()
    )
    fuentes = [
        ('factura', 'emision', Factura.objects.using(alias).filter(moneda='USD')
         .values_list('pk', 'empresa_id', 'tipo_cambio')),
        ('nota_credito', 'emision', notas_usd),
        ('pago_factura', 'cobro', Pago.objects.using(alias).filter(factura__moneda='USD')
         .values_list('pk', 'factura__empresa_id', 'factura__tipo_cambio')),
        ('pago_comision_proveedor', 'egreso', Comision.objects.using(alias)
         .filter(comision_origen__factura__moneda='USD')
         .values_list('pk', 'comision_origen__factura__empresa_id', 'comision_origen__factura__tipo_cambio')),
    ]
    for tipo, evento, documentos in fuentes:
        for documento_id, empresa_id, tasa in (documentos.iterator() if hasattr(documentos, 'iterator') else documentos):
            datos = {'moneda_origen': 'USD', 'conversion_verificada': False}
            if tasa is not None and Decimal(tasa) > 0:
                datos['tipo_cambio'] = tasa
            # No se retargetea un asiento importado que pertenece a otra empresa.
            Asiento.objects.using(alias).filter(
                empresa_id=empresa_id, documento_tipo=tipo,
                documento_id=documento_id,
            ).update(**datos)


class Migration(migrations.Migration):
    dependencies = [
        ('contabilidad', '0018_moneda_inventario_integrados'),
        ('facturacion', '0080_moneda_inventario_integrados'),
    ]
    operations = [migrations.RunPython(documentar_moneda_historica, migrations.RunPython.noop)]
