"""Consulta móvil de compras, sin operaciones ni cálculos financieros nuevos.

La lista utiliza los filtros y los siete resúmenes de compras_dashboard; el
detalle y PDF conservan sus consultas y propiedades originales. Las acciones
de inventario, contabilidad, pago o anulación no se exponen en esta interfaz.
"""

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from contabilidad.models import CuentaContable, CuentaFinanciera
from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import (
    BodegaInventario, ClienteContable, CompraInventario, ComprobanteEgresoCompra,
    CuentaAcumuladoCompra, EntradaInventarioDocumento, Factura, LineaCompraInventario,
    MovimientoInventario, NotaCredito, PagoCompra, Producto, Proveedor, TipoImpuesto,
)


ESTADOS_FILTRO_COMPRAS = ("borrador", "aplicada", "anulada")
PAGOS_FILTRO_COMPRAS = ("pendiente", "parcial", "pagado")


def es_compras_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def compras_disponibles_app(request, empresa):
    """Capacidad de consulta existente, sin leer compras ni preparar catálogos."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_compras", empresa)
    )


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador o página de compras no válidos.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador o página de compras no válidos.")
    return numero


def autorizar_compras_app(request, empresa, compra_id=None):
    if not compras_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar compras en esta empresa.")
    if request.method not in {"GET", "HEAD"} or request.FILES:
        raise PermissionDenied("Las compras móviles son únicamente de consulta.")
    permitidos = {"app", "q", "estado", "pago", "page"} if compra_id is None else {"app"}
    if (set(request.GET) - permitidos
            or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
            or request.GET.getlist("app") != ["1"]):
        raise PermissionDenied("Parámetros de compras móviles inválidos.")
    if compra_id is not None:
        numero = _numero_canonico(compra_id)
        segmentos = request.path.strip("/").split("/")
        if "compras" in segmentos:
            indice = segmentos.index("compras") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != numero:
                raise PermissionDenied("Identificador de compra no válido.")
    else:
        if "page" in request.GET:
            _numero_canonico(request.GET["page"])
        if (request.GET.get("estado", "").strip() not in ("", *ESTADOS_FILTRO_COMPRAS)
                or request.GET.get("pago", "").strip() not in ("", *PAGOS_FILTRO_COMPRAS)):
            raise PermissionDenied("Filtro de estado de compras no válido.")


def validar_relaciones_compras_app(empresa, compras):
    """Validar el conjunto antes de JOIN, filtros, propiedades o paginación.

Se revisan sólo relaciones existentes y pertenencia, no coincidencias entre
snapshots del documento y datos actuales ni nuevas reglas de saldo o stock.
"""
    usuarios = get_user_model().objects.values("pk")
    contables = CuentaContable.objects.filter(empresa=empresa).values("pk")
    clientes_contables = ClienteContable.objects.filter(empresa=empresa).values("pk")
    habituales = CuentaAcumuladoCompra.objects.filter(cliente_contable_id__in=clientes_contables).values("pk")
    proveedores = Proveedor.objects.filter(empresa=empresa).filter(
        Q(cliente_contable_id__isnull=True) | Q(cliente_contable_id__in=clientes_contables),
    ).filter(
        Q(cuenta_contable_id__isnull=True) | Q(cuenta_contable_id__in=contables),
    ).filter(
        Q(cuenta_habitual_id__isnull=True) | Q(cuenta_habitual_id__in=habituales),
    ).filter(
        Q(cuenta_habitual_por_id__isnull=True) | Q(cuenta_habitual_por_id__in=usuarios),
    ).values("pk")
    cuentas = CuentaFinanciera.objects.filter(empresa=empresa, cuenta_contable_id__in=contables).values("pk")
    compra_ids = compras.values("pk")
    productos = Producto.objects.filter(empresa=empresa).values("pk")
    lineas = LineaCompraInventario.objects.filter(compra_id__in=compra_ids)
    pagos = PagoCompra.objects.filter(compra_id__in=compra_ids)
    movimientos = MovimientoInventario.objects.filter(compra_documento_id__in=compra_ids)
    # No filtrar por empresa aquí: también importa un comprobante importado
    # incoherente enlazado por compra O por alguno de sus pagos.
    egresos = ComprobanteEgresoCompra.objects.filter(
        Q(compra_id__in=compra_ids) | Q(pago_id__in=pagos.values("pk")),
    )
    if (
        compras.exclude(empresa_id=empresa.pk).exists()
        or compras.filter(proveedor_id__isnull=False).exclude(proveedor_id__in=proveedores).exists()
        or compras.filter(cuenta_financiera_pago_id__isnull=False).exclude(cuenta_financiera_pago_id__in=cuentas).exists()
        or lineas.exclude(producto_id__in=productos).exists()
        # TipoImpuesto es global: no tiene empresa; sólo exige existencia.
        or lineas.filter(impuesto_id__isnull=False).exclude(impuesto_id__in=TipoImpuesto.objects.values("pk")).exists()
        or pagos.filter(cuenta_financiera_id__isnull=False).exclude(cuenta_financiera_id__in=cuentas).exists()
        or egresos.exclude(empresa_id=empresa.pk).exists()
        or egresos.exclude(compra_id__in=compra_ids).exists()
        or egresos.exclude(pago_id__in=pagos.values("pk")).exists()
        or egresos.exclude(pago__compra_id=F("compra_id")).exists()
        or egresos.filter(proveedor_id__isnull=False).exclude(proveedor_id__in=proveedores).exists()
        or movimientos.exclude(empresa_id=empresa.pk).exists()
        or movimientos.exclude(producto_id__in=productos).exists()
        or movimientos.filter(bodega_id__isnull=False).exclude(bodega_id__in=BodegaInventario.objects.filter(empresa=empresa).values("pk")).exists()
        or movimientos.filter(usuario_id__isnull=False).exclude(usuario_id__in=usuarios).exists()
        or movimientos.filter(factura_id__isnull=False).exclude(factura_id__in=Factura.objects.filter(empresa=empresa).values("pk")).exists()
        or movimientos.filter(nota_credito_id__isnull=False).exclude(nota_credito_id__in=NotaCredito.objects.filter(empresa=empresa).values("pk")).exists()
        or movimientos.filter(entrada_documento_id__isnull=False).exclude(entrada_documento_id__in=EntradaInventarioDocumento.objects.filter(empresa=empresa).values("pk")).exists()
    ):
        raise PermissionDenied("La compra contiene una relación incompatible con la empresa activa.")


def validar_compra_app(empresa, compra_id):
    compras = CompraInventario.objects.filter(pk=compra_id, empresa=empresa)
    get_object_or_404(compras)
    validar_relaciones_compras_app(empresa, compras)


def url_compra_app(vista, empresa, compra=None, **parametros):
    args = [empresa.slug] + ([compra.pk] if compra is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_compras_app(request, empresa, compra=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app
    from .mobile_suppliers import proveedores_disponibles_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_compra_app("compras_dashboard", empresa)
    proveedor = compra.proveedor if compra is not None and compra.proveedor_id else None
    puede_proveedor = bool(proveedor is not None and proveedor.cliente_contable_id is None and proveedores_disponibles_app(request, empresa)["ver"])
    contexto = {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_purchases": True, "can_view_purchase": True, "can_download_purchase": True,
        "can_view_supplier": puede_proveedor,
        "can_view_egresos": False,
        "app_purchases_url": listado, "app_purchases_search_url": listado,
        "app_purchase_back_url": listado if compra is not None else modulos,
        "app_purchase_supplier_url": url_compra_app("ver_proveedor_facturacion", empresa, proveedor) if puede_proveedor else "",
    }
    for accion in ("create", "edit", "apply", "void", "delete", "pay"):
        contexto[f"can_{accion}_purchase"] = False
        contexto[f"app_purchase_{accion}_url"] = ""
    contexto.update({
        "can_revert_purchase_payment": False, "app_purchase_payment_revert_url": "",
        "puede_eliminar_compra_anulada": False,
        "mobile_purchase_actions_pending": True,
    })
    if compra is not None:
        contexto.update({
            "app_purchase_detail_url": url_compra_app("ver_compra", empresa, compra),
            "app_purchase_pdf_url": url_compra_app("descargar_compra_pdf", empresa, compra),
        })
    return contexto


def render_compras_app(request, empresa, contexto):
    # Se pagina la lista YA filtrada por la vista original. Los siete resúmenes
    # originales siguen describiendo el conjunto completo, no sólo esta página.
    pagina = Paginator(contexto["compras"], 20).get_page(request.GET.get("page", "1"))
    contexto = {**contexto, **_contexto_compras_app(request, empresa)}
    filtros = {key: value for key, value in {
        "q": contexto["q"], "estado": contexto["estado_filtro"], "pago": contexto["pago_filtro"],
    }.items() if value}
    url_pagina = lambda numero: url_compra_app("compras_dashboard", empresa, **filtros, page=numero)
    contexto.update({
        "compras": pagina, "mobile_purchase_query": contexto["q"],
        "mobile_purchase_state": contexto["estado_filtro"], "mobile_purchase_payment_state": contexto["pago_filtro"],
        "mobile_purchase_total": pagina.paginator.count,
        "mobile_purchase_page": pagina.number, "mobile_purchase_num_pages": pagina.paginator.num_pages,
        "mobile_purchase_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_purchase_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_purchase_states": [{"codigo": codigo, "nombre": nombre} for codigo, nombre in CompraInventario.ESTADOS],
        "mobile_purchase_payment_states": [{"codigo": "pendiente", "nombre": "Pendiente"}, {"codigo": "parcial", "nombre": "Parcial"}, {"codigo": "pagado", "nombre": "Pagada"}],
        "mobile_purchase_items": [{
            "compra": compra, "detalle_url": url_compra_app("ver_compra", empresa, compra),
            "pdf_url": url_compra_app("descargar_compra_pdf", empresa, compra),
        } for compra in pagina],
    })
    return respuesta_compras_app(render(request, "facturacion/mobile_purchase_list.html", contexto))


def render_compra_app(request, empresa, contexto):
    compra = contexto["compra"]
    contexto = {**contexto, **_contexto_compras_app(request, empresa, compra)}
    pagos = []
    for pago in contexto["pagos"]:
        pagos.append({
            "pago": pago, "comprobante_numero": "",
            "comprobante_url": "", "comprobante_pdf_url": "",
        })
    contexto.update({
        "mobile_purchase_lines": [{"linea": linea} for linea in compra.lineas.all()],
        "mobile_purchase_payments": pagos,
        "mobile_purchase_movements": [{
            "movimiento": movimiento,
            "usuario": (movimiento.usuario.get_full_name().strip() or movimiento.usuario.username) if movimiento.usuario_id else "Usuario no disponible",
        } for movimiento in contexto["movimientos"]],
    })
    return respuesta_compras_app(render(request, "facturacion/mobile_purchase_detail.html", contexto))


def respuesta_compras_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
