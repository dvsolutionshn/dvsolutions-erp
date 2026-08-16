from django.conf import settings


def onix_disponible_para_empresa(empresa):
    """Limita el piloto de Onix a las empresas autorizadas por configuracion."""
    if empresa is None:
        return False

    slugs = getattr(settings, "ONIX_ALLOWED_COMPANY_SLUGS", ["demo_1"])
    if isinstance(slugs, str):
        slugs = [slug.strip() for slug in slugs.split(",") if slug.strip()]

    slugs_autorizados = set(slugs)
    return "*" in slugs_autorizados or empresa.slug in slugs_autorizados
