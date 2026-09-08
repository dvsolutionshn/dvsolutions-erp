"""Captura de documentos sobre el libro fiscal existente (piloto demo_1)."""
import re
from datetime import datetime
from decimal import Decimal

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Replace, Trim
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from core.models import Empresa
from .models import CompraInventario, Proveedor, RegistroCompraFiscal


def calcular_importes(exento, base_15, base_18):
    isv_15 = (base_15 * Decimal('0.15')).quantize(Decimal('0.01'))
    isv_18 = (base_18 * Decimal('0.18')).quantize(Decimal('0.01'))
    subtotal = exento + base_15 + base_18
    return dict(subtotal=subtotal, isv_15=isv_15, isv_18=isv_18,
                total=subtotal + isv_15 + isv_18)


def numero_normalizado(numero):
    return numero.strip().replace('-', '')


class IdentidadForm(forms.Form):
    proveedor = forms.ModelChoiceField(queryset=Proveedor.objects.none())
    numero_factura = forms.CharField(max_length=120)

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proveedor'].queryset = Proveedor.objects.filter(empresa=empresa, activo=True)

    def clean_numero_factura(self):
        numero = self.cleaned_data['numero_factura']
        if not re.fullmatch(r'(?:[0-9]{9,}|[0-9]{3}-[0-9]{3}-[0-9]{2}-[0-9]+)', numero):
            raise forms.ValidationError('Usa 3-3-2 dígitos y un correlativo, con o sin guiones.')
        numero = numero_normalizado(numero)
        formateado = f'{numero[:3]}-{numero[3:6]}-{numero[6:8]}-{numero[8:]}'
        if len(formateado) > 120:
            raise forms.ValidationError('El número de factura es demasiado largo.')
        return formateado


class CapturaForm(IdentidadForm):
    fecha_documento = forms.CharField()
    exento = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    base_15 = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    base_18 = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)

    def clean_fecha_documento(self):
        valor = self.cleaned_data['fecha_documento']
        # Año explícito 20YY; jamás se infiere el mes de trabajo.
        if re.fullmatch(r'[0-9]{6}', valor):
            valor = f'{valor[:2]}/{valor[2:4]}/20{valor[4:]}'
        for formato in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(valor, formato).date()
            except ValueError:
                pass
        raise forms.ValidationError('Fecha inválida. Usa DDMMAA o DD/MM/AAAA.')

    def clean(self):
        datos = super().clean()
        if self.errors:
            return datos
        for campo in ('exento', 'base_15', 'base_18'):
            datos[campo] = datos[campo] or Decimal('0.00')
        datos.update(calcular_importes(datos['exento'], datos['base_15'], datos['base_18']))
        if datos['total'] <= 0:
            raise forms.ValidationError('El total debe ser mayor que cero.')
        if datos['total'] >= Decimal('1000000000000'):
            raise forms.ValidationError('El total excede el importe permitido.')
        return datos


def sin_guiones(campo):
    return Replace(Trim(campo), Value('-'), Value(''))


def buscar_duplicada(empresa, proveedor, numero):
    """Incluye todo el historial fiscal y de inventario, incluso anulaciones.

    El fallback SQL permite consultar registros antiguos sin modificarlos.
    Un nombre solo identifica registros históricos sin proveedor ni RTN.
    """
    normalizado = numero_normalizado(numero)
    rtn = numero_normalizado(proveedor.rtn or '').replace(' ', '')
    identidad = Q(proveedor=proveedor)
    if rtn:
        identidad |= Q(rtn_normalizado=rtn) | Q(rtn_proveedor_normalizado=rtn)
    identidad |= (Q(proveedor__isnull=True) & (Q(proveedor_rtn='') | Q(proveedor_rtn__isnull=True))
                  & Q(proveedor_nombre__iexact=proveedor.nombre.strip()))
    fiscal = (RegistroCompraFiscal.objects.filter(empresa=empresa)
              .annotate(numero_comparable=sin_guiones('numero_factura'),
                        rtn_proveedor_normalizado=Replace(sin_guiones('proveedor__rtn'), Value(' '), Value('')),
                        rtn_normalizado=Replace(sin_guiones('proveedor_rtn'), Value(' '), Value('')))
              .filter(identidad).filter(Q(numero_factura_normalizado=normalizado) |
                                       Q(numero_comparable=normalizado)).first())
    if fiscal:
        return serializar(fiscal)
    identidad = Q(proveedor=proveedor)
    if rtn:
        identidad |= Q(rtn_normalizado=rtn)
    identidad |= Q(proveedor__isnull=True, proveedor_nombre__iexact=proveedor.nombre.strip())
    compra = (CompraInventario.objects.filter(empresa=empresa)
              .annotate(numero_comparable=sin_guiones('referencia_documento'),
                        rtn_normalizado=Replace(sin_guiones('proveedor__rtn'), Value(' '), Value('')))
              .filter(identidad, numero_comparable=normalizado).first())
    if compra:
        return dict(fecha=compra.fecha_documento.strftime('%d/%m/%Y'), proveedor=compra.proveedor_nombre,
                    numero=compra.referencia_documento, total=str(compra.total_documento),
                    estado=compra.get_estado_display())


def serializar(registro):
    return dict(id=registro.pk, fecha=registro.fecha_documento.strftime('%d/%m/%Y'),
                proveedor=registro.proveedor_nombre, numero=registro.numero_factura,
                total=str(registro.total), estado=registro.get_estado_display())


@login_required
@require_http_methods(['GET', 'POST'])
def captura_rapida(request, empresa_slug):
    if empresa_slug != 'demo_1':
        raise Http404
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    usuario = request.user
    if not usuario.is_superuser and not usuario.puede_acceder_empresa(empresa):
        return JsonResponse({'error': 'No tienes acceso a esta empresa.'}, status=403)
    if not (usuario.is_superuser or usuario.es_administrador_empresa or
            (usuario.tiene_permiso_erp('puede_compras', empresa) and
             usuario.tiene_permiso_erp('puede_crear_compras', empresa))):
        return JsonResponse({'error': 'No tienes permiso para crear compras.'}, status=403)
    if request.method == 'GET':
        accion = request.GET.get('accion')
        if accion == 'proveedores':
            # Subconsultas independientes evitan multiplicar ambas colecciones de compras.
            fiscales = (RegistroCompraFiscal.objects.filter(empresa=empresa, proveedor_id=OuterRef('pk'))
                        .order_by().values('proveedor_id').annotate(n=Count('pk')).values('n'))
            inventario = (CompraInventario.objects.filter(empresa=empresa, proveedor_id=OuterRef('pk'))
                          .order_by().values('proveedor_id').annotate(n=Count('pk')).values('n'))
            proveedores = (Proveedor.objects.filter(empresa=empresa, activo=True)
                           .filter(Q(nombre__icontains=request.GET.get('q', '').strip()) |
                                   Q(rtn__icontains=request.GET.get('q', '').strip()))
                           .annotate(frecuencia=Coalesce(Subquery(fiscales), 0) + Coalesce(Subquery(inventario), 0))
                           .order_by('-frecuencia', 'nombre', 'pk')[:15])
            return JsonResponse({'proveedores': [dict(id=p.pk, nombre=p.nombre, rtn=p.rtn or '')
                                                for p in proveedores]})
        if accion == 'duplicado':
            form = IdentidadForm(request.GET, empresa=empresa)
            if not form.is_valid():
                return JsonResponse({'errores': form.errors}, status=400)
            return JsonResponse({'duplicada': buscar_duplicada(empresa, form.cleaned_data['proveedor'],
                                                               form.cleaned_data['numero_factura'])})
        return render(request, 'facturacion/captura_rapida.html', {'empresa': empresa})
    form = CapturaForm(request.POST, empresa=empresa)
    if not form.is_valid():
        return JsonResponse({'errores': form.errors}, status=400)
    datos = form.cleaned_data
    proveedor = datos.pop('proveedor')
    normalizado = numero_normalizado(datos['numero_factura'])
    rtn = numero_normalizado(proveedor.rtn or '').replace(' ', '')
    try:
        with transaction.atomic():
            # Serializa los envíos del piloto; la restricción única también protege SQLite.
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            duplicada = buscar_duplicada(empresa, proveedor, datos['numero_factura'])
            if duplicada:
                return JsonResponse({'duplicada': duplicada}, status=409)
            registro = RegistroCompraFiscal(
                empresa=empresa, proveedor=proveedor, proveedor_nombre=proveedor.nombre,
                proveedor_rtn=proveedor.rtn, creado_por=usuario,
                numero_factura_normalizado=normalizado,
                identidad_captura=f'rtn:{rtn}' if rtn else f'proveedor:{proveedor.pk}',
                origen_importacion='Captura rápida', **datos)
            registro.save()  # Conserva full_clean, periodo fiscal y validaciones del modelo.
    except IntegrityError:
        duplicada = buscar_duplicada(empresa, proveedor, datos['numero_factura'])
        if duplicada:
            return JsonResponse({'duplicada': duplicada}, status=409)
        raise
    except forms.ValidationError as exc:
        # full_clean también puede detectar el envío concurrente antes del INSERT.
        duplicada = buscar_duplicada(empresa, proveedor, datos['numero_factura'])
        if duplicada:
            return JsonResponse({'duplicada': duplicada}, status=409)
        return JsonResponse({'errores': getattr(exc, 'message_dict', {'__all__': exc.messages})}, status=400)
    except OperationalError as exc:
        if 'locked' not in str(exc).lower():
            raise
        return JsonResponse({'error': 'Hay otro guardado en curso. Reintenta; la fila se conserva.'}, status=503)
    return JsonResponse({'registro': serializar(registro)}, status=201)
