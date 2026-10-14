"""Presentación móvil de proveedores y su resumen original de compras.

No crea catálogos contables ni concede permisos de compras o pagos. Los
formularios, guardados y cálculos siguen siendo los de las vistas existentes.
"""

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from contabilidad.models import CuentaContable, CuentaFinanciera
from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import (
    ClienteContable, CompraInventario, CuentaAcumuladoCompra, LineaCompraInventario,
    PagoCompra, Producto, Proveedor, TipoImpuesto,
)


def es_proveedores_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def proveedores_disponibles_app(request, empresa):
    """Las tres capacidades originales, independientes para todos los roles."""
    usuario = request.user
    habilitado = bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
    )
    return {
        clave: bool(habilitado and usuario.tiene_permiso_erp(permiso, empresa))
        for clave, permiso in (
            ("ver", "puede_proveedores"), ("crear", "puede_crear_proveedores"),
            ("editar", "puede_editar_proveedores"),
        )
    }


def _numero_canonico(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página de proveedores no válidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página de proveedores no válidos.")
    return numero


def autorizar_proveedores_app(request, empresa, accion="ver", proveedor_id=None, campos=None):
    if not proveedores_disponibles_app(request, empresa).get(accion, False):
        raise PermissionDenied("No tiene permiso para esta acción de proveedores.")
    escritura = accion in {"crear", "editar"}
    if request.method not in ({"GET", "HEAD", "POST"} if escritura else {"GET", "HEAD"}):
        raise PermissionDenied("Método no disponible en proveedores móviles.")
    if request.FILES:
        raise PermissionDenied("Este formulario de proveedor no recibe archivos.")
    if proveedor_id is not None:
        identificador = _numero_canonico(proveedor_id)
        segmentos = request.path.strip("/").split("/")
        if "proveedores" in segmentos:
            indice = segmentos.index("proveedores") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("Identificador de proveedor no válido.")
    permitidos_get = {"app", "q", "estado", "page"} if not escritura and proveedor_id is None else {"app"}
    if (
        set(request.GET) - permitidos_get
        or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
        or ("app" in request.GET and request.GET.getlist("app") != ["1"])
    ):
        raise PermissionDenied("Parámetros de proveedores móviles inválidos.")
    if request.method == "POST":
        campos = campos or {}
        iniciales = {f"initial-{nombre}" for nombre, campo in campos.items() if campo.show_hidden_initial}
        if (
            set(request.POST) - (set(campos) | iniciales | {"app", "csrfmiddlewaretoken"})
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
            or request.POST.getlist("app") != ["1"]
        ):
            raise PermissionDenied("Datos del formulario de proveedor inválidos.")
    elif request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("La consulta móvil requiere app=1.")
    if "page" in request.GET:
        _numero_canonico(request.GET["page"])
    if request.GET.get("estado", "").strip() not in {"", "activos", "inactivos"}:
        raise PermissionDenied("Filtro de estado de proveedores no válido.")


def _validar_proveedores_app(proveedores, empresa):
    cuentas = CuentaContable.objects.filter(empresa=empresa).values("pk")
    cuentas_habituales = CuentaAcumuladoCompra.objects.filter(
        cliente_contable_id__in=ClienteContable.objects.filter(empresa=empresa).values("pk"),
    ).values("pk")
    if (
        proveedores.filter(cuenta_contable_id__isnull=False).exclude(cuenta_contable_id__in=cuentas).exists()
        or proveedores.filter(cuenta_habitual_id__isnull=False).exclude(cuenta_habitual_id__in=cuentas_habituales).exists()
        or proveedores.filter(cuenta_habitual_por_id__isnull=False).exclude(cuenta_habitual_por_id__in=get_user_model().objects.values("pk")).exists()
    ):
        raise PermissionDenied("Las relaciones del proveedor no corresponden a la empresa activa.")


def validar_proveedor_app(empresa, proveedor):
    _validar_proveedores_app(Proveedor.objects.filter(pk=proveedor.pk, empresa=empresa, cliente_contable__isnull=True), empresa)


def validar_compras_proveedor_app(empresa, proveedor):
    # Revisar todo lo relacionado antes de filtrar por empresa o calcular los
    # agregados originales; no esconder una relación importada incoherente.
    compras = CompraInventario.objects.filter(proveedor_id=proveedor.pk)
    if compras.exclude(empresa_id=empresa.pk).exists():
        raise PermissionDenied("Una compra asociada no corresponde a la empresa del proveedor.")
    cuentas = CuentaFinanciera.objects.filter(
        empresa=empresa,
        cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk"),
    ).values("pk")
    lineas = LineaCompraInventario.objects.filter(compra_id__in=compras.values("pk"))
    pagos = PagoCompra.objects.filter(compra_id__in=compras.values("pk"))
    if (
        compras.filter(cuenta_financiera_pago_id__isnull=False).exclude(cuenta_financiera_pago_id__in=cuentas).exists()
        or lineas.exclude(producto_id__in=Producto.objects.filter(empresa=empresa).values("pk")).exists()
        # TipoImpuesto es un catálogo global: verificar existencia, no inventar
        # un aislamiento por empresa que el modelo no tiene.
        or lineas.filter(impuesto_id__isnull=False).exclude(impuesto_id__in=TipoImpuesto.objects.values("pk")).exists()
        or pagos.filter(cuenta_financiera_id__isnull=False).exclude(cuenta_financiera_id__in=cuentas).exists()
    ):
        raise PermissionDenied("Las relaciones de las compras del proveedor no son válidas para esta empresa.")


def url_proveedor_app(vista, empresa, proveedor=None, **parametros):
    args = [empresa.slug] + ([proveedor.pk] if proveedor is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_proveedores_app(request, empresa, proveedor=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app
    from .mobile_purchases import compras_disponibles_app

    permisos = proveedores_disponibles_app(request, empresa)
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_proveedor_app("proveedores_facturacion", empresa) if permisos["ver"] else ""
    detalle = url_proveedor_app("ver_proveedor_facturacion", empresa, proveedor) if proveedor and permisos["ver"] else ""
    edicion = url_proveedor_app("editar_proveedor_facturacion", empresa, proveedor) if proveedor and permisos["editar"] else ""
    creacion = url_proveedor_app("crear_proveedor_facturacion", empresa) if permisos["crear"] else ""
    puede_compra = bool(proveedor is not None and permisos["ver"] and compras_disponibles_app(request, empresa))
    return {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_suppliers": permisos["ver"], "can_create_supplier": permisos["crear"],
        "can_edit_supplier": permisos["editar"], "can_view_purchase": puede_compra,
        "app_supplier_purchase_url": "", "app_suppliers_url": listado,
        "app_suppliers_search_url": listado, "app_supplier_create_url": creacion,
        "app_supplier_detail_url": detalle, "app_supplier_edit_url": edicion,
        "app_supplier_form_url": edicion if proveedor else creacion,
        "app_supplier_back_url": detalle or listado or modulos,
    }


def listar_proveedores_app(request, empresa):
    proveedores = Proveedor.objects.filter(cliente_contable__isnull=True, empresa=empresa)
    _validar_proveedores_app(proveedores, empresa)
    consulta = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "").strip()
    if consulta:
        proveedores = proveedores.filter(
            Q(nombre__icontains=consulta) | Q(rtn__icontains=consulta)
            | Q(contacto__icontains=consulta) | Q(ciudad__icontains=consulta)
        )
    if estado == "activos":
        proveedores = proveedores.filter(activo=True)
    elif estado == "inactivos":
        proveedores = proveedores.filter(activo=False)
    resumen = {
        "total": proveedores.count(), "activos": proveedores.filter(activo=True).count(),
        "inactivos": proveedores.filter(activo=False).count(),
        "con_rtn": proveedores.exclude(rtn__isnull=True).exclude(rtn__exact="").count(),
    }
    pagina = Paginator(proveedores.order_by("nombre", "id"), 20).get_page(request.GET.get("page", "1"))
    contexto = _contexto_proveedores_app(request, empresa)
    filtros = {key: value for key, value in {"q": consulta, "estado": estado}.items() if value}
    url_pagina = lambda numero: url_proveedor_app("proveedores_facturacion", empresa, **filtros, page=numero)
    contexto.update({
        "proveedores": pagina, "q": consulta, "estado": estado, "resumen": resumen,
        "app_supplier_back_url": contexto["app_modules_url"],
        "mobile_supplier_query": consulta, "mobile_supplier_state": estado,
        "mobile_supplier_total": pagina.paginator.count,
        "mobile_supplier_page": pagina.number, "mobile_supplier_num_pages": pagina.paginator.num_pages,
        "mobile_supplier_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_supplier_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_supplier_items": [{
            "proveedor": proveedor,
            "detalle_url": url_proveedor_app("ver_proveedor_facturacion", empresa, proveedor),
            "editar_url": url_proveedor_app("editar_proveedor_facturacion", empresa, proveedor) if contexto["can_edit_supplier"] else "",
        } for proveedor in pagina],
    })
    return respuesta_proveedores_app(render(request, "facturacion/mobile_supplier_list.html", contexto))


def render_proveedor_app(request, empresa, contexto, formulario=False):
    proveedor = contexto.get("proveedor")
    contexto = {**contexto, **_contexto_proveedores_app(request, empresa, proveedor)}
    if formulario:
        form = contexto["form"]
        grupos = (
            ("Identificación", ("nombre", "rtn")),
            ("Contacto", ("contacto", "telefono", "correo", "direccion", "ciudad")),
            ("Condiciones y estado", ("condicion_pago", "dias_credito", "activo")),
        )
        contexto["mobile_supplier_sections"] = [
            {"titulo": titulo, "campos": [form[nombre] for nombre in nombres]}
            for titulo, nombres in grupos
        ]
        contexto["modo_edicion"] = proveedor is not None
    else:
        from .mobile_purchases import url_compra_app

        contexto["app_supplier_back_url"] = contexto["app_suppliers_url"]
        contexto["mobile_supplier_purchase_items"] = [{
            "compra": compra,
            "detalle_url": url_compra_app("ver_compra", empresa, compra) if contexto["can_view_purchase"] else "",
            "pdf_url": url_compra_app("descargar_compra_pdf", empresa, compra) if contexto["can_view_purchase"] else "",
        } for compra in contexto["compras"]]
    template = "facturacion/mobile_supplier_form.html" if formulario else "facturacion/mobile_supplier_detail.html"
    return respuesta_proveedores_app(render(request, template, contexto))


def errores_guardado_proveedor_app(form, error):
    if hasattr(error, "message_dict"):
        for campo, errores in error.message_dict.items():
            for mensaje in errores:
                form.add_error(campo if campo in form.fields else None, mensaje)
    else:
        form.add_error(None, str(error))


def volver_proveedor_app(request, empresa, proveedor, editar=False):
    permisos = proveedores_disponibles_app(request, empresa)
    if permisos["ver"]:
        destino = url_proveedor_app("ver_proveedor_facturacion", empresa, proveedor) if editar else url_proveedor_app("proveedores_facturacion", empresa)
    elif editar or permisos["editar"]:
        destino = url_proveedor_app("editar_proveedor_facturacion", empresa, proveedor)
    else:
        destino = url_proveedor_app("crear_proveedor_facturacion", empresa)
    return respuesta_proveedores_app(redirect(destino))


def respuesta_proveedores_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
