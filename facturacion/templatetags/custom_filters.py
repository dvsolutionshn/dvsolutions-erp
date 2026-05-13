from django import template

register = template.Library()

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