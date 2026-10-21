"""Presentación móvil del editor original de las cuatro empresas clínicas.

No activa configuración al consultar ni sustituye EmpresaControlForm.save.
Identidad, licencia, módulos y logo conservan sus reglas de superadministración.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from .access import interfaz_clinica_activa
from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, PlanComercial, PlanModulo


def es_empresa_app(request):
    # Detectar también app=1 fuera del alcance para denegar antes del Form.
    return "1" in request.GET.getlist("app") or "1" in request.POST.getlist("app")


def configuracion_empresa_disponible_app(request, empresa=None):
    usuario = request.user
    return bool(
        getattr(usuario, "is_authenticated", False) and usuario.is_superuser
        and (empresa is None or (
            empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        ))
    )


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de configuración no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador de configuración no válido.")
    return numero


def autorizar_empresa_app(request, empresa_id=None, campos=None):
    if not configuracion_empresa_disponible_app(request):
        raise PermissionDenied("La configuración de empresa es exclusiva de superadministradores.")
    metodos = {"GET", "HEAD", "POST"} if empresa_id is not None else {"GET", "HEAD"}
    if request.method not in metodos:
        raise PermissionDenied("Método no disponible en la configuración móvil.")
    if (
        set(request.GET) - {"app", "origen"}
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or ("app" in request.GET and request.GET.getlist("app") != ["1"])
        or ("origen" in request.GET and request.GET["origen"] not in EMPRESAS_CLINICAS_CON_ROLES)
    ):
        raise PermissionDenied("Parámetros de configuración de empresa inválidos.")
    if empresa_id is not None:
        identificador = _numero_canonico(empresa_id)
        segmentos = request.path.strip("/").split("/")
        if "empresas" in segmentos:
            indice = segmentos.index("empresas") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("Identificador de empresa no válido.")
    if set(request.FILES) - {"logo"} or any(len(request.FILES.getlist(clave)) != 1 for clave in request.FILES):
        raise PermissionDenied("La configuración sólo admite el archivo de logo.")
    if request.method == "POST":
        multiples = {"modulos_activos", "modulos_adicionales_visibles_clinica"}
        permitidos = {"app", "csrfmiddlewaretoken", "logo-clear", *(campos or {})}
        permitidos.update(f"initial-{nombre}" for nombre, campo in (campos or {}).items() if campo.show_hidden_initial)
        if (
            set(request.POST) - permitidos
            or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST if clave not in multiples)
            or request.POST.getlist("app") != ["1"]
        ):
            raise PermissionDenied("Datos de configuración de empresa inválidos.")
        for clave in multiples | {"plan_comercial"}:
            for valor in request.POST.getlist(clave):
                if valor:
                    _numero_canonico(valor)
    elif request.GET.getlist("app") != ["1"] or request.POST or request.FILES:
        raise PermissionDenied("La consulta de configuración requiere app=1.")


def validar_relaciones_empresa_app(request, empresa):
    if not configuracion_empresa_disponible_app(request, empresa):
        raise PermissionDenied("Esta empresa no pertenece a la configuración clínica móvil.")
    modulos = Modulo.objects.values("pk")
    if empresa.plan_comercial_id is not None and not PlanComercial.objects.filter(pk=empresa.plan_comercial_id).exists():
        raise PermissionDenied("La empresa contiene un plan comercial inexistente.")
    if EmpresaModulo.objects.filter(empresa_id=empresa.pk).exclude(modulo_id__in=modulos).exists():
        raise PermissionDenied("La empresa contiene un módulo inexistente.")
    configuracion = ConfiguracionAvanzadaEmpresa.objects.filter(empresa_id=empresa.pk).first()
    if configuracion is not None:
        relaciones = ConfiguracionAvanzadaEmpresa.modulos_adicionales_visibles_clinica.through.objects.filter(
            configuracionavanzadaempresa_id=configuracion.pk,
        )
        if relaciones.exclude(modulo_id__in=modulos).exists():
            raise PermissionDenied("La configuración contiene un módulo inexistente.")
    planes = {empresa.plan_comercial_id} if empresa.plan_comercial_id is not None else set()
    if request.method == "POST" and request.POST.get("plan_comercial"):
        # Un ID inexistente conserva el error ModelChoice del Form original.
        planes.add(int(request.POST["plan_comercial"]))
    if PlanModulo.objects.filter(plan_id__in=planes).exclude(modulo_id__in=modulos).exists():
        raise PermissionDenied("El plan contiene un módulo inexistente.")


def identidad_empresa_app(empresa):
    """URLs y encabezado confiables aun cuando ModelForm.clean mute instance."""
    return SimpleNamespace(
        pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre, slug=empresa.slug,
        tipo_solucion=empresa.tipo_solucion, activa=empresa.activa, logo=empresa.logo,
    )


def url_empresas_app(origen=""):
    parametros = {"app": "1"}
    if origen in EMPRESAS_CLINICAS_CON_ROLES:
        parametros["origen"] = origen
    return f"{reverse('superadmin_empresas_app')}?{urlencode(parametros)}"


def _contexto_empresa_app(empresa=None):
    modulos_url = (
        reverse("agenda_mobile_modules_screen", args=[empresa.slug])
        if empresa is not None and empresa.activa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) else ""
    )
    return {
        "empresa": empresa or SimpleNamespace(nombre="Configuración de empresas", logo=None),
        "modo_app": True, "app_home_disponible": False,
        "app_base_url": "", "app_modules_url": modulos_url,
        "can_view_app_modules": bool(modulos_url), "can_edit_company": True,
        "app_companies_url": url_empresas_app(empresa.slug if empresa else ""),
    }


def render_empresas_app(request):
    empresas = list(Empresa.objects.filter(
        slug__in=EMPRESAS_CLINICAS_CON_ROLES, tipo_solucion="clinica",
    ).order_by("nombre", "pk"))
    origen = next((empresa for empresa in empresas if empresa.slug == request.GET.get("origen")), None)
    contexto = {
        **_contexto_empresa_app(origen),
        "empresa": SimpleNamespace(nombre="Configuración de empresas", logo=None),
        "empresas": empresas, "total_empresas": len(empresas),
        "mobile_company_items": [
            {"empresa": empresa, "edit_url": f"{reverse('superadmin_empresa_edit', args=[empresa.pk])}?app=1"}
            for empresa in empresas
        ],
        "app_companies_back_url": url_empresas_app(origen.slug if origen else ""),
    }
    return respuesta_empresa_app(render(request, "core/mobile_company_list.html", contexto))


def render_empresa_form_app(request, form, empresa_original):
    form.fields["tipo_solucion"].widget.choices = form.fields["tipo_solucion"].choices
    for nombre in ("fecha_inicio_plan", "fecha_vencimiento_plan"):
        form.fields[nombre].widget.format = "%Y-%m-%d"
    grupos = (
        ("Identidad de la empresa", ("nombre", "tipo_solucion", "slug", "rtn", "logo")),
        ("Contacto y ubicación", ("correo", "telefono", "sitio_web", "direccion", "ciudad", "departamento", "pais")),
        ("Datos comerciales", ("slogan", "condiciones_pago", "observaciones_comerciales")),
        ("Plan, licencia y estado", ("plan_comercial", "estado_licencia", "fecha_inicio_plan", "fecha_vencimiento_plan", "activa")),
        ("Módulos habilitados", ("modulos_activos",)),
        ("Opciones clínicas y fiscales", ("permite_cai_historico", "permite_plantilla_factura_independiente", "manuales_pdf_habilitados", "modulos_adicionales_visibles_clinica")),
    )
    contexto = {
        **_contexto_empresa_app(empresa_original),
        "empresa_original": empresa_original, "form": form,
        "mobile_company_confirmation_name": empresa_original.nombre,
        "mobile_company_form_sections": [{"titulo": titulo, "campos": [form[nombre] for nombre in nombres]} for titulo, nombres in grupos],
        "app_company_form_url": f"{reverse('superadmin_empresa_edit', args=[empresa_original.pk])}?app=1",
        "app_company_back_url": url_empresas_app(empresa_original.slug),
    }
    return respuesta_empresa_app(render(request, "core/mobile_company_form.html", contexto))


def volver_empresas_app(request, empresa, empresa_original):
    if not configuracion_empresa_disponible_app(request, empresa):
        messages.warning(request, "La empresa se guardó con su configuración original. Su nuevo enlace o perfil queda fuera de las cuatro clínicas de esta app.")
    return respuesta_empresa_app(redirect(url_empresas_app(empresa_original.slug)))


def respuesta_empresa_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
