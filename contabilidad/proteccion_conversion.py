"""Conserva las fuentes de una revisión de conversión documentada."""
from django.core.exceptions import ValidationError
from django.db.models import Q


def asiento_tiene_revision(asiento_id, using="default"):
    from .models import RevisionConversionAsiento
    return bool(asiento_id) and RevisionConversionAsiento.objects.using(using).filter(
        Q(origen_id=asiento_id) | Q(ajuste_id=asiento_id)
    ).exists()


def validar_asiento_revisado(asiento, using="default", eliminar=False):
    if not asiento_tiene_revision(asiento.pk, using):
        return
    campos = [campo.attname for campo in asiento._meta.concrete_fields if not campo.primary_key]
    anterior = type(asiento).objects.using(using).filter(pk=asiento.pk).values(*campos).first()
    if eliminar or (anterior and any(anterior[campo] != getattr(asiento, campo) for campo in campos)):
        raise ValidationError("El asiento tiene una revisión integral documentada. Conserve su respaldo y registre un nuevo ajuste para corregirlo.")


def validar_partida_revisada(asiento_ids, using="default"):
    if any(asiento_tiene_revision(pk, using) for pk in set(asiento_ids) if pk):
        raise ValidationError("La partida respalda una revisión integral de moneda. Conserve sus importes y su cuenta originales.")
