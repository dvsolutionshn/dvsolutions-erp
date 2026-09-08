"""Captura de documentos sobre el libro fiscal existente (piloto demo_1)."""
import re
import hashlib
import json
from datetime import datetime
from decimal import Decimal

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Replace, Trim
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import Empresa
from .models import CompraInventario, LibroCompraMensual, Proveedor, RegistroCompraFiscal


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

    def __init__(self, *args, empresa, registro=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.registro = registro
        disponibles = Q(activo=True)
        if registro:
            disponibles |= Q(pk=registro.proveedor_id)
            self.fields['proveedor'].required = bool(registro.proveedor_id)
        self.fields['proveedor'].queryset = Proveedor.objects.filter(disponibles, empresa=empresa)

    def clean_numero_factura(self):
        numero = self.cleaned_data['numero_factura']
        if self.registro and numero == self.registro.numero_factura:
            return numero
        if not re.fullmatch(r'(?:[0-9]{9,}|[0-9]{3}-[0-9]{3}-[0-9]{2}-[0-9]+)', numero):
            raise forms.ValidationError('Usa 3-3-2 dígitos y un correlativo, con o sin guiones.')
        numero = numero_normalizado(numero)
        formateado = f'{numero[:3]}-{numero[3:6]}-{numero[6:8]}-{numero[8:]}'
        if len(formateado) > 120:
            raise forms.ValidationError('El número de factura es demasiado largo.')
        return formateado


class ProveedorCapturaForm(forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ('nombre', 'rtn')

    def clean_rtn(self):
        rtn = numero_normalizado(self.cleaned_data.get('rtn') or '').replace(' ', '')
        if not re.fullmatch(r'[0-9]{1,20}', rtn):
            raise forms.ValidationError('Ingresa el RTN del proveedor usando solo dígitos.')
        return rtn


def crear_proveedor_captura(request, empresa):
    form = ProveedorCapturaForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'errores': form.errors}, status=400)
    try:
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            existentes = list(Proveedor.objects.filter(empresa=empresa).annotate(
                rtn_normalizado=Replace(sin_guiones('rtn'), Value(' '), Value(''))
            ).filter(rtn_normalizado=form.cleaned_data['rtn']).order_by('-activo', 'pk')[:2])
            if existentes:
                if not existentes[0].activo:
                    return JsonResponse({'error': 'Este RTN pertenece a un proveedor inactivo. Revisa su ficha en Proveedores.'}, status=409)
                if len(existentes) > 1 and existentes[1].activo:
                    return JsonResponse({'error': 'Hay varios proveedores con ese RTN. Selecciona el correcto en el buscador.'}, status=409)
                proveedor, creado = existentes[0], False
            else:
                proveedor = form.save(commit=False)
                proveedor.empresa = empresa
                proveedor.save()
                creado = True
    except forms.ValidationError as exc:
        return JsonResponse({'errores': {'__all__': exc.messages}}, status=400)
    except OperationalError as exc:
        if 'locked' not in str(exc).lower():
            raise
        return JsonResponse({'error': 'Hay otro guardado en curso. Reintenta; los datos se conservan.'}, status=503)
    return JsonResponse({'proveedor': {'id': proveedor.pk, 'nombre': proveedor.nombre, 'rtn': proveedor.rtn},
                         'creado': creado}, status=201 if creado else 200)


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
        corta = re.fullmatch(r'([0-9]{1,2})([/\-])([0-9]{1,2})\2([0-9]{2})', valor)
        if corta:
            valor = f'{corta[1]}/{corta[3]}/20{corta[4]}'
        for formato in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(valor, formato).date()
            except ValueError:
                pass
        raise forms.ValidationError('Fecha inválida. Usa día/mes/año, por ejemplo 5/8/26, 05/08/2026 o 050826.')

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


def buscar_duplicada(empresa, proveedor, numero, excluir=None):
    """Incluye todo el historial fiscal y de inventario, incluso anulaciones.

    El fallback SQL permite consultar registros antiguos sin modificarlos.
    Un nombre solo identifica registros históricos sin proveedor ni RTN.
    """
    normalizado = numero_normalizado(numero)
    rtn = numero_normalizado(proveedor.rtn or '').replace(' ', '')
    identidad = Q(proveedor_id=proveedor.pk) if proveedor.pk else Q(pk__in=[])
    if rtn:
        identidad |= Q(rtn_normalizado=rtn) | Q(rtn_proveedor_normalizado=rtn)
    identidad |= (Q(proveedor__isnull=True) & (Q(proveedor_rtn='') | Q(proveedor_rtn__isnull=True))
                  & Q(proveedor_nombre__iexact=proveedor.nombre.strip()))
    fiscal = (RegistroCompraFiscal.objects.filter(empresa=empresa).exclude(pk=excluir)
              .annotate(numero_comparable=sin_guiones('numero_factura'),
                        rtn_proveedor_normalizado=Replace(sin_guiones('proveedor__rtn'), Value(' '), Value('')),
                        rtn_normalizado=Replace(sin_guiones('proveedor_rtn'), Value(' '), Value('')))
              .filter(identidad).filter(Q(numero_factura_normalizado=normalizado) |
                                       Q(numero_comparable=normalizado)).first())
    if fiscal:
        return serializar(fiscal)
    identidad = Q(proveedor_id=proveedor.pk) if proveedor.pk else Q(pk__in=[])
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


MONTOS_LIBRO = ('exento', 'base_15', 'base_18', 'isv_15', 'isv_18', 'total')
MESES = ('Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto',
         'Septiembre', 'Octubre', 'Noviembre', 'Diciembre')


def serializar(registro):
    datos = dict(id=registro.pk, fecha=registro.fecha_documento.strftime('%d/%m/%Y'),
                 proveedor=registro.proveedor_nombre, proveedor_id=registro.proveedor_id,
                 numero=registro.numero_factura, estado=registro.get_estado_display(),
                 estado_codigo=registro.estado, exonerado=f'{registro.exonerado:.2f}',
                 periodo_anio=registro.periodo_anio, periodo_mes=registro.periodo_mes,
                 **{campo: f'{getattr(registro, campo):.2f}' for campo in MONTOS_LIBRO})
    datos['version'] = hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()
    return datos


def resumen_libro(registros):
    resumen = registros.exclude(estado='anulada').aggregate(
        documentos=Count('pk'), **{campo: Coalesce(Sum(campo), Decimal('0.00')) for campo in MONTOS_LIBRO})
    return {clave: valor.quantize(Decimal('0.01')) if clave in MONTOS_LIBRO else valor
            for clave, valor in resumen.items()}


def selector_libros(request, empresa):
    try:
        anio = int(request.GET.get('anio') or timezone.localdate().year)
        if not 1 <= anio <= 9999:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'Selecciona un año válido.'}, status=400)
    registros = RegistroCompraFiscal.objects.filter(empresa=empresa, periodo_anio=anio)
    acumulados = {fila['periodo_mes']: fila for fila in registros.exclude(estado='anulada')
                  .values('periodo_mes').annotate(documentos=Count('pk'), **{c: Sum(c) for c in MONTOS_LIBRO})}
    estados = dict(LibroCompraMensual.objects.filter(empresa=empresa, anio=anio).values_list('mes','estado'))
    meses = [dict(mes=i, nombre=nombre, estado=estados.get(i, 'en_proceso'),
                  resumen=acumulados.get(i, dict(documentos=0, **{c: Decimal('0.00') for c in MONTOS_LIBRO})))
             for i, nombre in enumerate(MESES, 1)]
    return render(request, 'facturacion/libros_captura_mensual.html', {
        'empresa': empresa, 'anio': anio, 'meses': meses, 'resumen': resumen_libro(registros)})


@login_required
@require_http_methods(['GET', 'POST'])
def captura_rapida(request, empresa_slug, anio=None, mes=None):
    if empresa_slug != 'demo_1':
        raise Http404
    if anio is not None and not (1 <= anio <= 9999 and 1 <= mes <= 12):
        raise Http404
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    usuario = request.user
    if not usuario.is_superuser and not usuario.puede_acceder_empresa(empresa):
        return JsonResponse({'error': 'No tienes acceso a esta empresa.'}, status=403)
    def puede(permiso):
        return usuario.is_superuser or usuario.es_administrador_empresa or usuario.tiene_permiso_erp(permiso, empresa)
    if not puede('puede_compras'):
        return JsonResponse({'error': 'No tienes permiso para consultar compras.'}, status=403)
    permisos = dict(crear=puede('puede_crear_compras'), editar=puede('puede_editar_compras'),
                    anular=puede('puede_anular_compras'))
    puede_crear_proveedor = puede('puede_crear_proveedores') and permisos['crear']
    accion = request.POST.get('accion', 'guardar') if request.method == 'POST' else request.GET.get('accion')
    if request.method == 'POST' and accion == 'crear_proveedor':
        if not puede_crear_proveedor:
            return JsonResponse({'error': 'No tienes permiso para crear proveedores.'}, status=403)
        return crear_proveedor_captura(request, empresa)
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
                                                               form.cleaned_data['numero_factura'],
                                                               excluir=request.GET.get('registro_id') if str(request.GET.get('registro_id','')).isdigit() else None)})
        if anio is None:
            return selector_libros(request, empresa)
        registros = RegistroCompraFiscal.objects.filter(empresa=empresa, periodo_anio=anio, periodo_mes=mes).order_by('pk')
        libro = LibroCompraMensual.objects.filter(empresa=empresa, anio=anio, mes=mes).first()
        cuadro = {'registros': [serializar(r) for r in registros], 'resumen': resumen_libro(registros),
                  'estado_libro': libro.estado if libro else 'en_proceso'}
        if accion == 'cuadro':
            return JsonResponse(cuadro)
        return render(request, 'facturacion/captura_rapida.html', {
            'empresa': empresa, 'puede_crear_proveedor': puede_crear_proveedor, 'permisos': permisos,
            'anio': anio, 'mes': mes, 'nombre_mes': MESES[mes-1], 'cuadro': cuadro})
    if anio is None:
        return JsonResponse({'error': 'Selecciona el año y mes del Libro de Compras antes de capturar.'}, status=400)
    permiso = {'guardar':'crear', 'editar':'editar', 'anular':'anular', 'estado':'editar'}.get(accion)
    if not permiso or not permisos[permiso]:
        return JsonResponse({'error': 'No tienes permiso para esta acción.'}, status=403)
    if accion == 'estado' and request.POST.get('estado') not in dict(LibroCompraMensual.ESTADOS):
        return JsonResponse({'error': 'Estado de libro inválido.'}, status=400)
    registro_id = None
    if accion in ('editar', 'anular'):
        try:
            registro_id = int(request.POST.get('registro_id', ''))
            if registro_id <= 0:
                raise ValueError
        except ValueError:
            return JsonResponse({'error': 'Selecciona una factura válida.'}, status=400)
    registros = RegistroCompraFiscal.objects.filter(empresa=empresa, periodo_anio=anio, periodo_mes=mes)
    original = get_object_or_404(registros, pk=registro_id) if registro_id else None
    form = CapturaForm(request.POST, empresa=empresa, registro=original) if accion in ('guardar','editar') else None
    if form and not form.is_valid():
        return JsonResponse({'errores': form.errors}, status=400)
    datos = form.cleaned_data if form else {}
    proveedor = datos.pop('proveedor', None)
    if form and not proveedor and original:
        proveedor = Proveedor(empresa=empresa, nombre=original.proveedor_nombre, rtn=original.proveedor_rtn)
    registro = None
    try:
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            libro, _ = LibroCompraMensual.objects.get_or_create(empresa=empresa, anio=anio, mes=mes)
            if accion == 'estado':
                libro.estado = request.POST['estado']
            else:
                if accion == 'guardar' and libro.estado == 'finalizado':
                    return JsonResponse({'error': 'Este libro está finalizado. Reábrelo para continuar capturando.'}, status=409)
                if registro_id:
                    registro = get_object_or_404(registros.select_for_update(), pk=registro_id)
                    if request.POST.get('version') != serializar(registro)['version']:
                        return JsonResponse({'error': 'La factura cambió en otra sesión. Actualiza el cuadro antes de editarla.'}, status=409)
                    if registro.estado == 'anulada':
                        return JsonResponse({'error': 'Esta factura ya está anulada.'}, status=409)
                if accion == 'anular':
                    registro.estado = 'anulada'
                    registro.save(update_fields=['estado'])
                else:
                    duplicada = buscar_duplicada(empresa, proveedor, datos['numero_factura'], excluir=registro_id)
                    if duplicada:
                        return JsonResponse({'duplicada': duplicada}, status=409)
                    if registro is None:
                        registro = RegistroCompraFiscal(empresa=empresa, creado_por=usuario, origen_importacion='Captura rápida')
                    for campo, valor in datos.items():
                        setattr(registro, campo, valor)
                    # Preserva los importes exonerados históricos, no capturados en las nueve columnas.
                    registro.subtotal += registro.exonerado
                    registro.total += registro.exonerado
                    registro.proveedor = proveedor if proveedor.pk else None
                    registro.proveedor_nombre, registro.proveedor_rtn = proveedor.nombre, proveedor.rtn
                    registro.numero_factura_normalizado = numero_normalizado(datos['numero_factura'])
                    rtn = numero_normalizado(proveedor.rtn or '').replace(' ', '')
                    registro.identidad_captura = f'rtn:{rtn}' if rtn else (f'proveedor:{proveedor.pk}' if proveedor.pk else None)
                    registro.periodo_anio, registro.periodo_mes = anio, mes
                    registro.save()
            libro.actualizado_por = usuario
            libro.save()
    except (IntegrityError, forms.ValidationError) as exc:
        duplicada = buscar_duplicada(empresa, proveedor, datos['numero_factura'], excluir=registro_id) if proveedor else None
        if duplicada:
            return JsonResponse({'duplicada': duplicada}, status=409)
        if isinstance(exc, forms.ValidationError):
            return JsonResponse({'errores': getattr(exc, 'message_dict', {'__all__': exc.messages})}, status=400)
        raise
    except OperationalError as exc:
        if 'locked' not in str(exc).lower():
            raise
        return JsonResponse({'error': 'Hay otro guardado en curso. Reintenta; la fila se conserva.'}, status=503)
    return JsonResponse({'registro': serializar(registro) if registro else None,
                         'resumen': resumen_libro(registros), 'estado_libro': libro.estado},
                        status=201 if accion == 'guardar' else 200)
