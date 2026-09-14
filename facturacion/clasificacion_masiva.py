"""Revisión firmada de la clasificación masiva; no cambia facturas al previsualizar."""
import json
from decimal import Decimal
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .captura_rapida import serializar, MESES
from .proveedores_compras import cuenta_sugerida

SALT = 'compras.clasificacion.masiva.v1'


def guardar_cuenta(compra, cuenta, usuario):
    compra.cuenta_acumulado = cuenta
    compra.clasificado_por = usuario
    compra.clasificado_en = timezone.now()
    compra.save(update_fields=['cuenta_acumulado', 'clasificado_por', 'clasificado_en'])


def revisar_o_confirmar(request, empresa, cliente, anio, registros, filtradas, cuentas):
    """El llamador conserva el bloqueo de empresa durante revisión/confirmación."""
    contexto = [empresa.pk, cliente.pk, request.user.pk, anio]
    if request.POST.get('accion') == 'confirmar_asignacion':
        try:
            datos = signing.loads(request.POST.get('token',''), salt=SALT, max_age=3600)
        except signing.BadSignature:
            raise ValidationError('La revisión venció o no es válida. Selecciona y revisa las facturas nuevamente.')
        if datos['contexto'] != contexto:
            raise PermissionDenied
        try:
            seleccion = json.loads(request.POST.get('seleccion', '[]'))
            if not isinstance(seleccion, list) or len(seleccion) > 1000:
                raise ValueError
            ids = set(map(int, seleccion))
        except (ValueError, TypeError):
            raise ValidationError('La selección no es válida.')
        filas = {f['id']:f for f in datos['filas']}
        if not ids or not ids.issubset(filas):
            raise ValidationError('Selecciona facturas incluidas en la revisión.')
        elegidas = list(registros.select_for_update().filter(pk__in=ids).select_related('proveedor__cuenta_habitual'))
        if len(elegidas) != len(ids):
            raise ValidationError('La selección contiene compras ajenas al cliente o año.')
        destinos = {}
        for c in elegidas:
            f = filas[c.pk]
            if c.estado == 'anulada' or serializar(c)['version'] != f['version']:
                raise ValidationError(f'La factura #{c.pk} cambió o fue anulada. Revisa el lote otra vez.')
            destino = None if f['cuenta'] is None else get_object_or_404(cuentas, pk=f['cuenta'], activa=True)
            if datos['sugeridas'] and (not cuenta_sugerida(c.proveedor) or cuenta_sugerida(c.proveedor).pk != f['cuenta']):
                raise ValidationError(f'La sugerencia de la factura #{c.pk} cambió. Revisa el lote otra vez.')
            destinos[c.pk] = destino
        for c in elegidas:
            guardar_cuenta(c, destinos[c.pk], request.user)
        return len(elegidas), None
    if request.POST.get('alcance') == 'filtradas':
        elegidas = list(filtradas.select_related('proveedor__cuenta_habitual')[:1001])
    else:
        ids = request.POST.getlist('registros')
        if not ids or len(ids)>100 or any(not i.isdigit() for i in ids):
            raise ValidationError('Selecciona facturas de esta página o elige revisar todas las filtradas.')
        ids = set(map(int,ids))
        elegidas = list(registros.filter(pk__in=ids).select_related('proveedor__cuenta_habitual'))
        if len(elegidas) != len(ids):
            raise ValidationError('La selección contiene facturas ajenas al cliente o año.')
        for c in elegidas:
            if request.POST.get(f'version_{c.pk}') != serializar(c)['version']:
                raise ValidationError(f'La factura #{c.pk} cambió. Actualiza la página.')
    if not elegidas or len(elegidas)>1000:
        raise ValidationError('Revisa entre 1 y 1000 facturas por lote. Reduce el filtro si hay más.')
    sugeridas = request.POST.get('modo') == 'sugeridas'
    cuenta = None
    if not sugeridas:
        destino = request.POST.get('cuenta_destino','')
        if destino != 'pendiente' and not destino.isdigit():
            raise ValidationError('Selecciona la cuenta que deseas aplicar.')
        cuenta = None if destino == 'pendiente' else get_object_or_404(cuentas,pk=destino,activa=True)
    filas = []
    for c in elegidas:
        if c.estado == 'anulada':
            raise ValidationError(f'La factura #{c.pk} está anulada.')
        actual = cuenta_sugerida(c.proveedor) if sugeridas else cuenta
        if sugeridas and not actual:
            raise ValidationError(f'La factura #{c.pk} no tiene proveedor con cuenta habitual activa. Desmárcala o configura la sugerencia.')
        filas.append(dict(id=c.pk,version=serializar(c)['version'],cuenta=actual.pk if actual else None,
            cuenta_nombre=actual.nombre if actual else 'Por clasificar', proveedor=c.proveedor_nombre,
            numero=c.numero_factura, mes=MESES[c.periodo_mes-1], total=f'{c.total:.2f}',
            neto=f'{c.exento+c.base_15+c.base_18+c.exonerado:.2f}'))
    token = signing.dumps(dict(contexto=contexto,filas=filas,sugeridas=sugeridas),salt=SALT,compress=True)
    return None, render(request,'facturacion/revisar_clasificacion.html',dict(empresa=empresa,cliente=cliente,
        anio=anio, filas=filas, token=token, total=sum((Decimal(f['total']) for f in filas),Decimal('0.00')),
        neto=sum((Decimal(f['neto']) for f in filas),Decimal('0.00')),
        cuentas_destino=', '.join(dict.fromkeys(f['cuenta_nombre'] for f in filas))))
