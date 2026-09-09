"""Contexto explícito y administración de clientes contables de Dubón."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import Empresa, Usuario
from .models import ClienteContable, Proveedor


def es_admin(usuario):
    return usuario.is_superuser or usuario.es_administrador_empresa


def empresa_contable(request, empresa_slug):
    if empresa_slug != 'dubon_asociados':
        raise Http404
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    if not request.user.is_authenticated or not request.user.puede_acceder_empresa(empresa):
        raise PermissionDenied
    if not request.user.tiene_permiso_erp('puede_compras', empresa):
        raise PermissionDenied
    return empresa


def clientes_visibles(usuario, empresa):
    clientes = ClienteContable.objects.filter(empresa=empresa)
    return clientes if es_admin(usuario) else clientes.filter(activo=True, usuarios=usuario)


def cliente_autorizado(request, empresa, cliente_id):
    # No confiar en sesiones ni parámetros POST para establecer el contexto.
    return get_object_or_404(clientes_visibles(request.user, empresa), pk=cliente_id)


class ClienteContableForm(forms.ModelForm):
    class Meta:
        model = ClienteContable
        fields = ('nombre', 'razon_social', 'rtn', 'activo', 'usuarios')
        labels = {'razon_social': 'Razón social', 'rtn': 'RTN', 'usuarios': 'Usuarios asignados'}
        widgets = {'usuarios': forms.CheckboxSelectMultiple()}

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.empresa = empresa
        self.fields['usuarios'].queryset = Usuario.objects.filter(
            Q(empresa=empresa) | Q(empresas_acceso=empresa), is_active=True).distinct().order_by('username')

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre'].strip()
        if ClienteContable.objects.filter(empresa=self.instance.empresa, nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un cliente contable con este nombre.')
        return nombre


@login_required
@require_http_methods(['GET', 'POST'])
def clientes_contables(request, empresa_slug):
    empresa = empresa_contable(request, empresa_slug)
    if request.method != 'GET':
        raise PermissionDenied
    return render(request, 'facturacion/clientes_contables.html', {
        'empresa': empresa, 'clientes': clientes_visibles(request.user, empresa).prefetch_related('usuarios'), 'administrar': es_admin(request.user)})


@login_required
@require_http_methods(['GET', 'POST'])
def editar_cliente_contable(request, empresa_slug, cliente_id=None):
    empresa = empresa_contable(request, empresa_slug)
    if not es_admin(request.user):
        raise PermissionDenied
    cliente = get_object_or_404(ClienteContable, empresa=empresa, pk=cliente_id) if cliente_id else None
    form = ClienteContableForm(request.POST or None, empresa=empresa, instance=cliente)
    if request.method == 'POST':
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            if form.is_valid():
                guardado = form.save()
                messages.success(request, f'Cliente {guardado.nombre}: datos y usuarios asignados guardados correctamente.')
                return redirect('clientes_contables', empresa_slug=empresa_slug)
    return render(request, 'facturacion/cliente_contable_form.html', {'empresa': empresa, 'form': form, 'cliente': cliente})


@login_required
@require_http_methods(['GET', 'POST'])
def proveedores_cliente(request, empresa_slug, cliente_id, proveedor_id=None):
    from .captura_rapida import ProveedorCapturaForm, sin_guiones
    from django.db.models import Value
    from django.db.models.functions import Replace
    empresa = empresa_contable(request, empresa_slug)
    cliente = cliente_autorizado(request, empresa, cliente_id)
    proveedores = Proveedor.objects.filter(empresa=empresa, cliente_contable=cliente)
    proveedor = get_object_or_404(proveedores, pk=proveedor_id) if proveedor_id else None
    permiso = 'puede_editar_proveedores' if proveedor else 'puede_crear_proveedores'
    puede_guardar = cliente.activo and request.user.tiene_permiso_erp(permiso, empresa)

    class ProveedorClienteForm(ProveedorCapturaForm):
        class Meta(ProveedorCapturaForm.Meta):
            fields = ('nombre', 'rtn', 'activo')

        def clean_rtn(self):
            rtn = super().clean_rtn()
            existentes = proveedores.annotate(normalizado=Replace(sin_guiones('rtn'),Value(' '),Value('')))
            if existentes.filter(normalizado=rtn).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('Ya existe un proveedor con ese RTN en este cliente.')
            return rtn

    form = ProveedorClienteForm(request.POST or None, instance=proveedor or Proveedor(empresa=empresa,cliente_contable=cliente))
    if request.method == 'POST':
        if not puede_guardar:
            raise PermissionDenied
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            if form.is_valid():
                form.save()
                return redirect('proveedores_cliente_contable', empresa_slug=empresa_slug, cliente_id=cliente.pk)
    q = request.GET.get('q','').strip()
    return render(request, 'facturacion/proveedores_cliente_contable.html', {
        'empresa': empresa, 'cliente': cliente, 'proveedores': proveedores.filter(Q(nombre__icontains=q)|Q(rtn__icontains=q)).order_by('nombre'),
        'form': form, 'puede_guardar': puede_guardar, 'puede_editar': cliente.activo and request.user.tiene_permiso_erp('puede_editar_proveedores',empresa), 'q': q})
