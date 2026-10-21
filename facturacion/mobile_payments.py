"""Preparación y presentación móvil de los cobros originales.

No calcula ni guarda dinero. Las dos vistas existentes conservan sus parsers,
transacciones, modelos y servicios contables.
"""

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from contabilidad.models import CuentaContable, CuentaFinanciera
from .mobile_receipts import _validar_pagos_app, _validar_recibos_app
from .models import Cliente, ClienteContable, NotaCredito, PagoComisionProveedor, PagoFactura, Proveedor, ReciboPago


MODOS_PAGO = ("efectivo", "tarjeta", "transferencia")
CAMPOS_COMUNES = {
    "fecha", "retencion_isr", "retencion_isv", "separar_isv",
    "cuenta_financiera_impuesto", "proveedor_comision", "porcentaje_comision",
    "monto_comision", "app", "csrfmiddlewaretoken",
}
CAMPOS_SIMPLES = {"monto", "metodo", "cuenta_financiera", "referencia"}
CAMPOS_MIXTOS = {f"{prefijo}_{modo}" for prefijo in ("monto", "cuenta", "referencia") for modo in MODOS_PAGO}


def es_cobros_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def cobros_disponibles_app(request, empresa):
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_registrar_pagos_clientes", empresa)
    )


def _identificador(valor):
    valor = str(valor)
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador de cobro inválido.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador de cobro inválido.")
    return numero


def autorizar_cobros_app(request, empresa, factura_id, pago_id=None):
    if not cobros_disponibles_app(request, empresa):
        raise PermissionDenied("No tiene permiso para registrar o corregir cobros en esta empresa.")
    if request.method not in {"GET", "HEAD", "POST"}:
        raise PermissionDenied("Método no disponible en los cobros móviles.")
    _identificador(factura_id)
    if pago_id is not None:
        _identificador(pago_id)
    segmentos = request.path.strip("/").split("/")
    if "facturacion" in segmentos:
        indice = segmentos.index("facturacion") + 1
        if indice < len(segmentos) and _identificador(segmentos[indice]) != factura_id:
            raise PermissionDenied("Identificador de factura inválido.")
        if pago_id is not None and indice + 2 < len(segmentos) and _identificador(segmentos[indice + 2]) != pago_id:
            raise PermissionDenied("Identificador de pago inválido.")
    if (set(request.GET) - {"app"}
            or any(len(request.GET.getlist(key)) != 1 for key in request.GET)
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])):
        raise PermissionDenied("Parámetros de cobro móvil inválidos.")
    if request.method == "POST":
        mixto = any(f"monto_{modo}" in request.POST for modo in MODOS_PAGO)
        campos = CAMPOS_COMUNES | (CAMPOS_MIXTOS if mixto else CAMPOS_SIMPLES)
        if (set(request.POST) - campos or request.FILES
                or request.POST.getlist("app") != ["1"]
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)):
            raise PermissionDenied("Datos del formulario de cobro inválidos.")
        for campo in ("cuenta_financiera", "cuenta_financiera_impuesto", "proveedor_comision", *(f"cuenta_{modo}" for modo in MODOS_PAGO)):
            if request.POST.get(campo):
                _identificador(request.POST[campo])


def _validar_cuenta_tercero(terceros, empresa):
    if terceros.filter(cuenta_contable_id__isnull=False).exclude(
        cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk"),
    ).exists():
        raise PermissionDenied("La cuenta contable del cliente o proveedor no corresponde a esta empresa.")


def _validar_proveedores_historial(proveedores, empresa):
    _validar_cuenta_tercero(proveedores, empresa)
    if proveedores.filter(cliente_contable_id__isnull=False).exclude(
        cliente_contable_id__in=ClienteContable.objects.filter(empresa=empresa).values("pk"),
    ).exists():
        raise PermissionDenied("El cliente contable del proveedor no corresponde a esta empresa.")


def validar_factura_cobro_app(empresa, factura):
    if factura.empresa_id != empresa.pk:
        raise PermissionDenied("La factura no corresponde a esta empresa.")
    cliente = Cliente.objects.filter(pk=factura.cliente_id, empresa=empresa)
    if not cliente.exists() or (factura.vendedor_id and not get_user_model().objects.filter(pk=factura.vendedor_id).exists()):
        raise PermissionDenied("Las relaciones de la factura no corresponden a esta empresa.")
    _validar_cuenta_tercero(cliente, empresa)
    pagos = PagoFactura.objects.filter(factura_id=factura.pk, anulado=False)
    _validar_pagos_app(pagos, empresa)
    _validar_proveedores_historial(Proveedor.objects.filter(pk__in=pagos.values("proveedor_comision_id")), empresa)
    comisiones = PagoComisionProveedor.objects.filter(comision_origen_id__in=pagos.values("pk"), comision_origen__anulado=False)
    cuentas_historicas = CuentaFinanciera.objects.filter(
        empresa=empresa, cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk"),
    ).values("pk")
    compensacion_valida = (
        Q(metodo="compensacion", cuenta_financiera__isnull=True,
          comision_origen__comision_retenida_cliente=True,
          comision_origen__factura__empresa__slug="digital_planning", monto__gt=0)
        & Q(monto=F("comision_origen__monto_comision"), fecha=F("comision_origen__fecha"),
            tipo_cambio=F("comision_origen__tipo_cambio"))
    )
    egreso_valido = ~Q(metodo="compensacion") & Q(cuenta_financiera_id__in=cuentas_historicas)
    if (comisiones.exclude(compensacion_valida | egreso_valido).exists()
            or comisiones.filter(registrado_por_id__isnull=False).exclude(registrado_por_id__in=get_user_model().objects.values("pk")).exists()):
        raise PermissionDenied("Las relaciones de pagos de comisión no corresponden a esta empresa.")
    # El saver sincroniza el recibo encontrado por pago, incluso si una
    # importación le asignó otra empresa. No permitir ese retarget accidental.
    recibos = ReciboPago.objects.filter(pago_id__in=pagos.values("pk"))
    if recibos.exclude(empresa_id=empresa.pk).exists():
        raise PermissionDenied("Un recibo relacionado no corresponde a la empresa de la factura.")
    _validar_recibos_app(recibos, empresa)
    notas = NotaCredito.objects.filter(factura_origen_id=factura.pk, estado="emitida")
    if notas.exclude(empresa_id=empresa.pk, cliente_id=factura.cliente_id).exists():
        raise PermissionDenied("Las notas de crédito aplicadas no corresponden a esta factura.")


def cuentas_cobro_app(empresa):
    cuentas = CuentaFinanciera.objects.filter(empresa=empresa, activa=True)
    if cuentas.exclude(cuenta_contable_id__in=CuentaContable.objects.filter(empresa=empresa).values("pk")).exists():
        raise PermissionDenied("Las cuentas financieras contienen una relación incompatible con esta empresa.")
    return cuentas.select_related("cuenta_contable").order_by("nombre")


def validar_selecciones_cobro_app(request, empresa, cuentas):
    if request.method != "POST":
        return
    for campo in ("cuenta_financiera", "cuenta_financiera_impuesto", *(f"cuenta_{modo}" for modo in MODOS_PAGO)):
        valor = request.POST.get(campo)
        if valor and not cuentas.filter(pk=valor).exists():
            raise PermissionDenied("La cuenta seleccionada no está activa o no pertenece a esta empresa.")
    proveedor_id = request.POST.get("proveedor_comision")
    if proveedor_id:
        proveedores = Proveedor.objects.filter(pk=proveedor_id, empresa=empresa, activo=True, cliente_contable__isnull=True)
        if not proveedores.exists():
            raise PermissionDenied("El proveedor seleccionado no está disponible en esta empresa.")
        _validar_cuenta_tercero(proveedores, empresa)


def errores_numericos_cobro_app(request):
    """Sólo evitar comparaciones NaN/Inf fuera del try de la rama original.

    Precisión, redondeo, capacidad, signos y demás reglas siguen en el parser
    y modelo existentes, que difieren legítimamente entre registrar y editar.
    """
    errores = {}
    if request.method == "POST":
        campos = {"monto", "retencion_isr", "retencion_isv", "porcentaje_comision"} | {f"monto_{modo}" for modo in MODOS_PAGO}
        for campo in campos:
            valor = request.POST.get(campo)
            if not valor:
                continue
            try:
                if not Decimal(valor).is_finite():
                    errores[campo] = "Ingresa un valor numérico finito."
            except InvalidOperation:
                pass  # El parser original conserva su error y validación.
    return errores


def _url_cobro(vista, empresa, factura, pago=None):
    args = [empresa.slug, factura.pk] + ([pago.pk] if pago is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1'})}"


def _contexto_cobro_app(request, empresa, factura, pago=None):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    ver_factura = request.user.tiene_permiso_erp("puede_ver_facturas", empresa)
    detalle = _url_cobro("ver_factura", empresa, factura) if ver_factura else ""
    registrar = _url_cobro("registrar_pago", empresa, factura)
    editar = _url_cobro("editar_pago_factura", empresa, factura, pago) if pago is not None else ""
    return {
        "modo_app": True, "app_base_url": inicio, "app_home_disponible": home,
        "app_modules_url": modulos, "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_invoice_detail_url": detalle, "app_payment_back_url": detalle or modulos,
        "app_payment_register_url": registrar, "app_payment_edit_url": editar,
        "app_payment_form_url": editar or registrar,
        "can_register_payment": factura.estado != "anulada" and factura.saldo_pendiente > 0,
        "can_edit_payment": pago is not None and not pago.anulado and factura.estado != "anulada",
    }


def render_cobro_app(request, empresa, contexto, errores=None, readonly_reason=""):
    pago = contexto.get("pago_edicion")
    if pago is not None and request.method == "POST":
        # Un rollback no deshace setters de la instancia original. Recuperar
        # sólo metadata persistida para la tarjeta, sin cambiar el saver ni
        # los valores enviados que se mantienen en form_data.
        pago = PagoFactura.objects.select_related(
            "cuenta_financiera", "cuenta_financiera_impuesto", "proveedor_comision",
        ).get(pk=pago.pk, factura_id=contexto["factura"].pk, factura__empresa=empresa, anulado=False)
        contexto = {**contexto, "pago_edicion": pago}
    contexto = {**contexto, **_contexto_cobro_app(request, empresa, contexto["factura"], pago)}
    datos = dict(contexto["form_data"].items())
    if request.method != "POST" and "fecha" not in datos:
        datos["fecha"] = contexto["today"].isoformat()
    mixto = any(f"monto_{modo}" in request.POST for modo in MODOS_PAGO) if request.method == "POST" else contexto["usa_pagos_mixtos"]
    contexto.update({
        "form_data": datos, "mobile_payment_mode": "mixto" if mixto else "simple",
        "mobile_payment_bound": request.method == "POST",
        "mobile_payment_errors": errores or {},
        "mobile_payment_readonly": bool(readonly_reason),
        "mobile_payment_readonly_reason": readonly_reason,
    })
    return respuesta_cobro_app(render(request, "facturacion/mobile_payment_form.html", contexto))


def respuesta_cobro_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def presentar_mensaje_cobro_app(factura, texto, modo_app):
    # Sólo el mensaje concreto producido por el flujo de cobro. No consumir
    # ni reescribir mensajes ajenos, y mantener textos web/modal intactos.
    return texto.replace("L. ", "$ ") if modo_app and factura.moneda == "USD" else texto


def volver_cobro_app(request, empresa, factura):
    vista = "ver_factura" if request.user.tiene_permiso_erp("puede_ver_facturas", empresa) else "registrar_pago"
    return respuesta_cobro_app(redirect(_url_cobro(vista, empresa, factura)))


def contexto_pagos_factura_app(request, empresa, factura):
    permitido = request.user.tiene_permiso_erp("puede_ver_facturas", empresa) and cobros_disponibles_app(request, empresa)
    contexto = {
        "can_use_invoice_payments": bool(permitido),
        "can_register_invoice_payment": False, "can_edit_invoice_payments": False,
        "app_register_payment_url": "", "mobile_invoice_payment_items": [],
    }
    if not permitido:
        return contexto
    validar_factura_cobro_app(empresa, factura)
    contexto.update({
        "can_register_invoice_payment": factura.estado != "anulada" and factura.saldo_pendiente > 0,
        "can_edit_invoice_payments": factura.estado != "anulada",
        "app_register_payment_url": _url_cobro("registrar_pago", empresa, factura),
    })
    ver_recibos = request.user.tiene_permiso_erp("puede_recibos", empresa)
    recibos = {recibo.pago_id: recibo for recibo in ReciboPago.objects.filter(pago__factura_id=factura.pk)} if ver_recibos else {}
    pagos = PagoFactura.objects.filter(factura=factura, anulado=False).select_related("cuenta_financiera", "cajero").order_by("fecha", "id")
    contexto["mobile_invoice_payment_items"] = [{
        "pago": pago, "fecha": pago.fecha, "metodo_label": pago.get_metodo_display(),
        "referencia": pago.referencia or "", "cuenta": pago.cuenta_financiera.nombre if pago.cuenta_financiera_id else "",
        "cajero": pago.cajero.get_full_name().strip() or pago.cajero.username if pago.cajero_id else "Usuario no disponible",
        "monto": pago.monto, "retenciones": pago.total_retenciones, "total_aplicado": pago.total_aplicado,
        "edit_url": _url_cobro("editar_pago_factura", empresa, factura, pago) if contexto["can_edit_invoice_payments"] else "",
        "recibo_url": f"{reverse('ver_recibo', args=[empresa.slug, recibos[pago.pk].pk])}?app=1" if pago.pk in recibos else "",
    } for pago in pagos]
    return contexto
