from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def compra_importe(value):
    """Importes de revisión con coma de miles y punto decimal, sin usar float."""
    try:
        amount = Decimal(str(value))
        return format(amount, ',.2f') if amount.is_finite() else 'No disponible'
    except (InvalidOperation, ValueError, TypeError):
        return 'No disponible'

# ==========================================
# AGREGAR CLASE A INPUTS (YA LO TENÍAS)
# ==========================================
@register.filter(name='add_class')
def add_class(field, css):
    try:
        return field.as_widget(attrs={"class": css})
    except:
        return field  # evita error si no es un campo Django


# ==========================================
# SUMAR DESCUENTOS (NUEVO 🔥)
# ==========================================
@register.filter
def sum_descuento(lineas):
    total = 0
    for l in lineas:
        total += l.descuento_monto or 0
    return total
