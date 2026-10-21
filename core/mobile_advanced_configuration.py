"""Presentación clínica del ModelAdmin original de configuración avanzada.

Las rutas Core sólo consultan. El formulario se envía al add/change canónico
del administrador: no hay otro formulario, validador ni servicio de guardado.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .access import interfaz_clinica_activa
from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .models import ConfiguracionAvanzadaEmpresa, Empresa, Modulo


CAMPOS_AVANZADOS_APP = (
    "empresa", "usa_cierre_caja", "usa_pagos_mixtos", "usa_reporte_bancos",
    "usa_inventario_farmaceutico", "usa_control_lotes_fefo", "manuales_pdf_habilitados",
    "usa_bodegas_internas", "ventas_solo_desde_vitrina", "permite_cai_historico",
    "permite_plantilla_factura_independiente", "modulos_adicionales_visibles_clinica",
    "bodega_venta_predeterminada", "notas",
)


def es_configuracion_avanzada_app(request):
    return "1" in request.GET.getlist("app") or "1" in request.POST.getlist("app")


def _administrador():
    return admin.site.get_model_admin(ConfiguracionAvanzadaEmpresa)


def configuracion_avanzada_disponible_app(request, empresa=None):
    if not (
        settings.ENABLE_DJANGO_ADMIN and admin.site.has_permission(request)
        and (empresa is None or (
            empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        ))
    ):
        return dict.fromkeys(("ver", "editar", "crear"), False)
    administrador = _administrador()
    return {
        "ver": administrador.has_view_or_change_permission(request),
        "editar": administrador.has_change_permission(request),
        "crear": administrador.has_add_permission(request),
    }


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de configuración avanzada no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or texto != str(numero):
        raise PermissionDenied("Identificador de configuración avanzada no válido.")
    return numero


def _autorizar_consulta(request, empresa_id=None):
    if request.method not in {"GET", "HEAD"} or request.POST or request.FILES:
        raise PermissionDenied("Esta ruta de configuración avanzada sólo permite consultar.")
    if set(request.GET) - {"app"} or request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("La consulta de configuración avanzada requiere app=1.")
    if not any(configuracion_avanzada_disponible_app(request).values()):
        raise PermissionDenied("No tiene permisos de administrador para configuración avanzada.")
    if empresa_id is not None:
        identificador = _numero_canonico(empresa_id)
        segmentos = request.path.strip("/").split("/")
        if "empresas" in segmentos:
            indice = segmentos.index("empresas") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("Identificador de empresa no válido.")


def _empresa(empresa_id):
    empresa = get_object_or_404(Empresa.objects.only("pk", "nombre", "slug", "tipo_solucion", "activa"), pk=empresa_id)
    if empresa.slug not in EMPRESAS_CLINICAS_CON_ROLES or not interfaz_clinica_activa(empresa):
        raise PermissionDenied("Esta empresa no pertenece a las cuatro clínicas de la app.")
    return empresa


def _identidad(empresa):
    return SimpleNamespace(pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre,
        slug=empresa.slug, tipo_solucion=empresa.tipo_solucion, activa=empresa.activa, logo=None)


def _validar_modulos(configuracion_id):
    relaciones = ConfiguracionAvanzadaEmpresa.modulos_adicionales_visibles_clinica.through.objects.filter(
        configuracionavanzadaempresa_id=configuracion_id,
    )
    if relaciones.exclude(modulo_id__in=Modulo.objects.values("pk")).exists():
        raise PermissionDenied("La configuración contiene un módulo inexistente.")


def _url_presentador(empresa_id):
    return f"{reverse('configuracion_avanzada_empresa_app', args=[empresa_id])}?app=1"


def _url_formulario(administrador, empresa_id, configuracion_id=None):
    nombre = f"admin:{administrador.opts.app_label}_{administrador.opts.model_name}_"
    url = reverse(nombre + ("add" if configuracion_id is None else "change"),
        args=[] if configuracion_id is None else [configuracion_id], current_app=administrador.admin_site.name)
    return f"{url}?{urlencode({'app': '1', 'empresa': empresa_id})}"


def _contexto(request, empresa=None, *, configuracion_existe=False, capacidades=None):
    capacidades = capacidades or configuracion_avanzada_disponible_app(request, empresa)
    # El derecho Django es global; no concede pertenencia a la navegación ERP.
    modulos_url = ""
    if empresa is not None and empresa.activa and request.user.puede_acceder_empresa(empresa):
        from crm.views import _proteger_agenda_mobile
        from .mobile_users import usuarios_disponibles_app

        if _proteger_agenda_mobile(request, empresa) is None or usuarios_disponibles_app(request, empresa):
            if request.user.is_superuser or empresa.licencia_operativa or usuarios_disponibles_app(request, empresa):
                modulos_url = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    picker = f"{reverse('configuracion_avanzada_empresas_app')}?app=1"
    return {
        "empresa": _identidad(empresa) if empresa else SimpleNamespace(nombre="Configuración avanzada", logo=None),
        "empresa_original": _identidad(empresa) if empresa else None,
        "modo_app": True, "app_home_disponible": False, "app_base_url": "",
        "app_modules_url": modulos_url, "can_view_app_modules": bool(modulos_url),
        "app_advanced_picker_url": picker, "app_advanced_back_url": picker,
        "configuracion_existe": configuracion_existe,
        "can_view_advanced": capacidades["ver"], "can_change_advanced": capacidades["editar"],
        "can_add_advanced": capacidades["crear"], "mobile_advanced_readonly": not capacidades["editar"],
        "mobile_advanced_confirmation_name": empresa.nombre if empresa else "",
        "app_advanced_add_url": _url_presentador(empresa.pk) if empresa and not configuracion_existe and capacidades["crear"] else "",
        "app_advanced_form_url": "", "mobile_advanced_sections": [], "mobile_advanced_values": [],
    }


def preparar_cambio_avanzado_app(administrador, request, object_id):
    if not settings.ENABLE_DJANGO_ADMIN or not administrador.admin_site.has_permission(request):
        raise PermissionDenied("El administrador no está disponible para este usuario.")
    if request.method not in {"GET", "HEAD", "POST"} or request.FILES:
        raise PermissionDenied("Método no disponible en la configuración avanzada móvil.")
    estado = getattr(request, "_mobile_advanced_state", None)
    claves_get = {"app"} if estado is not None else {"app", "empresa"}
    if (
        set(request.GET) - claves_get
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros de configuración avanzada inválidos.")
    if estado is None:
        if len(request.GET.getlist("empresa")) != 1:
            raise PermissionDenied("La configuración avanzada requiere una empresa explícita.")
        empresa = _empresa(_numero_canonico(request.GET["empresa"]))
    else:
        empresa = estado["empresa"]
    configuracion_id = _numero_canonico(object_id) if object_id is not None else None
    if configuracion_id is not None:
        segmentos = request.path.strip("/").split("/")
        if segmentos and segmentos[-1] == "change" and _numero_canonico(segmentos[-2]) != configuracion_id:
            raise PermissionDenied("Identificador de configuración no válido.")
        configuracion = get_object_or_404(ConfiguracionAvanzadaEmpresa.objects.only("pk", "empresa_id"),
            pk=configuracion_id, empresa_id=empresa.pk)
        if request.method == "POST":
            permitido = administrador.has_change_permission(request, configuracion)
        else:
            permitido = administrador.has_view_or_change_permission(request, configuracion)
        if not permitido:
            raise PermissionDenied("No tiene permiso para esta configuración avanzada.")
        _validar_modulos(configuracion_id)
    elif not administrador.has_add_permission(request):
        raise PermissionDenied("No tiene permiso para agregar configuración avanzada.")
    if request.method == "POST":
        multiples = {"modulos_adicionales_visibles_clinica"}
        permitidos = set(CAMPOS_AVANZADOS_APP) | {"app", "csrfmiddlewaretoken", "_save"}
        if (
            set(request.POST) - permitidos
            or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST if clave not in multiples)
            or request.POST.getlist("app") != ["1"]
            or len(request.POST.getlist("empresa")) != 1
            or _numero_canonico(request.POST.get("empresa", "")) != empresa.pk
        ):
            raise PermissionDenied("Sólo se puede guardar la configuración de la empresa seleccionada.")
        for valor in request.POST.getlist("modulos_adicionales_visibles_clinica"):
            if valor:
                _numero_canonico(valor)
    elif request.POST:
        raise PermissionDenied("La consulta no admite datos de guardado.")
    elif configuracion_id is None and ConfiguracionAvanzadaEmpresa.objects.filter(empresa_id=empresa.pk).exists():
        raise PermissionDenied("La empresa ya tiene configuración avanzada; use su acceso autorizado de consulta o edición.")
    # La identidad se fija antes de la validación original del ModelForm.
    request._mobile_advanced_state = {
        "empresa": empresa, "empresa_original": _identidad(empresa),
        "configuracion_id": configuracion_id, "add": configuracion_id is None,
    }


def adaptar_formulario_avanzado_app(formulario):
    for nombre, campo in formulario.base_fields.items():
        if nombre == "empresa":
            campo.widget = forms.HiddenInput()
        elif isinstance(campo, forms.ModelMultipleChoiceField):
            campo.widget = forms.CheckboxSelectMultiple()
            campo.widget.choices = campo.choices
            campo.help_text = ConfiguracionAvanzadaEmpresa._meta.get_field(nombre).help_text
        elif isinstance(campo, forms.BooleanField):
            campo.widget = forms.CheckboxInput()
        elif nombre == "notas":
            campo.widget = forms.Textarea(attrs={"rows": 4})
        else:
            campo.widget = forms.TextInput()
    return formulario


def _valores(configuracion, empresa_original):
    valores = []
    for nombre in CAMPOS_AVANZADOS_APP:
        campo = ConfiguracionAvanzadaEmpresa._meta.get_field(nombre)
        if nombre == "empresa":
            valor = empresa_original.nombre
        elif nombre == "modulos_adicionales_visibles_clinica":
            valor = ", ".join(configuracion.modulos_adicionales_visibles_clinica.values_list("nombre", flat=True)) or "Sin módulos seleccionados"
        else:
            valor = getattr(configuracion, nombre)
            if isinstance(valor, bool):
                valor = "Sí" if valor else "No"
            else:
                valor = str(valor) if valor is not None else ""
        valores.append({"nombre": nombre, "etiqueta": str(campo.verbose_name).capitalize(), "valor": valor})
    valores.append({"nombre": "fecha_actualizacion", "etiqueta": "Última actualización", "valor": configuracion.fecha_actualizacion})
    return valores


def render_cambio_avanzado_app(administrador, request, context, add, obj):
    estado = request._mobile_advanced_state
    empresa = estado["empresa"]
    capacidades = configuracion_avanzada_disponible_app(request, empresa)
    editable = administrador.has_add_permission(request) if add else administrador.has_change_permission(request, obj)
    form = context["adminform"].form
    if add and request.method in {"GET", "HEAD"}:
        form.initial["empresa"] = empresa.pk
    grupos = (
        ("Operación y cobros", ("usa_cierre_caja", "usa_pagos_mixtos", "usa_reporte_bancos")),
        ("Inventario y bodegas", ("usa_inventario_farmaceutico", "usa_control_lotes_fefo", "usa_bodegas_internas", "ventas_solo_desde_vitrina", "bodega_venta_predeterminada")),
        ("Opciones clínicas y fiscales", ("manuales_pdf_habilitados", "permite_cai_historico", "permite_plantilla_factura_independiente")),
        ("Navegación clínica", ("modulos_adicionales_visibles_clinica",)),
        ("Notas", ("notas",)),
    )
    contexto = {
        **context, **_contexto(request, empresa, configuracion_existe=not add, capacidades=capacidades),
        "empresa": estado["empresa_original"], "empresa_original": estado["empresa_original"],
        "form": form, "mobile_advanced_add": add, "mobile_advanced_readonly": not editable,
        "app_advanced_form_url": _url_formulario(administrador, empresa.pk, estado["configuracion_id"]) if editable else "",
        "mobile_advanced_sections": [
            {"titulo": titulo, "campos": [form[nombre] for nombre in nombres if nombre in form.fields]}
            for titulo, nombres in grupos
        ] if editable else [],
        "mobile_advanced_values": _valores(obj, estado["empresa_original"]) if not editable else [],
        "has_change_permission": capacidades["editar"], "has_add_permission": capacidades["crear"],
        "has_view_permission": capacidades["ver"], "has_delete_permission": False,
    }
    return respuesta_avanzada_app(TemplateResponse(request, "core/mobile_advanced_form.html", contexto))


def respuesta_guardado_avanzado_app(administrador, request, obj, *, add=False):
    texto = "Configuración avanzada creada correctamente." if add else "Configuración avanzada actualizada correctamente."
    administrador.message_user(request, texto, messages.SUCCESS)
    capacidades = configuracion_avanzada_disponible_app(request)
    destino = _url_presentador(obj.empresa_id) if capacidades["ver"] else f"{reverse('configuracion_avanzada_empresas_app')}?app=1"
    return respuesta_avanzada_app(HttpResponseRedirect(destino))


def _picker(request):
    _autorizar_consulta(request)
    capacidades = configuracion_avanzada_disponible_app(request)
    empresas = list(Empresa.objects.filter(slug__in=EMPRESAS_CLINICAS_CON_ROLES, tipo_solucion="clinica")
        .only("pk", "nombre", "slug", "tipo_solucion", "activa").order_by("nombre", "pk"))
    configuradas = set(ConfiguracionAvanzadaEmpresa.objects.filter(empresa_id__in=[empresa.pk for empresa in empresas])
        .values_list("empresa_id", flat=True))
    contexto = {
        **_contexto(request, capacidades=capacidades), "total_empresas": len(empresas),
        "mobile_advanced_items": [{
            "empresa": _identidad(empresa), "configuracion_existe": empresa.pk in configuradas,
            "entry_url": _url_presentador(empresa.pk) if (capacidades["ver"] or (empresa.pk not in configuradas and capacidades["crear"])) else "",
            "can_view_advanced": capacidades["ver"], "can_change_advanced": capacidades["editar"],
            "can_add_advanced": capacidades["crear"],
        } for empresa in empresas],
    }
    return respuesta_avanzada_app(TemplateResponse(request, "core/mobile_advanced_list.html", contexto))


def _presentador(request, empresa_id):
    _autorizar_consulta(request, empresa_id)
    empresa = _empresa(_numero_canonico(empresa_id))
    administrador = _administrador()
    configuracion = ConfiguracionAvanzadaEmpresa.objects.filter(empresa_id=empresa.pk).only("pk", "empresa_id").first()
    capacidades = configuracion_avanzada_disponible_app(request, empresa)
    if configuracion is not None and not capacidades["ver"]:
        raise PermissionDenied("No tiene permiso para consultar la configuración existente.")
    if configuracion is None and not capacidades["crear"]:
        return respuesta_avanzada_app(TemplateResponse(request, "core/mobile_advanced_form.html", {
            **_contexto(request, empresa, capacidades=capacidades),
            "form": None, "adminform": None, "mobile_advanced_readonly": True,
        }))
    request._mobile_advanced_state = {"empresa": empresa}
    return administrador.changeform_view(request, object_id=str(configuracion.pk) if configuracion else None,
        form_url=_url_formulario(administrador, empresa.pk, configuracion.pk if configuracion else None))


def configuracion_avanzada_empresas_app(request):
    if not settings.ENABLE_DJANGO_ADMIN:
        raise Http404("El administrador está deshabilitado.")
    return admin.site.admin_view(_picker)(request)


def configuracion_avanzada_empresa_app(request, empresa_id):
    if not settings.ENABLE_DJANGO_ADMIN:
        raise Http404("El administrador está deshabilitado.")
    return admin.site.admin_view(_presentador)(request, empresa_id)


def respuesta_avanzada_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
