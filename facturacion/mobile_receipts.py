"""Presentación móvil de recibos, sin modificar cobros ni contabilidad.

La edición conserva ReciboPagoForm y su guardado documental original; el PDF
conserva la plantilla, el motor y los encabezados de su vista existente.
"""

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from contabilidad.models import CuentaContable, CuentaFinanciera
from .models import Cliente, Factura, NotaCredito, PagoFactura, Proveedor, ReciboPago


def es_recibos_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def recibos_disponibles_app(request, empresa):
    """Permiso existente del módulo, sin consultar documentos o crear datos."""
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_recibos", empresa)
    )


def _numero_canonico(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador o página de recibos no válidos.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador o página de recibos no válidos.")
    return numero


def autorizar_recibos_app(request, empresa, recibo_id=None, editar=False, campos=None):
    if not recibos_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para consultar o editar recibos en esta empresa.")
    if request.method not in ({"GET", "HEAD", "POST"} if editar else {"GET", "HEAD"}):
        raise PermissionDenied("Método no disponible en los recibos móviles.")
    if recibo_id is not None:
        _numero_canonico(recibo_id)
        segmentos = request.path.strip("/").split("/")
        if "recibos" in segmentos:
            indice = segmentos.index("recibos") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != recibo_id:
                raise PermissionDenied("Identificador de recibo no válido.")
    permitidos_get = {"app", "q", "page"} if recibo_id is None else {"app"}
    if (set(request.GET) - permitidos_get
            or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])):
        raise PermissionDenied("Parámetros de recibos móviles inválidos.")
    if request.method == "POST":
        campos = campos or {}
        iniciales = {f"initial-{nombre}" for nombre, campo in campos.items() if campo.show_hidden_initial}
        if (set(request.POST) - (set(campos) | iniciales | {"app", "csrfmiddlewaretoken"})
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
                or request.POST.getlist("app") != ["1"] or request.FILES):
            raise PermissionDenied("Datos del formulario de recibo inválidos.")
    elif "page" in request.GET:
        _numero_canonico(request.GET["page"])


def _validar_pagos_app(pagos, empresa):
    cuentas = CuentaFinanciera.objects.filter(
        empresa=empresa,
        cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk"),
    ).values("pk")
    if (
        pagos.filter(cuenta_financiera_id__isnull=False).exclude(cuenta_financiera_id__in=cuentas).exists()
        or pagos.filter(cuenta_financiera_impuesto_id__isnull=False).exclude(cuenta_financiera_impuesto_id__in=cuentas).exists()
        or pagos.filter(proveedor_comision_id__isnull=False).exclude(proveedor_comision_id__in=Proveedor.objects.filter(empresa=empresa).values("pk")).exists()
        or pagos.filter(cajero_id__isnull=False).exclude(cajero_id__in=get_user_model().objects.values("pk")).exists()
    ):
        raise PermissionDenied("Las relaciones del cobro no corresponden a la empresa del recibo.")


def _validar_recibos_app(recibos, empresa):
    clientes = Cliente.objects.filter(empresa=empresa).values("pk")
    facturas = Factura.objects.filter(empresa=empresa, cliente_id__in=clientes).values("pk")
    pagos = PagoFactura.objects.filter(factura_id__in=facturas)
    # Primero verificar existencia con subqueries, sin INNER JOIN que oculte
    # una FK huérfana importada. Después comprobar coherencia de los enlaces.
    if (
        recibos.exclude(factura_id__in=facturas).exists()
        or recibos.exclude(cliente_id__in=clientes).exists()
        or recibos.exclude(pago_id__in=pagos.values("pk")).exists()
        or recibos.exclude(pago__factura_id=F("factura_id")).exists()
        or recibos.exclude(cliente_id=F("factura__cliente_id")).exists()
    ):
        raise PermissionDenied("El recibo contiene una relación incompatible con la empresa activa.")
    _validar_pagos_app(PagoFactura.objects.filter(pk__in=recibos.values("pago_id")), empresa)


def validar_recibo_app(empresa, recibo_id, detalle=False):
    recibos = ReciboPago.objects.filter(pk=recibo_id, empresa=empresa)
    recibo = get_object_or_404(recibos)
    _validar_recibos_app(recibos, empresa)
    if detalle:
        _validar_pagos_app(PagoFactura.objects.filter(factura_id=recibo.factura_id), empresa)
        factura = Factura.objects.only("cliente_id").get(pk=recibo.factura_id)
        # Sólo las emitidas intervienen en Factura.saldo_pendiente; no cambiar
        # su cálculo ni consultar etiquetas o detalle de notas de crédito.
        notas = NotaCredito.objects.filter(factura_origen_id=recibo.factura_id, estado="emitida")
        if notas.exclude(empresa_id=empresa.pk, cliente_id=factura.cliente_id).exists():
            raise PermissionDenied("Las notas de crédito aplicadas no corresponden a esta factura y empresa.")


def url_recibo_app(vista, empresa, recibo=None, **parametros):
    args = [empresa.slug] + ([recibo.pk] if recibo is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _contexto_recibos_app(request, empresa, recibo=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    listado = url_recibo_app("recibos_dashboard", empresa)
    contexto = {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_receipts": True, "can_edit_receipt": True,
        "app_receipts_url": listado, "app_receipt_search_url": listado,
        "app_receipts_search_url": listado,
        "app_receipts_back_url": modulos, "app_receipt_back_url": listado,
        "can_view_invoice": request.user.tiene_permiso_erp("puede_ver_facturas", empresa),
    }
    if recibo is not None:
        contexto.update({
            "app_receipt_detail_url": url_recibo_app("ver_recibo", empresa, recibo),
            "app_receipt_edit_url": url_recibo_app("editar_recibo", empresa, recibo),
            "app_receipt_form_url": url_recibo_app("editar_recibo", empresa, recibo),
            "app_receipt_pdf_url": url_recibo_app("descargar_recibo_pdf", empresa, recibo),
            "app_receipt_invoice_url": f"{reverse('ver_factura', args=[empresa.slug, recibo.factura_id])}?app=1" if contexto["can_view_invoice"] else "",
        })
    return contexto


def listar_recibos_app(request, empresa):
    recibos = ReciboPago.objects.filter(empresa=empresa)
    _validar_recibos_app(recibos, empresa)
    consulta = request.GET.get("q", "").strip()
    if consulta:
        recibos = recibos.filter(
            Q(numero_recibo__icontains=consulta) | Q(cliente__nombre__icontains=consulta)
            | Q(referencia__icontains=consulta) | Q(concepto__icontains=consulta)
            | Q(factura__numero_factura__icontains=consulta)
        )
    pagina = Paginator(recibos.select_related("cliente", "factura").order_by("-fecha_creacion", "-id"), 20).get_page(request.GET.get("page", "1"))
    contexto = _contexto_recibos_app(request, empresa)
    url_pagina = lambda numero: url_recibo_app("recibos_dashboard", empresa, **({"q": consulta} if consulta else {}), page=numero)
    contexto.update({
        "recibos": pagina, "q": consulta, "mobile_receipt_query": consulta,
        "mobile_receipt_total": pagina.paginator.count,
        "mobile_receipt_page": pagina.number, "mobile_receipt_num_pages": pagina.paginator.num_pages,
        "mobile_receipt_previous_url": url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_receipt_next_url": url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
        "mobile_receipt_items": [{
            "recibo": recibo, "numero": recibo.numero_recibo, "fecha": recibo.fecha,
            "cliente": recibo.cliente.nombre, "monto": recibo.monto,
            "moneda": recibo.factura.moneda, "metodo": recibo.metodo,
            "metodo_label": recibo.get_metodo_display(),
            "detalle_url": url_recibo_app("ver_recibo", empresa, recibo),
            "pdf_url": url_recibo_app("descargar_recibo_pdf", empresa, recibo),
        } for recibo in pagina],
    })
    return respuesta_recibos_app(render(request, "facturacion/mobile_receipt_list.html", contexto))


def render_recibo_app(request, empresa, contexto, editar=False):
    recibo = contexto["recibo"]
    contexto = {**contexto, **_contexto_recibos_app(request, empresa, recibo)}
    if editar:
        contexto["form"].fields["fecha"].widget.format = "%Y-%m-%d"
        contexto["app_receipt_back_url"] = contexto["app_receipt_detail_url"]
    else:
        contexto["mobile_receipt_payment_items"] = [{
            "pago": pago, "fecha": pago.fecha, "metodo_label": pago.get_metodo_display(),
            "referencia": pago.referencia or "", "cuenta": pago.cuenta_financiera.nombre if pago.cuenta_financiera_id else "",
            "cajero": pago.cajero.get_full_name().strip() or pago.cajero.username if pago.cajero_id else "Usuario no disponible",
            "monto": pago.monto, "retenciones": pago.total_retenciones,
            "total_aplicado": pago.total_aplicado, "actual": pago.pk == recibo.pago_id,
        } for pago in contexto["pagos_factura"]]
    template = "facturacion/mobile_receipt_edit.html" if editar else "facturacion/mobile_receipt_detail.html"
    return respuesta_recibos_app(render(request, template, contexto))


def respuesta_recibos_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def volver_recibo_app(empresa, recibo):
    return respuesta_recibos_app(redirect(url_recibo_app("ver_recibo", empresa, recibo)))
