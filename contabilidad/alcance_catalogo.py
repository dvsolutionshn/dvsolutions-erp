"""La propuesta de catálogo especial pertenece exclusivamente a RUMI."""

SLUG_EMPRESA_CATALOGO_RUMI = "rumi"


def es_empresa_rumi(empresa):
    if not empresa or getattr(empresa, "slug", None) != SLUG_EMPRESA_CATALOGO_RUMI:
        return False
    if not getattr(empresa, "pk", None):
        return False
    from core.models import Empresa
    return Empresa.objects.using(empresa._state.db or "default").filter(
        pk=empresa.pk, slug=SLUG_EMPRESA_CATALOGO_RUMI,
    ).exists()
