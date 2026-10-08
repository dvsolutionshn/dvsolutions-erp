"""Selecciones clínicas existentes para la captura de Gastos Adicionales."""
import re
import unicodedata


def cirugias_gastos_adicionales_choices():
    # El formulario clínico importa modelos: acceder al catálogo después de
    # cargar las aplicaciones evita introducir un ciclo durante su importación.
    from .forms import PROCEDIMIENTOS_GENERALES_GRUPOS

    return [
        (grupo, [(codigo, etiqueta.split("(", 1)[0].strip()) for codigo, etiqueta in opciones])
        for grupo, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS[:4]
    ]


def nombre_cirugia_gasto_adicional(codigo):
    return next(
        (etiqueta for _, opciones in cirugias_gastos_adicionales_choices() for clave, etiqueta in opciones if clave == codigo),
        "",
    )


def _tokens_profesional(nombre):
    normalizado = unicodedata.normalize("NFKD", str(nombre or ""))
    normalizado = normalizado.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z]+", normalizado)


def profesionales_luis_gasto_adicional(empresa):
    from .models import ProfesionalSalud

    activos = ProfesionalSalud.objects.filter(empresa=empresa, activo=True)
    ids = [profesional.pk for profesional in activos if "luis" in _tokens_profesional(profesional.nombre)]
    return ProfesionalSalud.objects.filter(empresa=empresa, activo=True, pk__in=ids)


def profesional_luis_gasto_adicional(empresa):
    candidatos = list(profesionales_luis_gasto_adicional(empresa))
    canonicos = [
        profesional for profesional in candidatos
        if any(token.startswith("gonzal") for token in _tokens_profesional(profesional.nombre))
    ]
    # El nombre visible corresponde al doctor de la agenda clínica. Otro
    # profesional llamado Luis no debe recibir ese vínculo por coincidencia.
    return canonicos[0] if len(canonicos) == 1 else None
