from django.contrib import messages
from django.shortcuts import redirect

from core.access import (
    permiso_contabilidad_accion,
    permiso_contabilidad_desde_ruta,
    permiso_facturacion_accion,
    permiso_facturacion_desde_ruta,
    permiso_crm_desde_ruta,
    permiso_rrhh_desde_ruta,
)
from core.models import Empresa


class EmpresaAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info or ""
        parts = [part for part in path.split("/") if part]

        if len(parts) >= 3 and parts[1] == "dashboard" and parts[2] == "facturacion":
            empresa_slug = parts[0]
            empresa = Empresa.objects.filter(slug=empresa_slug, activa=True).first()
            if empresa and request.user.is_authenticated:
                if not request.user.is_superuser and request.user.empresa_id != empresa.id:
                    messages.error(request, "Tu usuario no pertenece a esta empresa.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not request.user.is_superuser and not empresa.licencia_operativa:
                    messages.error(request, "La licencia comercial de esta empresa esta suspendida o vencida.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not empresa.tiene_modulo_activo("facturacion"):
                    messages.error(request, "El modulo de facturacion no esta habilitado para esta empresa.")
                    return redirect("dashboard", slug=empresa.slug)

                if not request.user.is_superuser and not request.user.es_administrador_empresa:
                    suffix = "/".join(parts[3:])
                    permiso = permiso_facturacion_desde_ruta(suffix)
                    permiso_accion = permiso_facturacion_accion(suffix)
                    if permiso and not request.user.tiene_permiso_erp(permiso):
                        messages.error(request, "Tu rol no tiene permiso para entrar a esta seccion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if permiso_accion and not request.user.tiene_permiso_erp(permiso_accion):
                        messages.error(request, "Tu rol no tiene permiso para ejecutar esta accion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if not suffix and not request.user.tiene_alguna_permision_facturacion:
                        messages.error(request, "Tu rol no tiene acceso operativo al modulo de facturacion.")
                        return redirect("dashboard", slug=empresa.slug)

        if len(parts) >= 3 and parts[1] == "dashboard" and parts[2] == "contabilidad":
            empresa_slug = parts[0]
            empresa = Empresa.objects.filter(slug=empresa_slug, activa=True).first()
            if empresa and request.user.is_authenticated:
                if not request.user.is_superuser and request.user.empresa_id != empresa.id:
                    messages.error(request, "Tu usuario no pertenece a esta empresa.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not request.user.is_superuser and not empresa.licencia_operativa:
                    messages.error(request, "La licencia comercial de esta empresa esta suspendida o vencida.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not empresa.tiene_modulo_activo("contabilidad"):
                    messages.error(request, "El modulo de contabilidad no esta habilitado para esta empresa.")
                    return redirect("dashboard", slug=empresa.slug)

                if not request.user.is_superuser and not request.user.es_administrador_empresa:
                    suffix = "/".join(parts[3:])
                    permiso = permiso_contabilidad_desde_ruta(suffix)
                    permiso_accion = permiso_contabilidad_accion(suffix)
                    if permiso and not request.user.tiene_permiso_erp(permiso):
                        messages.error(request, "Tu rol no tiene permiso para entrar a esta seccion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if permiso_accion and not request.user.tiene_permiso_erp(permiso_accion):
                        messages.error(request, "Tu rol no tiene permiso para ejecutar esta accion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if not suffix and not request.user.tiene_alguna_permision_contabilidad:
                        messages.error(request, "Tu rol no tiene acceso operativo al modulo de contabilidad.")
                        return redirect("dashboard", slug=empresa.slug)

        if len(parts) >= 3 and parts[1] == "dashboard" and parts[2] == "rrhh":
            empresa_slug = parts[0]
            empresa = Empresa.objects.filter(slug=empresa_slug, activa=True).first()
            if empresa and request.user.is_authenticated:
                if not request.user.is_superuser and request.user.empresa_id != empresa.id:
                    messages.error(request, "Tu usuario no pertenece a esta empresa.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not request.user.is_superuser and not empresa.licencia_operativa:
                    messages.error(request, "La licencia comercial de esta empresa esta suspendida o vencida.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not empresa.tiene_modulo_activo("rrhh"):
                    messages.error(request, "El modulo de recursos humanos no esta habilitado para esta empresa.")
                    return redirect("dashboard", slug=empresa.slug)

                if not request.user.is_superuser and not request.user.es_administrador_empresa:
                    suffix = "/".join(parts[3:])
                    permiso = permiso_rrhh_desde_ruta(suffix)
                    if permiso and not request.user.tiene_permiso_erp(permiso):
                        messages.error(request, "Tu rol no tiene permiso para entrar a esta seccion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if not suffix and not request.user.tiene_alguna_permision_rrhh:
                        messages.error(request, "Tu rol no tiene acceso operativo al modulo de recursos humanos.")
                        return redirect("dashboard", slug=empresa.slug)

        if len(parts) >= 3 and parts[1] == "dashboard" and parts[2] == "crm":
            empresa_slug = parts[0]
            empresa = Empresa.objects.filter(slug=empresa_slug, activa=True).first()
            if empresa and request.user.is_authenticated:
                if not request.user.is_superuser and request.user.empresa_id != empresa.id:
                    messages.error(request, "Tu usuario no pertenece a esta empresa.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not request.user.is_superuser and not empresa.licencia_operativa:
                    messages.error(request, "La licencia comercial de esta empresa esta suspendida o vencida.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not empresa.tiene_modulo_activo("crm_marketing"):
                    messages.error(request, "El modulo CRM, Marketing y Agenda no esta habilitado para esta empresa.")
                    return redirect("dashboard", slug=empresa.slug)

                if not request.user.is_superuser and not request.user.es_administrador_empresa:
                    suffix = "/".join(parts[3:])
                    permiso = permiso_crm_desde_ruta(suffix)
                    if permiso and not request.user.tiene_permiso_erp(permiso):
                        messages.error(request, "Tu rol no tiene permiso para entrar a esta seccion.")
                        return redirect("dashboard", slug=empresa.slug)
                    if not suffix and not request.user.tiene_alguna_permision_crm:
                        messages.error(request, "Tu rol no tiene acceso operativo al modulo CRM.")
                        return redirect("dashboard", slug=empresa.slug)

        if len(parts) >= 3 and parts[1] == "dashboard" and parts[2] == "citas":
            empresa_slug = parts[0]
            empresa = Empresa.objects.filter(slug=empresa_slug, activa=True).first()
            if empresa and request.user.is_authenticated:
                if not request.user.is_superuser and request.user.empresa_id != empresa.id:
                    messages.error(request, "Tu usuario no pertenece a esta empresa.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not request.user.is_superuser and not empresa.licencia_operativa:
                    messages.error(request, "La licencia comercial de esta empresa esta suspendida o vencida.")
                    return redirect("empresa_login", slug=empresa.slug)

                if not empresa.tiene_modulo_activo("agenda_citas"):
                    messages.error(request, "El modulo de citas no esta habilitado para esta empresa.")
                    return redirect("dashboard", slug=empresa.slug)

                if not request.user.is_superuser and not request.user.es_administrador_empresa:
                    if not request.user.tiene_permiso_erp("puede_citas"):
                        messages.error(request, "Tu rol no tiene permiso para entrar a citas.")
                        return redirect("dashboard", slug=empresa.slug)

        return self.get_response(request)
