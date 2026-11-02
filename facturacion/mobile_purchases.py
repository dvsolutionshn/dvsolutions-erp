"""Presentación móvil de las compras y de su registro original.

La lista utiliza los filtros y los siete resúmenes de compras_dashboard; el
detalle y PDF conservan sus consultas y propiedades originales. Registrar una
compra reutiliza el formulario y guardado web, sin otro cálculo o servicio.
Los pagos, anulaciones y demás operaciones separadas no se exponen aquí.
"""

from urllib.parse import urlencode
from types import SimpleNamespace
from decimal import Decimal
import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, RequestDataTooBig, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from contabilidad.models import CuentaContable, CuentaFinanciera
from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from core.read_only_permissions import evaluar_ruta_solo_lectura, rol_solo_lectura
from .models import (
    BodegaInventario, ClienteContable, CompraInventario, ComprobanteEgresoCompra,
    CuentaAcumuladoCompra, EntradaInventarioDocumento, Factura, LineaCompraInventario,
    MovimientoInventario, NotaCredito, PagoCompra, Producto, Proveedor, TipoImpuesto,
)


ESTADOS_FILTRO_COMPRAS = ("borrador", "aplicada", "anulada")
PAGOS_FILTRO_COMPRAS = ("pendiente", "parcial", "pagado")
MAX_BYTES_ALTA_RAPIDA_COMPRA = 16384


def es_compras_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def compras_disponibles_app(request, empresa):
    """Capacidad de consulta existente, sin leer compras ni preparar catálogos."""
    return _accion_compras_disponible_app(request, empresa, "puede_compras")


def _accion_compras_disponible_app(request, empresa, permiso):
    usuario = request.user
    return bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
        and usuario.tiene_permiso_erp(permiso, empresa)
    )


def crear_compra_disponible_app(request, empresa):
    """Crear es una acción independiente del permiso de consultar compras."""
    return bool(
        _accion_compras_disponible_app(request, empresa, "puede_crear_compras")
        and not rol_solo_lectura(request.user, empresa, "inventario")
    )


def puede_aplicar_compra_app(request, empresa):
    return bool(
        crear_compra_disponible_app(request, empresa)
        and request.user.tiene_permiso_erp("puede_aplicar_compras", empresa)
    )


def altas_rapidas_compra_app(request, empresa):
    disponible = crear_compra_disponible_app(request, empresa)
    return {
        "proveedor": bool(disponible and request.user.tiene_permiso_erp("puede_crear_proveedores", empresa)),
        "producto": bool(disponible and request.user.tiene_permiso_erp("puede_crear_productos", empresa)),
    }


def es_alta_rapida_compra_app(request, empresa):
    """Un marcador incorrecto tampoco puede eludir la guarda hacia la web."""
    if "app" in request.GET or "app" in request.POST:
        return True
    # El cliente usa ?app=1; detectar también un marcador en JSON permite
    # rechazarlo explícitamente, sin tratarlo como una creación web normal.
    if request.content_type == "application/json":
        try:
            cuerpo = request.body
            if len(cuerpo) <= MAX_BYTES_ALTA_RAPIDA_COMPRA:
                datos = json.loads(cuerpo.decode("utf-8"))
                return isinstance(datos, dict) and "app" in datos
        except (UnicodeError, ValueError, RecursionError, RequestDataTooBig):
            pass
    return False


def _objeto_json_compra_sin_duplicados(pares):
    datos = {}
    for clave, valor in pares:
        if clave in datos:
            raise ValidationError("El formulario no admite campos JSON duplicados.")
        datos[clave] = valor
    return datos


def datos_alta_rapida_compra_app(request, empresa, tipo):
    """Sólo preparar el payload del creador original; no guardar otro objeto."""
    if not altas_rapidas_compra_app(request, empresa).get(tipo, False):
        raise PermissionDenied("No tiene permiso para crear este registro desde la compra de esta empresa.")
    if (
        request.method != "POST" or request.FILES
        or set(request.GET) != {"app"} or request.GET.getlist("app") != ["1"]
        or evaluar_ruta_solo_lectura(request, empresa, "inventario", "inventario/compras/crear/") is False
    ):
        raise PermissionDenied("Parámetros o acceso móvil no válidos.")
    if request.content_type != "application/json" or request.POST:
        raise ValidationError("Envíe el formulario móvil como un objeto JSON.")
    try:
        cuerpo = request.body
        if len(cuerpo) > MAX_BYTES_ALTA_RAPIDA_COMPRA:
            raise ValidationError("El formulario es demasiado grande.")
        datos = json.loads(cuerpo.decode("utf-8"), object_pairs_hook=_objeto_json_compra_sin_duplicados)
    except (UnicodeError, ValueError, RecursionError, RequestDataTooBig) as exc:
        raise ValidationError("El formulario contiene JSON no válido.") from exc
    if not isinstance(datos, dict) or any(not isinstance(valor, str) for valor in datos.values()):
        raise ValidationError("Cada campo del formulario debe ser texto.")
    campos = (
        {"nombre", "rtn", "contacto", "telefono", "correo", "ciudad", "condicion_pago", "dias_credito"}
        if tipo == "proveedor" else {"nombre", "codigo", "precio", "unidad_medida", "impuesto_id"}
    )
    if set(datos) - campos:
        raise ValidationError("El formulario no admite esos campos.")
    datos = {clave: valor.strip() for clave, valor in datos.items()}
    if tipo == "proveedor":
        from .forms import ProveedorForm

        datos.setdefault("condicion_pago", "contado")
        datos["condicion_pago"] = datos["condicion_pago"] or "contado"
        datos["dias_credito"] = datos.get("dias_credito") or "0"
        form = ProveedorForm({**datos, "activo": True}, instance=Proveedor(empresa=empresa))
        if not form.is_valid():
            raise ValidationError(dict(form.errors))
        datos["dias_credito"] = str(form.cleaned_data.get("dias_credito") or 0)
        return datos
    # ProductoForm inicializa configuración y bodegas. El ModelForm mínimo
    # usa los mismos campos/validadores de Producto sin esos inicializadores.
    ProductoRapidoForm = forms.modelform_factory(
        Producto, fields=["nombre", "codigo", "precio", "unidad_medida", "impuesto_predeterminado"],
    )
    impuesto_id = datos.get("impuesto_id") or ""
    if impuesto_id:
        try:
            _numero_canonico(impuesto_id)
        except PermissionDenied as exc:
            raise ValidationError({"impuesto_id": "Seleccione un impuesto activo válido."}) from exc
    else:
        impuesto_id = str(TipoImpuesto.objects.filter(activo=True).order_by("nombre").values_list("pk", flat=True).first() or "")
    form = ProductoRapidoForm({
        **{clave: valor for clave, valor in datos.items() if clave != "impuesto_id"},
        "precio": datos.get("precio") or "0", "unidad_medida": datos.get("unidad_medida") or "unidad",
        "impuesto_predeterminado": impuesto_id,
    }, instance=Producto(empresa=empresa, tipo_item="producto", controla_inventario=True, activo=True))
    form.fields["impuesto_predeterminado"].queryset = TipoImpuesto.objects.filter(activo=True)
    form.fields["impuesto_predeterminado"].required = True
    if not form.is_valid():
        raise ValidationError(dict(form.errors))
    if form.cleaned_data["precio"] < Decimal("0"):
        raise ValidationError({"precio": "El precio no puede ser negativo."})
    datos.update({
        "precio": str(form.cleaned_data["precio"]), "unidad_medida": form.cleaned_data["unidad_medida"],
        "impuesto_id": str(form.cleaned_data["impuesto_predeterminado"].pk),
    })
    return datos


def cuentas_compra_app(empresa):
    """Consultar cuentas configuradas, sin inicializar catálogos al abrir la app."""
    return CuentaFinanciera.objects.filter(
        empresa=empresa, activa=True, cuenta_contable__empresa=empresa,
        cuenta_contable__activa=True, cuenta_contable__acepta_movimientos=True,
    ).select_related("cuenta_contable").order_by("nombre")


def autorizar_crear_compra_app(request, empresa):
    if not crear_compra_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para registrar compras en esta empresa.")
    if request.method not in {"GET", "HEAD", "POST"} or request.FILES:
        raise PermissionDenied("Método o archivos no admitidos al registrar la compra móvil.")
    if evaluar_ruta_solo_lectura(request, empresa, "inventario", "inventario/compras/crear/") is False:
        raise PermissionDenied("El acceso de sólo lectura no permite registrar compras.")
    if (
        set(request.GET) - {"app"}
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
        or ("app" in request.GET and request.GET.getlist("app") != ["1"])
        or ("app" in request.POST and request.POST.getlist("app") != ["1"])
        or (request.GET.getlist("app") != ["1"] and request.POST.getlist("app") != ["1"])
    ):
        raise PermissionDenied("Parámetros de registro móvil inválidos.")
    if request.method != "POST":
        if request.POST or request.body:
            raise PermissionDenied("La consulta del formulario no admite cuerpo.")
        return
    if any(len(request.POST.getlist(clave)) != 1 for clave in request.POST):
        raise PermissionDenied("El registro móvil no admite campos duplicados.")
    total = _numero_canonico(request.POST.get("lineas_compra-TOTAL_FORMS", ""))
    if total > 100 or request.POST.get("lineas_compra-INITIAL_FORMS") != "0":
        raise PermissionDenied("Envíe entre una y cien líneas nuevas para la compra.")
    permitidos = {
        "app", "csrfmiddlewaretoken", "proveedor", "proveedor_nombre", "proveedor_busqueda",
        "referencia_documento", "fecha_documento", "condicion_pago", "metodo_pago",
        "cuenta_financiera_pago", "dias_credito", "fecha_vencimiento", "observacion", "estado",
        "lineas_compra-TOTAL_FORMS", "lineas_compra-INITIAL_FORMS",
        "lineas_compra-MIN_NUM_FORMS", "lineas_compra-MAX_NUM_FORMS",
    }
    for indice in range(total):
        prefijo = f"lineas_compra-{indice}"
        permitidos.update(f"{prefijo}-{campo}" for campo in (
            "producto", "cantidad", "costo_unitario", "descuento_porcentaje", "comentario",
            "impuesto", "DELETE", "id", "compra",
        ))
        if request.POST.get(f"{prefijo}-id") or request.POST.get(f"{prefijo}-compra"):
            raise PermissionDenied("La compra nueva no admite líneas o documentos existentes.")
    if set(request.POST) - permitidos:
        raise PermissionDenied("El formulario de compra móvil no admite esos campos.")
    if request.POST.get("estado") == "aplicada" and not puede_aplicar_compra_app(request, empresa):
        raise PermissionDenied("No tiene permiso para aplicar compras al inventario.")


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
    puede_consultar = compras_disponibles_app(request, empresa)
    contexto = {
        "empresa": empresa, "modo_app": True, "app_base_url": inicio,
        "app_home_disponible": home, "app_modules_url": modulos,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "can_view_purchases": puede_consultar, "can_view_purchase": puede_consultar,
        "can_download_purchase": puede_consultar,
        "can_view_supplier": puede_proveedor,
        "can_view_egresos": False,
        "app_purchases_url": listado, "app_purchases_search_url": listado,
        "app_purchase_back_url": listado if compra is not None else modulos,
        "app_purchase_supplier_url": url_compra_app("ver_proveedor_facturacion", empresa, proveedor) if puede_proveedor else "",
    }
    for accion in ("create", "edit", "apply", "void", "delete", "pay"):
        contexto[f"can_{accion}_purchase"] = False
        contexto[f"app_purchase_{accion}_url"] = ""
    contexto["can_create_purchase"] = compra is None and crear_compra_disponible_app(request, empresa)
    if contexto["can_create_purchase"]:
        contexto["app_purchase_create_url"] = url_compra_app("crear_compra", empresa)
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


def render_crear_compra_app(request, empresa, contexto):
    """Adaptar campos y navegación, sin validar ni guardar otro documento."""
    form, formset = contexto["form"], contexto["formset"]
    for nombre in ("fecha_documento", "fecha_vencimiento"):
        form.fields[nombre].widget = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
    # empty_form no pertenece a formset.forms: necesita los mismos catálogos
    # y atributos que la configuración original da a las líneas actuales.
    from .views import _configurar_lineas_compra_formset

    empty_form = formset.empty_form
    _configurar_lineas_compra_formset(
        SimpleNamespace(forms=[empty_form]), contexto["productos"], contexto["impuestos"],
    )
    for linea in [*formset.forms, empty_form]:
        linea.fields["producto"].label_from_instance = lambda producto: (
            f"{producto.nombre} · {producto.codigo}" if producto.codigo else producto.nombre
        )
    form.fields["observacion"].widget.attrs["rows"] = 3
    contexto = {**contexto, **_contexto_compras_app(request, empresa)}
    puede_consultar = contexto["can_view_purchases"]
    puede_aplicar = puede_aplicar_compra_app(request, empresa)
    altas = altas_rapidas_compra_app(request, empresa)
    contexto.update({
        "app_purchase_form_action": url_compra_app("crear_compra", empresa),
        "app_purchase_back_url": contexto["app_purchases_url"] if puede_consultar else contexto["app_modules_url"],
        "app_purchase_can_apply": puede_aplicar, "can_apply_purchase": puede_aplicar,
        "app_purchase_empty_form": empty_form, "empty_line_form": empty_form,
        "can_create_purchase_supplier": altas["proveedor"], "can_create_purchase_product": altas["producto"],
        "app_purchase_supplier_create_url": url_compra_app("compra_crear_proveedor_rapido", empresa) if altas["proveedor"] else "",
        "app_purchase_product_create_url": url_compra_app("compra_crear_producto_rapido", empresa) if altas["producto"] else "",
        "mobile_purchase_supplier_conditions": Proveedor.CONDICIONES_PAGO,
        "mobile_purchase_product_units": Producto.UNIDADES_MEDIDA,
        "mobile_purchase_product_taxes": contexto["impuestos"],
    })
    return respuesta_compras_app(render(request, "facturacion/mobile_purchase_form.html", contexto))


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
