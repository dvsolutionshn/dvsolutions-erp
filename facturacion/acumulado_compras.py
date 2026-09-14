"""Acumulado por cuentas sobre las compras existentes del cliente."""
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError, OperationalError
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import Empresa
from .captura_rapida import MESES, selector_libros, serializar
from .clientes_contables import empresa_contable, cliente_autorizado
from .models import CuentaAcumuladoCompra, RegistroCompraFiscal

ZERO = Decimal('0.00')


def sin_isv():
    return ExpressionWrapper(F('exento') + F('base_15') + F('base_18') + F('exonerado'),
                             output_field=DecimalField(max_digits=18, decimal_places=2))


def cuenta_fila(nombre, importes, cuenta=None, tipo='cuenta'):
    return dict(nombre=nombre, importes=importes, total=sum(importes, ZERO), cuenta=cuenta, tipo=tipo)


def construir_acumulado(registros, cuentas):
    """Usa el período del libro, nunca el mes de la fecha documental."""
    activos = registros.exclude(estado='anulada')
    datos = {(r['cuenta_acumulado_id'], r['periodo_mes']): r['importe'] or ZERO
             for r in activos.values('cuenta_acumulado_id', 'periodo_mes').annotate(importe=Sum(sin_isv()))}
    filas = []
    for codigo, nombre in CuentaAcumuladoCompra.GRUPOS:
        filas.append(dict(tipo='grupo', nombre=nombre))
        grupo = [c for c in cuentas if c.grupo == codigo]
        for cuenta in grupo:
            filas.append(cuenta_fila(cuenta.nombre, [datos.get((cuenta.pk, m), ZERO) for m in range(1, 13)], cuenta))
        filas.append(cuenta_fila('Total ' + nombre.lower(),
                     [sum((datos.get((c.pk, m), ZERO) for c in grupo), ZERO) for m in range(1, 13)], tipo='subtotal'))
    filas.append(cuenta_fila('Por clasificar', [datos.get((None, m), ZERO) for m in range(1, 13)], tipo='pendiente'))
    mensual = {r['periodo_mes']: r for r in activos.values('periodo_mes').annotate(
        neto=Sum(sin_isv()), isv15=Sum('isv_15'), isv18=Sum('isv_18'), total=Sum('total'), documentos=Count('pk'))}
    for key, label in [('neto', 'Total compras sin ISV'), ('isv15', 'ISV 15%'), ('isv18', 'ISV 18%'), ('total', 'Total de los libros')]:
        filas.append(cuenta_fila(label, [mensual.get(m, {}).get(key, ZERO) for m in range(1, 13)], tipo='control'))
    diferencias = [mensual.get(m, {}).get('total', ZERO) - sum(
        (mensual.get(m, {}).get(k, ZERO) for k in ('neto', 'isv15', 'isv18')), ZERO) for m in range(1, 13)]
    if any(diferencias):
        filas.append(cuenta_fila('Diferencia de importes por revisar', diferencias, tipo='alerta'))
    return dict(filas=filas, pendientes=activos.filter(cuenta_acumulado__isnull=True).count(),
                documentos=activos.count(), diferencia=any(diferencias))


class CuentaForm(forms.ModelForm):
    class Meta:
        model = CuentaAcumuladoCompra
        fields = ('nombre', 'grupo', 'orden', 'activa')
        labels = {'nombre': 'Nombre de la cuenta', 'grupo': 'Grupo', 'orden': 'Orden', 'activa': 'Activa'}

    def __init__(self, *args, cliente, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.cliente_contable = cliente

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        if CuentaAcumuladoCompra.objects.filter(cliente_contable=self.instance.cliente_contable,
                nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe una cuenta con este nombre en el cliente.')
        return nombre


@login_required
@require_http_methods(['GET', 'POST'])
def acumulado_cliente(request, empresa_slug, cliente_id):
    empresa = empresa_contable(request, empresa_slug)
    cliente = cliente_autorizado(request, empresa, cliente_id)
    cuentas = CuentaAcumuladoCompra.objects.filter(cliente_contable=cliente)
    # Los demás clientes mantienen su resumen actual hasta habilitar su catálogo.
    if not cuentas.exists():
        if request.method != 'GET':
            raise PermissionDenied
        return selector_libros(request, empresa, cliente, acumulado=True)
    puede_editar = cliente.activo and request.user.tiene_permiso_erp('puede_editar_compras', empresa)
    try:
        anio = int(request.GET.get('anio') or timezone.localdate().year)
        mes = int(request.GET.get('mes') or 0)
        if not 1 <= anio <= 9999 or not 0 <= mes <= 12:
            raise ValueError
    except ValueError:
        raise Http404('Selecciona un año y mes válidos.')
    registros = RegistroCompraFiscal.objects.filter(empresa=empresa, cliente_contable=cliente, periodo_anio=anio)
    error = None
    if request.method == 'POST':
        if not puede_editar:
            raise PermissionDenied
        try:
            ids = request.POST.getlist('registros')
            if not ids or len(ids) > 100 or any(not n.isdigit() for n in ids):
                raise ValueError('Selecciona entre 1 y 100 facturas de la página.')
            ids = set(map(int, ids))
            with transaction.atomic():
                Empresa.objects.select_for_update().get(pk=empresa.pk)
                cliente = cliente_autorizado(request, empresa, cliente_id)
                if not cliente.activo or not request.user.tiene_permiso_erp('puede_editar_compras', empresa):
                    raise PermissionDenied
                destino = request.POST.get('cuenta_destino', '')
                if destino != 'pendiente' and not destino.isdigit():
                    raise ValueError('Selecciona la cuenta de destino.')
                cuenta = None if destino == 'pendiente' else get_object_or_404(cuentas, pk=destino, activa=True)
                elegidas = list(registros.select_for_update().filter(pk__in=ids).order_by('pk'))
                if len(elegidas) != len(ids):
                    raise ValueError('La selección contiene facturas ajenas a este cliente o año. No se guardó ningún cambio.')
                for compra in elegidas:
                    if compra.estado == 'anulada' or request.POST.get(f'version_{compra.pk}') != serializar(compra)['version']:
                        raise ValueError(f'La factura #{compra.pk} cambió o fue anulada. Actualiza la página y revisa la selección.')
                for compra in elegidas:
                    compra.cuenta_acumulado = cuenta
                    compra.clasificado_por = request.user
                    compra.clasificado_en = timezone.now()
                    compra.save(update_fields=['cuenta_acumulado', 'clasificado_por', 'clasificado_en'])
            messages.success(request, f'{len(elegidas)} facturas clasificadas. El acumulado se actualizó con sus importes existentes.')
            return redirect(request.get_full_path())
        except (ValueError, ValidationError) as exc:
            error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        except (IntegrityError, OperationalError):
            error = 'No se guardó la clasificación. Actualiza la página y reintenta cuando termine el otro guardado.'
    filtro_cuenta = request.GET.get('cuenta', 'pendiente')
    detalle = registros.exclude(estado='anulada').select_related('cuenta_acumulado', 'clasificado_por')
    if mes:
        detalle = detalle.filter(periodo_mes=mes)
    if filtro_cuenta == 'pendiente':
        detalle = detalle.filter(cuenta_acumulado__isnull=True)
    elif filtro_cuenta != 'todas':
        if not filtro_cuenta.isdigit():
            raise Http404
        cuenta = get_object_or_404(cuentas, pk=filtro_cuenta)
        detalle = detalle.filter(cuenta_acumulado=cuenta)
    proveedor = request.GET.get('proveedor', '').strip()
    if proveedor:
        detalle = detalle.filter(proveedor_nombre__icontains=proveedor)
    detalle = detalle.annotate(importe_acumulado=sin_isv()).order_by('periodo_mes', 'pk')
    pagina = Paginator(detalle, 100).get_page(request.GET.get('pagina'))
    for compra in pagina:
        compra.version_acumulado = serializar(compra)['version']
        compra.nombre_mes = MESES[compra.periodo_mes - 1]
    params = request.GET.copy()
    params.pop('pagina', None)
    cuenta_lista = list(cuentas)
    return render(request, 'facturacion/acumulado_cuentas.html', dict(
        empresa=empresa, cliente=cliente, anio=anio, mes=mes, meses=list(enumerate(MESES, 1)),
        cuentas=cuenta_lista, pagina=pagina, filtro_cuenta=filtro_cuenta, proveedor=proveedor,
        params=params.urlencode(), puede_editar=puede_editar, error=error,
        **construir_acumulado(registros, cuenta_lista)))


@login_required
@require_http_methods(['GET', 'POST'])
def cuentas_cliente(request, empresa_slug, cliente_id, cuenta_id=None):
    empresa = empresa_contable(request, empresa_slug)
    cliente = cliente_autorizado(request, empresa, cliente_id)
    cuentas = CuentaAcumuladoCompra.objects.filter(cliente_contable=cliente)
    if not cuentas.exists():
        raise Http404
    if not cliente.activo or not request.user.tiene_permiso_erp('puede_editar_compras', empresa):
        raise PermissionDenied
    cuenta = get_object_or_404(cuentas, pk=cuenta_id) if cuenta_id else None
    form = CuentaForm(request.POST or None, cliente=cliente, instance=cuenta)
    if request.method == 'POST':
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            cliente = cliente_autorizado(request, empresa, cliente_id)
            if not cliente.activo:
                raise PermissionDenied
            if form.is_valid():
                form.save()
                return redirect('cuentas_acumulado_cliente', empresa_slug=empresa_slug, cliente_id=cliente.pk)
    return render(request, 'facturacion/cuentas_acumulado.html', dict(
        empresa=empresa, cliente=cliente, cuentas=cuentas, form=form, cuenta=cuenta,
        anio=timezone.localdate().year))
