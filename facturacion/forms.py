from django import forms
from .models import PagoFactura

class PagoFacturaForm(forms.ModelForm):
    class Meta:
        model = PagoFactura
        fields = ['monto', 'metodo', 'referencia']