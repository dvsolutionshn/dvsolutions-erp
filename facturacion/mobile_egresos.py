"""Consulta móvil de comprobantes de egreso, sin escrituras financieras.

Los datos documentales son los snapshots originales del comprobante. El PDF
conserva la vista, plantilla, motor y encabezados originales.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from contabilidad.models import CuentaContable, CuentaFinanciera
from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import ClienteContable, CompraInventario, ComprobanteEgresoCompra, PagoCompra, Proveedor


METODOS_FILTRO_EGRESOS = ("efectivo", "transferencia", "tarjeta")


def es_egresos_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def egresos_disponibles_app(request, empresa):
    """Capacidad existente, sin consultar documentos ni configurar cuentas."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_egresos", empresa)
    )


def _numero_canonico(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página de egresos no válidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página de egresos no válidos.")
    return numero


def autorizar_egresos_app(request, empresa, comprobante_id=None):
    if not egresos_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar egresos en esta empresa.")
    if request.method not in {"GET", "HEAD"} or request.FILES:
        raise PermissionDenied("Los egresos móviles son únicamente de consulta.")
    permitidos = {"app", "q", "metodo", "page"} if comprobante_id is None else {"app"}
    if (
        set(request.GET) - permitidos
        or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros de egresos móviles inválidos.")
    if comprobante_id is not None:
        identificador = _numero_canonico(comprobante_id)
        segmentos = request.path.strip("/").split("/")
        if "egresos" in segmentos:
            indice = segmentos.index("egresos") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("Identificador de comprobante no válido.")
    else:
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])
        if request.GET.get("metodo", "").strip() not in ("", *METODOS_FILTRO_EGRESOS):
            raise PermissionDenied("Filtro de método de egreso no válido.")


def _validar_egresos_app(egresos, empresa):
    # Subqueries de existencia antes de los JOIN de presentación: una FK
    # importada huérfana no debe desaparecer silenciosamente del listado.
    proveedores = Proveedor.objects.filter(empresa=empresa).filter(
        Q(cliente_contable_id__isnull=True)
        | Q(cliente_contable_id__in=ClienteContable.objects.filter(empresa=empresa).values("pk"))
    ).values("pk")
    compras = CompraInventario.objects.filter(empresa=empresa).filter(
        Q(proveedor_id__isnull=True) | Q(proveedor_id__in=proveedores)
    ).values("pk")
    cuentas = CuentaFinanciera.objects.filter(
        empresa=empresa,
        cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk"),
    ).values("pk")
    pagos = PagoCompra.objects.filter(compra_id__in=compras).filter(
        Q(cuenta_financiera_id__isnull=True) | Q(cuenta_financiera_id__in=cuentas)
    ).values("pk")
    if (
        egresos.exclude(compra_id__in=compras).exists()
        or egresos.filter(proveedor_id__isnull=False).exclude(proveedor_id__in=proveedores).exists()
        or egresos.exclude(pago_id__in=pagos).exists()
        or egresos.exclude(pago__compra_id=F("compra_id")).exists()
    ):
        raise PermissionDenied("El comprobante contiene una relación incompatible con la empresa activa.")


def validar_egreso_app(empresa, comprobante_id):
    egresos = ComprobanteEgresoCompra.objects.filter(pk=comprobante_id, empresa=empresa)
    get_object_or_404(egresos)
    _validar_egresos_app(egresos, empresa)


def url_egreso_app(vista, empresa, comprobante=None, **parametros):
    args = [empresa.slug] + ([comprobante.pk] if comprobante is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_egresos_app(request, empresa, comprobante=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_egreso_app("egresos_dashboard", empresa)
    contexto = {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_egresos": True, "can_download_egreso": True,
        "can_view_purchase": False, "app_egreso_purchase_url": "",
        "app_egresos_url": listado, "app_egresos_search_url": listado,
        "app_egreso_back_url": listado if comprobante is not None else modulos,
    }
    if comprobante is not None:
        contexto.update({
            "app_egreso_detail_url": url_egreso_app("ver_comprobante_egreso", empresa, comprobante),
            "app_egreso_pdf_url": url_egreso_app("descargar_comprobante_egreso_pdf", empresa, comprobante),
        })
    return contexto


def listar_egresos_app(request, empresa):
    egresos = ComprobanteEgresoCompra.objects.filter(empresa=empresa)
    _validar_egresos_app(egresos, empresa)
    consulta = request.GET.get("q", "").strip()
    metodo = request.GET.get("metodo", "").strip()
    if consulta:
        egresos = egresos.filter(
            Q(numero_comprobante__icontains=consulta) | Q(proveedor_nombre__icontains=consulta)
            | Q(referencia__icontains=consulta) | Q(compra__numero_compra__icontains=consulta)
        )
    if metodo in METODOS_FILTRO_EGRESOS:
        egresos = egresos.filter(metodo=metodo)
    pagina = Paginator(egresos.select_related("compra", "proveedor", "pago").order_by("-fecha_creacion", "-id"), 20).get_page(request.GET.get("page", "1"))
    contexto = _contexto_egresos_app(request, empresa)
    filtros = {key: value for key, value in {"q": consulta, "metodo": metodo}.items() if value}
    url_pagina = lambda numero: url_egreso_app("egresos_dashboard", empresa, **filtros, page=numero)
    etiquetas = dict(PagoCompra.METODOS)
    contexto.update({
        "egresos": pagina, "q": consulta, "metodo": metodo,
        "mobile_egreso_query": consulta, "mobile_egreso_method": metodo,
        "mobile_egreso_total": pagina.paginator.count,
        "mobile_egreso_page": pagina.number, "mobile_egreso_num_pages": pagina.paginator.num_pages,
        "mobile_egreso_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_egreso_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_egreso_methods": [{"codigo": codigo, "nombre": etiquetas[codigo]} for codigo in METODOS_FILTRO_EGRESOS],
        "mobile_egreso_items": [{
            "comprobante": egreso, "numero": egreso.numero_comprobante,
            "fecha": egreso.fecha, "proveedor": egreso.proveedor_nombre,
            "monto": egreso.monto, "metodo": egreso.metodo,
            "metodo_label": egreso.get_metodo_display(),
            "compra_numero": egreso.compra.numero_compra, "referencia": egreso.referencia or "",
            "detalle_url": url_egreso_app("ver_comprobante_egreso", empresa, egreso),
            "pdf_url": url_egreso_app("descargar_comprobante_egreso_pdf", empresa, egreso),
        } for egreso in pagina],
    })
    return respuesta_egresos_app(render(request, "facturacion/mobile_egreso_list.html", contexto))


def render_egreso_app(request, empresa, contexto):
    contexto = {**contexto, **_contexto_egresos_app(request, empresa, contexto["comprobante"])}
    return respuesta_egresos_app(render(request, "facturacion/mobile_egreso_detail.html", contexto))


def respuesta_egresos_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
