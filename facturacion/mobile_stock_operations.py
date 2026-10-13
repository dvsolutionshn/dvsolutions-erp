"""Presentación móvil de las entradas por lote y traslados existentes.

Estas vistas originales usan POST manual, no Forms. Los descriptores sólo
presentan sus mismos campos; movimientos, FEFO e inicializadores siguen en
views.py. No es un servicio nuevo ni una consulta sin efectos.
"""

from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .models import (
    BodegaInventario, ExistenciaLoteBodega, InventarioProducto, LoteInventario,
    MovimientoLoteBodega, Producto, Proveedor,
)


CAMPOS_LOTE_APP = frozenset({
    "producto", "bodega", "numero_lote", "cantidad", "fecha_vencimiento",
    "fecha_vencimiento_rapida", "proveedor", "observacion",
})
CAMPOS_TRASLADO_APP = frozenset({"existencia", "bodega_destino", "cantidad", "observacion"})
VISTAS_STOCK_APP = {
    "entrada_lote": "crear_lote_inventario",
    "transferencia": "traslado_inventario_farmaceutico",
    "entrada": "entrada_inventario",
}


def es_stock_app(request, empresa):
    return bool(empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and (
        "app" in request.GET or "app" in request.POST
    ))


def stock_disponible_app(request, empresa):
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and empresa.activa and interfaz_clinica_activa(empresa)
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp("puede_inventario", empresa)
    )


def _identificador(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de inventario no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador de inventario no válido.")
    return numero


def autorizar_stock_app(request, empresa, operacion):
    if not stock_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para gestionar este inventario.")
    metodos = {"GET", "HEAD"} if operacion == "entrada" else {"GET", "HEAD", "POST"}
    if request.method not in metodos or request.FILES:
        raise PermissionDenied("Método o archivos no admitidos en esta pantalla.")
    permitidos_get = {"app", "producto"} if operacion in {"entrada_lote", "entrada"} else {"app"}
    if (
        set(request.GET) - permitidos_get
        or any(len(request.GET.getlist(k)) != 1 for k in request.GET)
        or request.GET.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Parámetros de inventario móvil no válidos.")
    if "producto" in request.GET:
        producto_id = _identificador(request.GET["producto"])
        if not Producto.objects.filter(pk=producto_id, empresa=empresa, activo=True, controla_inventario=True).exists():
            raise Http404("El producto no está disponible en esta empresa.")
    if request.method != "POST":
        if request.POST or request.body:
            raise PermissionDenied("La apertura no admite datos o archivos.")
        return
    campos = CAMPOS_LOTE_APP if operacion == "entrada_lote" else CAMPOS_TRASLADO_APP
    if (
        set(request.POST) - (campos | {"app", "csrfmiddlewaretoken"})
        or any(len(request.POST.getlist(k)) != 1 for k in request.POST)
        or request.POST.getlist("app") != ["1"]
    ):
        raise PermissionDenied("Campos de inventario móvil no válidos.")
    modelos = {"producto": Producto, "bodega": BodegaInventario, "proveedor": Proveedor,
               "existencia": ExistenciaLoteBodega, "bodega_destino": BodegaInventario}
    for nombre, modelo in modelos.items():
        if nombre in campos and request.POST.get(nombre):
            if not modelo.objects.filter(pk=_identificador(request.POST[nombre]), empresa=empresa).exists():
                raise Http404("La selección no pertenece a esta empresa.")


def validar_relaciones_stock_app(empresa):
    """Verificar el alcance antes de inicializar bodegas o presentar relaciones."""
    productos = Producto.objects.filter(empresa=empresa).values("pk")
    bodegas = BodegaInventario.objects.filter(empresa=empresa).values("pk")
    lotes = LoteInventario.objects.filter(Q(empresa=empresa) | Q(producto_id__in=productos))
    if (
        lotes.exclude(empresa_id=empresa.pk, producto_id__in=productos).exists()
        or lotes.filter(proveedor_id__isnull=False).exclude(
            proveedor_id__in=Proveedor.objects.filter(empresa=empresa).values("pk"),
        ).exists()
        or InventarioProducto.objects.filter(Q(empresa=empresa) | Q(producto_id__in=productos)).exclude(
            empresa_id=empresa.pk, producto_id__in=productos,
        ).exists()
    ):
        raise PermissionDenied("Los lotes o inventarios contienen relaciones de otra empresa.")
    propias = LoteInventario.objects.filter(empresa=empresa).values("pk")
    existencias = ExistenciaLoteBodega.objects.filter(
        Q(empresa=empresa) | Q(bodega_id__in=bodegas) | Q(lote_id__in=propias)
    )
    if existencias.exclude(empresa_id=empresa.pk, bodega_id__in=bodegas, lote_id__in=propias).exists():
        raise PermissionDenied("Las existencias contienen relaciones de otra empresa.")


def errores_estructura_stock_app(request, empresa, operacion):
    """Límites de almacenamiento originales, sin duplicar las reglas del POST."""
    errores = {}
    if request.method != "POST":
        return errores
    # Los selectores vacíos del HTML no son un ID para consultar en el ORM.
    # La vista original ya exige estas selecciones antes de mover existencias.
    selectores = ("producto", "bodega") if operacion == "entrada_lote" else ("existencia", "bodega_destino")
    for nombre in selectores:
        if not request.POST.get(nombre):
            errores[nombre] = "Seleccione una opción de esta empresa antes de continuar."
    raw = request.POST.get("cantidad", "").strip()
    cantidad = None
    if raw:
        try:
            if len(raw) > 128:
                raise ValidationError("Cantidad demasiado larga.")
            cantidad = Decimal(raw)
            if not cantidad.is_finite():
                raise ValidationError("Cantidad no finita.")
            MovimientoLoteBodega._meta.get_field("cantidad").run_validators(cantidad)
        except (InvalidOperation, ValidationError, ValueError):
            errores["cantidad"] = "Escriba una cantidad válida de hasta 10 enteros y 2 decimales."
            cantidad = None
    if operacion == "entrada_lote":
        try:
            LoteInventario._meta.get_field("numero_lote").run_validators(request.POST.get("numero_lote", "").strip())
        except ValidationError:
            errores["numero_lote"] = "El número de lote admite hasta 80 caracteres."
        vencimiento = request.POST.get("fecha_vencimiento", "")
        if vencimiento:
            try:
                LoteInventario._meta.get_field("fecha_vencimiento").clean(vencimiento, None)
            except ValidationError:
                errores["fecha_vencimiento"] = "Seleccione una fecha válida. También puede utilizar vencimiento rápido."
        elif request.POST.get("fecha_vencimiento_rapida", "").strip():
            from .views import _parse_vencimiento_lote_rapido

            try:
                _parse_vencimiento_lote_rapido(request.POST["fecha_vencimiento_rapida"])
            except ValidationError as exc:
                errores["fecha_vencimiento_rapida"] = "; ".join(exc.messages)
            except (ValueError, OverflowError):
                errores["fecha_vencimiento_rapida"] = "El año del vencimiento rápido no cabe en una fecha válida."
        producto = Producto.objects.filter(pk=request.POST.get("producto") or None, empresa=empresa).first()
        numero = request.POST.get("numero_lote", "").strip()
        lote = LoteInventario.objects.filter(empresa=empresa, producto=producto, numero_lote=numero).first() if producto else None
        bodega_id = request.POST.get("bodega") or None
        anterior = ExistenciaLoteBodega.objects.filter(empresa=empresa, lote=lote, bodega_id=bodega_id).values_list("cantidad", flat=True).first() if lote else None
        general = InventarioProducto.objects.filter(empresa=empresa, producto=producto).values_list("existencias", flat=True).first() if producto else None
        limites = [(ExistenciaLoteBodega, "cantidad", anterior), (InventarioProducto, "existencias", general)]
    else:
        existencia = ExistenciaLoteBodega.objects.select_related("bodega").filter(
            pk=request.POST.get("existencia") or None, empresa=empresa,
        ).first()
        destino = BodegaInventario.objects.filter(pk=request.POST.get("bodega_destino") or None, empresa=empresa).first()
        anterior = ExistenciaLoteBodega.objects.filter(empresa=empresa, lote_id=existencia.lote_id, bodega=destino).values_list("cantidad", flat=True).first() if existencia and destino else None
        limites = [(ExistenciaLoteBodega, "cantidad", anterior)]
        if existencia and destino:
            try:
                MovimientoLoteBodega._meta.get_field("referencia").run_validators(
                    f"Traslado {existencia.bodega.nombre} -> {destino.nombre}",
                )
            except ValidationError:
                errores["bodega_destino"] = "Los nombres de las bodegas exceden la longitud admitida para la referencia."
    if cantidad is not None and cantidad > 0:
        for modelo, campo, anterior in limites:
            try:
                modelo._meta.get_field(campo).run_validators((anterior or Decimal("0.00")) + cantidad)
            except ValidationError:
                errores["cantidad"] = "La cantidad supera la capacidad de almacenamiento del inventario."
    return errores


def url_stock_app(empresa, operacion="entrada_lote", **parametros):
    return f"{reverse(VISTAS_STOCK_APP[operacion], args=[empresa.slug])}?{urlencode({'app': '1', **parametros})}"


def volver_stock_app(empresa):
    return redirect("agenda_mobile_inventory", empresa_slug=empresa.slug)


def acciones_stock_app(request, empresa):
    if not stock_disponible_app(request, empresa):
        return {}
    return {"app_stock_entry_url": url_stock_app(empresa), "app_stock_transfer_url": url_stock_app(empresa, "transferencia")}


def render_stock_app(request, empresa, operacion, *, productos=(), proveedores=(), bodegas=(),
                     existencias=(), bodega_principal=None, errores=None, readiness_reason=""):
    errores = errores or {}
    bound = request.method == "POST"
    valores = request.POST if bound else {}

    def campo(nombre, label, tipo="text", opciones=(), initial="", required=False, help_text=""):
        value = valores.get(nombre, initial)
        return {"name": nombre, "label": label, "type": tipo, "value": value,
                "required": required, "step": "0.01" if tipo == "number" else "",
                "help_text": help_text, "errors": [errores[nombre]] if nombre in errores else [],
                "choices": [{"value": str(pk), "label": titulo, "selected": str(value) == str(pk)} for pk, titulo in opciones]}

    if operacion == "entrada_lote":
        titulo = "Agregar existencia por lote"
        campos = [
            campo("producto", "Producto", "select", [(p.pk, f"{p.nombre} · {p.codigo}" if p.codigo else p.nombre) for p in productos], request.GET.get("producto", ""), True),
            campo("bodega", "Bodega de destino", "select", [(b.pk, b.nombre) for b in bodegas], getattr(bodega_principal, "pk", ""), True),
            campo("numero_lote", "Número de lote", required=True),
            campo("cantidad", "Cantidad a ingresar", "number", required=True),
            campo("fecha_vencimiento", "Fecha de vencimiento", "date", help_text="FEFO requiere vencimiento: seleccione la fecha o utilice el vencimiento rápido original."),
            campo("fecha_vencimiento_rapida", "Vencimiento rápido", help_text="Ejemplo: ago-27 o dic-26. Se utiliza el último día del mes con las reglas actuales."),
            campo("proveedor", "Proveedor", "select", [(p.pk, p.nombre) for p in proveedores]),
            campo("observacion", "Observación", "textarea"),
        ]
        grupos = [("destino", "Producto y bodega", campos[:2]), ("lote", "Lote y cantidad", campos[2:6]), ("referencia", "Datos adicionales", campos[6:])]
    else:
        titulo = "Traslado entre bodegas"
        campos = [
            campo("existencia", "Lote de origen", "select", [(e.pk, f"{e.lote.producto.nombre} · {e.lote.numero_lote} · {e.bodega.nombre} · {e.cantidad}") for e in existencias], required=True),
            campo("bodega_destino", "Bodega de destino", "select", [(b.pk, b.nombre) for b in bodegas], required=True),
            campo("cantidad", "Cantidad a trasladar", "number", required=True),
            campo("observacion", "Observación", "textarea"),
        ]
        grupos = [("traslado", "Origen, destino y cantidad", campos[:3]), ("referencia", "Datos adicionales", campos[3:])]
    contexto = {
        "empresa": SimpleNamespace(nombre=empresa.nombre, slug=empresa.slug), "modo_app": True,
        "app_home_disponible": False, "app_hide_agenda_navigation": True, "app_hide_patient_navigation": True,
        "can_view_app_modules": True, "app_modules_url": reverse("agenda_mobile_modules_screen", args=[empresa.slug]),
        "app_stock_back_url": reverse("agenda_mobile_inventory", args=[empresa.slug]),
        "app_stock_form_url": url_stock_app(empresa, operacion), "mobile_stock_operation": operacion,
        "mobile_stock_title": titulo, "mobile_stock_confirmation_name": empresa.nombre,
        "mobile_stock_sections": [{"key": k, "titulo": t, "campos": c} for k, t, c in grupos],
        "mobile_stock_bound": bound, "mobile_stock_errors": errores, "readiness_reason": readiness_reason,
    }
    response = render(request, "facturacion/mobile_stock_operation.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
