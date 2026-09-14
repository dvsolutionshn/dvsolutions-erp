import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError, OperationalError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import Empresa
from .clientes_contables import empresa_contable, cliente_autorizado
from .captura_rapida import serializar, MESES
from .models import RegistroCompraFiscal, Proveedor
from .proveedores_compras import es_nordic, buscar_proveedor, resolver_proveedor, nombre_normalizado, rtn_normalizado

SALT = 'compras.vincular.proveedores.v1'


def plan_vinculacion(registros, proveedores):
    grupos = {}
    for compra in registros:
        if compra.proveedor_id:
            continue
        try:
            rtn = rtn_normalizado(compra.proveedor_rtn)
            clave = ('rtn', rtn) if rtn else ('nombre', nombre_normalizado(compra.proveedor_nombre))
            proveedor = buscar_proveedor(proveedores, compra.proveedor_nombre, rtn)
            error = ''
        except ValidationError as exc:
            clave = ('error', compra.pk); proveedor = None; error = '; '.join(exc.messages)
        grupo = grupos.setdefault(clave, dict(nombre=compra.proveedor_nombre, rtn=compra.proveedor_rtn or '',
            proveedor=proveedor, error=error, registros=[], id=len(grupos)))
        grupo['registros'].append(compra.pk)
    return list(grupos.values())


@login_required
@require_http_methods(['GET', 'POST'])
def vincular_libro(request, empresa_slug, cliente_id, anio, mes):
    empresa = empresa_contable(request, empresa_slug)
    cliente = cliente_autorizado(request, empresa, cliente_id)
    if not es_nordic(cliente) or not 1 <= anio <= 9999 or not 1 <= mes <= 12:
        raise Http404
    if not cliente.activo or not request.user.tiene_permiso_erp('puede_editar_compras', empresa):
        raise PermissionDenied
    registros = RegistroCompraFiscal.objects.filter(empresa=empresa, cliente_contable=cliente,
        periodo_anio=anio, periodo_mes=mes).order_by('pk')
    error = None
    if request.method == 'POST':
        try:
            datos = signing.loads(request.POST.get('token',''), salt=SALT, max_age=3600)
            if datos['contexto'] != [empresa.pk, cliente.pk, request.user.pk, anio, mes]:
                raise PermissionDenied
            seleccion = request.POST.getlist('grupos')
            if request.POST.get('seleccion'):
                try:
                    seleccion = json.loads(request.POST['seleccion'])
                    if not isinstance(seleccion, list) or not all(isinstance(s,str) for s in seleccion):
                        raise ValueError
                except (ValueError, TypeError):
                    raise ValidationError('La selección no es válida.')
            ids = {int(pk) for g in datos['grupos'] if str(g['id']) in seleccion for pk in g['registros']}
            if not ids:
                raise ValidationError('Selecciona al menos un grupo sin errores.')
            with transaction.atomic():
                Empresa.objects.select_for_update().get(pk=empresa.pk)
                actual = cliente_autorizado(request, empresa, cliente_id)
                if not actual.activo:
                    raise PermissionDenied
                compras = list(registros.select_for_update().filter(pk__in=ids))
                if len(compras) != len(ids) or any(c.proveedor_id or datos['versiones'].get(str(c.pk)) != serializar(c)['version'] for c in compras):
                    raise ValidationError('El libro cambió desde la revisión. Actualiza y vuelve a revisar.')
                creados = 0
                catalogo = list(Proveedor.objects.filter(empresa=empresa, cliente_contable=cliente))
                for c in compras:
                    proveedor, creado = resolver_proveedor(empresa, cliente, c.proveedor_nombre, c.proveedor_rtn, request.user, catalogo)
                    creados += int(creado)
                    # Solo relación y auditoría; los datos históricos de factura se conservan exactamente.
                    registros.filter(pk=c.pk).update(proveedor=proveedor, proveedor_vinculado_por=request.user,
                                                     proveedor_vinculado_en=timezone.now())
            messages.success(request, f'{len(compras)} facturas vinculadas; {creados} proveedores creados. Los datos originales se conservaron.')
            return redirect('captura_cliente_contable', empresa_slug=empresa_slug, cliente_id=cliente.pk, anio=anio, mes=mes)
        except signing.BadSignature:
            error = 'La revisión venció o no es válida. Revisa nuevamente los grupos.'
        except ValidationError as exc:
            error = '; '.join(exc.messages)
        except (IntegrityError, OperationalError):
            error = 'No se guardaron cambios. Actualiza y reintenta cuando termine el otro guardado.'
    compras = list(registros)
    grupos = plan_vinculacion(compras, list(Proveedor.objects.filter(empresa=empresa, cliente_contable=cliente)))
    datos = dict(contexto=[empresa.pk,cliente.pk,request.user.pk,anio,mes],
                 grupos=[{'id':g['id'],'registros':g['registros']} for g in grupos if not g['error']],
                 versiones={str(c.pk):serializar(c)['version'] for c in compras if not c.proveedor_id})
    return render(request, 'facturacion/vincular_proveedores.html', dict(empresa=empresa, cliente=cliente,
        anio=anio, mes=mes, nombre_mes=MESES[mes-1], grupos=grupos, error=error,
        vinculadas=sum(bool(c.proveedor_id) for c in compras), token=signing.dumps(datos,salt=SALT,compress=True)))
