"""Captura rápida con validación de importes y catálogos por empresa."""
import json
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .catalogo_gastos_adicionales import productos_gastos_adicionales
from .catalogo_cirugias_gastos_adicionales import (
    cirugias_gastos_adicionales_choices,
    profesional_luis_gasto_adicional,
    profesionales_luis_gasto_adicional,
)
from .models import Paciente, ProfesionalSalud


def es_id_valido(valor):
    texto = str(valor)
    return texto.isascii() and texto.isdecimal() and len(texto) <= 18 and int(texto) > 0


class GastoAdicionalForm(forms.Form):
    paciente = forms.ModelChoiceField(queryset=Paciente.objects.none(), widget=forms.HiddenInput())
    fecha = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    profesional = forms.ModelChoiceField(
        queryset=ProfesionalSalud.objects.none(), required=False, disabled=True,
        widget=forms.HiddenInput(), label="Profesional",
    )
    tipo_cirugia = forms.ChoiceField(required=True, choices=(), label="Tipo de cirugía")
    observacion = forms.CharField(required=False, max_length=5000, label="Observación", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, empresa, gasto=None, **kwargs):
        super().__init__(*args, **kwargs)
        pacientes = Paciente.objects.filter(empresa=empresa, activo=True)
        if gasto:
            pacientes = Paciente.objects.filter(empresa=empresa).filter(Q(activo=True) | Q(pk=gasto.paciente_id))
        self.fields["paciente"].queryset = pacientes
        self.fields["tipo_cirugia"].choices = [("", "Seleccione el tipo de cirugía"), *cirugias_gastos_adicionales_choices()]
        self.initial.setdefault("tipo_cirugia", gasto.tipo_cirugia if gasto else "")
        self.fields["profesional"].queryset = profesionales_luis_gasto_adicional(empresa)
        profesional = profesional_luis_gasto_adicional(empresa)
        self.initial["profesional"] = profesional.pk if profesional else None


def validar_lineas_gasto(raw, empresa, *, gasto=None):
    """No aceptar totales del navegador, números no finitos ni productos ajenos."""
    try:
        datos = json.loads(raw or "[]")
    except (ValueError, TypeError):
        raise ValidationError("El detalle de productos no es válido. Vuelva a seleccionarlos.")
    if not isinstance(datos, list) or not 1 <= len(datos) <= 100:
        raise ValidationError("Agregue entre 1 y 100 líneas de productos.")
    ids = []
    for dato in datos:
        if not isinstance(dato, dict):
            raise ValidationError("Cada línea debe contener un producto, cantidad y precio.")
        valor = dato.get("producto_id")
        if not es_id_valido(valor):
            raise ValidationError("Seleccione un producto válido en cada línea.")
        ids.append(int(valor))
    productos = productos_gastos_adicionales(empresa, gasto=gasto).filter(pk__in=ids)
    por_id = {producto.pk: producto for producto in productos}
    cantidad_field = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    precio_field = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))
    monto_field = forms.DecimalField(max_digits=12, decimal_places=2)
    lineas, total = [], Decimal("0.00")
    for indice, (dato, producto_id) in enumerate(zip(datos, ids), start=1):
        producto = por_id.get(producto_id)
        if producto is None:
            raise ValidationError(f"Línea {indice}: el producto no está disponible en esta empresa.")
        try:
            cantidad = cantidad_field.clean(dato.get("cantidad"))
            precio = precio_field.clean(dato.get("precio_unitario"))
            subtotal = monto_field.clean((cantidad * precio).quantize(Decimal("0.01")))
        except ValidationError as exc:
            raise ValidationError([f"Línea {indice}: {mensaje}" for mensaje in exc.messages])
        total += subtotal
        lineas.append({"producto": producto, "cantidad": cantidad, "precio_unitario": precio})
    monto_field.clean(total)
    return lineas
