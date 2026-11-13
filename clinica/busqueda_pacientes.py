import re

from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.db.models.lookups import Exact


def filtro_identidad_paciente(consulta):
    numero = re.sub(r"[\s-]+", "", consulta)
    if not numero.isascii() or not numero.isdecimal():
        return None
    identidad = Replace(Replace("identidad", Value("-"), Value("")), Value(" "), Value(""))
    return Q(Exact(identidad, Value(numero)))
