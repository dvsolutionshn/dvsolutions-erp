from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from core.onix_access import onix_disponible_para_empresa

from .models import SesionOnixMovil


def _error(mensaje, *, status, codigo):
    return JsonResponse(
        {"ok": False, "error": mensaje, "code": codigo},
        status=status,
    )


def autenticar_token_movil(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        autorizacion = request.headers.get("Authorization", "")
        esquema, separador, token = autorizacion.partition(" ")
        if not separador or esquema.lower() != "bearer" or not token.strip():
            return _error(
                "Inicia sesion nuevamente para continuar.",
                status=401,
                codigo="authentication_required",
            )

        sesion = (
            SesionOnixMovil.objects.select_related("usuario", "empresa")
            .filter(token_hash=SesionOnixMovil.calcular_hash(token.strip()))
            .first()
        )
        if not sesion or not sesion.activa:
            return _error(
                "La sesion vencio o fue cerrada. Inicia sesion nuevamente.",
                status=401,
                codigo="invalid_session",
            )

        usuario = sesion.usuario
        empresa = sesion.empresa
        if (
            not usuario.is_active
            or not empresa.licencia_operativa
            or (not usuario.is_superuser and not usuario.puede_acceder_empresa(empresa))
            or not onix_disponible_para_empresa(empresa)
        ):
            return _error(
                "Esta cuenta ya no tiene acceso a Onix en la empresa seleccionada.",
                status=403,
                codigo="access_revoked",
            )

        SesionOnixMovil.objects.filter(pk=sesion.pk).update(ultima_actividad=timezone.now())
        request.user = usuario
        request.empresa = empresa
        request.onix_mobile_session = sesion
        return view(request, *args, **kwargs)

    return wrapper

