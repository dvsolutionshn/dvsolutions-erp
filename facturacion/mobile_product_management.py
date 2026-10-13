"""Presentación APP del ProductoForm y de la baja lógica originales.

Los constructores mantienen sus inicializadores de configuración/bodegas. No
hay un segundo guardado ni reglas de existencias: las tres vistas conservan
sus transacciones, perfiles, costes, distribución y bitácora originales.
"""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .forms import EliminarProductoForm, ProductoForm
from .models import (
    BodegaInventario, ExistenciaLoteBodega, InventarioProducto, LoteInventario,
    Producto, Proveedor,
)


def es_gestion_producto_app(request, empresa):
    """Un marcador inválido también debe pasar por la guarda; fuera de 4, WEB."""
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and ("app" in request.GET or "app" in request.POST)
    )


def gestion_productos_disponible_app(request, empresa):
    """Permisos de acción originales, sin consultar catálogos ni inicializarlos."""
    usuario = request.user
    disponible = bool(
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
    )
    return {
        clave: bool(disponible and usuario.tiene_permiso_erp(permiso, empresa))
        for clave, permiso in (
            ("ver", "puede_productos"), ("crear", "puede_crear_productos"),
            ("editar", "puede_editar_productos"), ("eliminar", "puede_productos"),
        )
    }


def _numero_canonico(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de producto o bodega no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador de producto o bodega no válido.")
    return numero


def _campos_post(form_fields):
    return set(form_fields) | {
        f"initial-{nombre}" for nombre, campo in form_fields.items()
        if campo.show_hidden_initial
    } | {"app", "csrfmiddlewaretoken", "foto-clear"}


def autorizar_gestion_producto_app(request, empresa, accion, producto_id=None):
    if not gestion_productos_disponible_app(request, empresa).get(accion, False):
        raise PermissionDenied("No tiene permiso para esta acción de productos.")
    metodos = {"POST"} if accion == "eliminar" else {"GET", "HEAD", "POST"}
    if request.method not in metodos:
        raise PermissionDenied("Método no disponible en la gestión móvil de productos.")
    if (
        set(request.GET) - {"app"}
        or request.GET.getlist("app") != ["1"]
        or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
    ):
        raise PermissionDenied("Parámetros móviles de producto inválidos.")
    if producto_id is not None:
        identificador = _numero_canonico(producto_id)
        segmentos = request.path.strip("/").split("/")
        if "productos" in segmentos:
            indice = segmentos.index("productos") + 1
            if indice < len(segmentos) and _numero_canonico(segmentos[indice]) != identificador:
                raise PermissionDenied("El producto de la ruta no coincide.")
    if request.method != "POST":
        if request.POST or request.FILES or request.body:
            raise PermissionDenied("La presentación de producto no admite cuerpo o archivos.")
        return
    if (
        request.POST.getlist("app") != ["1"]
        or any(len(request.POST.getlist(clave)) != 1 for clave in request.POST)
        or set(request.FILES) - ({"foto"} if accion != "eliminar" else set())
        or any(len(request.FILES.getlist(clave)) != 1 for clave in request.FILES)
        or ("foto" in request.POST and "foto" in request.FILES)
    ):
        raise PermissionDenied("Datos móviles de producto inválidos.")
    if accion == "eliminar":
        permitidos = set(EliminarProductoForm.base_fields) | {"app", "csrfmiddlewaretoken"}
    else:
        permitidos = _campos_post(ProductoForm.base_fields)
        for clave in request.POST:
            if clave.startswith("stock_bodega_"):
                bodega_id = _numero_canonico(clave.removeprefix("stock_bodega_"))
                if not BodegaInventario.objects.filter(pk=bodega_id, empresa=empresa).exists():
                    raise PermissionDenied("La bodega del formulario no pertenece a esta empresa.")
                permitidos.add(clave)
        for clave in ("impuesto_predeterminado", "categoria_farmaceutica"):
            valor = request.POST.get(clave)
            if valor not in (None, ""):
                _numero_canonico(valor)
    if set(request.POST) - permitidos:
        raise PermissionDenied("El formulario móvil no admite esos campos.")


def validar_formulario_producto_app(request, form):
    """La instancia original define los opcionales y todos los stocks reales.

Se comprueba después del constructor original, pero antes de validar/guardar.
No omitir stocks: el distribuidor original interpreta una omisión como cero.
"""
    if request.method == "POST" and (
        set(request.POST) - _campos_post(form.fields)
        or any(nombre not in request.POST for _, nombre in form.bodega_stock_fields)
    ):
        raise PermissionDenied("Envíe todos los campos de existencias del formulario original.")


def validar_relaciones_gestion_producto_app(empresa, producto):
    """Antes del constructor, sus JOINs de stock y los guardados originales."""
    from .mobile_products import validar_relaciones_productos_app

    base = Producto.objects.filter(pk=producto.pk, empresa=empresa)
    validar_relaciones_productos_app(empresa, base)
    lotes = LoteInventario.objects.filter(producto_id=producto.pk).order_by()
    existencias = ExistenciaLoteBodega.objects.filter(lote_id__in=lotes.values("pk")).order_by()
    if (
        InventarioProducto.objects.filter(producto_id=producto.pk).exclude(empresa_id=empresa.pk).exists()
        or lotes.exclude(empresa_id=empresa.pk).exists()
        or lotes.filter(proveedor_id__isnull=False).exclude(
            proveedor_id__in=Proveedor.objects.filter(empresa=empresa).values("pk"),
        ).exists()
        or existencias.exclude(empresa_id=empresa.pk).exists()
        or existencias.exclude(
            bodega_id__in=BodegaInventario.objects.filter(empresa=empresa).values("pk"),
        ).exists()
    ):
        raise PermissionDenied("Las relaciones de inventario del producto no corresponden a esta empresa.")


def validar_preparacion_bodegas_producto_app(empresa):
    """Luque consulta existencias de todas sus bodegas al preparar el alta.

La revisión sólo protege ese conjunto previo; no cambia la decisión original
de preparar, renombrar, reactivar o desactivar sus bodegas.
"""
    from core.models import ConfiguracionAvanzadaEmpresa

    if empresa.slug != "luque_aestetic" or not ConfiguracionAvanzadaEmpresa.objects.filter(
        empresa=empresa, usa_bodegas_internas=True,
    ).exists():
        return
    bodegas = BodegaInventario.objects.filter(empresa=empresa).values("pk")
    lotes = LoteInventario.objects.filter(
        empresa=empresa, producto_id__in=Producto.objects.filter(empresa=empresa).values("pk"),
    ).filter(
        Q(proveedor_id__isnull=True)
        | Q(proveedor_id__in=Proveedor.objects.filter(empresa=empresa).values("pk")),
    ).values("pk")
    existencias = ExistenciaLoteBodega.objects.filter(
        Q(empresa=empresa) | Q(bodega_id__in=bodegas),
    ).order_by()
    if (
        existencias.exclude(empresa_id=empresa.pk).exists()
        or existencias.exclude(bodega_id__in=bodegas).exists()
        or existencias.exclude(lote_id__in=lotes).exists()
    ):
        raise PermissionDenied("Las existencias usadas al preparar las bodegas tienen relaciones incompatibles.")


def identidad_producto_app(producto):
    return {"id": producto.pk, "nombre": producto.nombre, "codigo": producto.codigo or ""}


def url_gestion_producto_app(vista, empresa, producto=None):
    args = [empresa.slug]
    if producto is not None:
        args.append(producto.pk if hasattr(producto, "pk") else producto["id"])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1'})}"


def _puede_ver_modulos(request, empresa):
    # Las puertas actuales de metadatos no incluyen todos los permisos de
    # edición. Un editor aislado regresa a su propia presentación, sin grant.
    return bool(
        request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_alguna_permision_facturacion_empresa(empresa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa)
    )


def _secciones_producto(form):
    grupos = (
        ("datos", "Datos del artículo", ("nombre", "codigo", "tipo_item", "unidad_medida", "descripcion", "foto", "activo")),
        ("precios", "Precio e impuesto", ("precio", "costo_real_inventario", "impuesto_predeterminado")),
        ("fechas", "Fechas y alertas", ("fecha_referencia", "fecha_alerta", "nota_fecha")),
        ("perfil", "Perfil farmacéutico", (
            "categoria_farmaceutica", "principio_activo", "presentacion", "concentracion",
            "laboratorio", "registro_sanitario", "requiere_receta", "requiere_refrigeracion",
            "producto_controlado", "alerta_vencimiento_dias",
        )),
        ("inventario", "Existencias por bodega", (
            "controla_inventario", "lote_inicial", "vencimiento_lote_inicial",
            *(nombre for _, nombre in form.bodega_stock_fields),
        )),
    )
    secciones, usados = [], set()
    for clave, titulo, nombres in grupos:
        campos = [form[nombre] for nombre in nombres if nombre in form.fields and not form[nombre].is_hidden]
        usados.update(campo.name for campo in campos)
        if campos:
            secciones.append({"key": clave, "titulo": titulo, "campos": campos})
    restantes = [campo for campo in form.visible_fields() if campo.name not in usados]
    if restantes:
        secciones.append({"key": "otros", "titulo": "Otros datos del formulario", "campos": restantes})
    return secciones


def render_gestion_producto_app(request, empresa, form, producto_original=None, delete_form=None):
    from .mobile_products import url_productos_app

    permisos = gestion_productos_disponible_app(request, empresa)
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    puede_modulos = _puede_ver_modulos(request, empresa)
    edicion = bool(producto_original)
    formulario_url = url_gestion_producto_app(
        "editar_producto_facturacion" if edicion else "crear_producto_facturacion", empresa, producto_original,
    )
    for campo in form.fields.values():
        if getattr(campo.widget, "input_type", None) == "date":
            campo.widget.format = "%Y-%m-%d"
    listado = url_productos_app(empresa) if permisos["ver"] else ""
    puede_baja = bool(edicion and permisos["eliminar"] and permisos["editar"])
    contexto = {
        "empresa": {"nombre": empresa.nombre, "slug": empresa.slug}, "modo_app": True,
        "app_home_disponible": False, "app_hide_agenda_navigation": True,
        "app_hide_patient_navigation": True, "can_view_app_modules": puede_modulos,
        "app_modules_url": modulos if puede_modulos else "",
        "can_view_products": permisos["ver"], "can_create_product": permisos["crear"],
        "can_edit_product": permisos["editar"], "can_delete_product": puede_baja,
        "app_products_url": listado,
        "app_product_create_url": url_gestion_producto_app("crear_producto_facturacion", empresa) if permisos["crear"] else "",
        "app_product_form_url": formulario_url,
        "app_product_back_url": listado or (modulos if puede_modulos else formulario_url),
        "app_product_delete_url": url_gestion_producto_app("eliminar_producto_facturacion", empresa, producto_original) if puede_baja else "",
        "producto_actual": producto_original, "mobile_product_editing": edicion,
        "mobile_product_confirmation_name": (producto_original or {}).get("nombre", empresa.nombre),
        "form": form, "mobile_product_sections": _secciones_producto(form),
        "mobile_product_hidden_fields": form.hidden_fields(),
        "delete_form": (delete_form if delete_form is not None else EliminarProductoForm()) if puede_baja else None,
        "mobile_product_delete_bound": bool(delete_form is not None and delete_form.is_bound),
        "mostrar_perfil_farmaceutico": form.mostrar_perfil_farmaceutico,
        "mostrar_bodega_inicial": form.mostrar_bodega_inicial,
        "lector_codigo_activo": form.lector_codigo_activo,
        "titulo": "Editar Producto" if edicion else "Nuevo Producto",
        "texto_boton": "Guardar Cambios" if edicion else "Guardar Producto",
    }
    response = render(request, "facturacion/mobile_product_form.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def volver_gestion_producto_app(request, empresa, producto=None):
    from .mobile_products import url_productos_app

    permisos = gestion_productos_disponible_app(request, empresa)
    if permisos["ver"]:
        return redirect(url_productos_app(empresa))
    if producto is not None and permisos["editar"]:
        return redirect(url_gestion_producto_app("editar_producto_facturacion", empresa, producto))
    if permisos["crear"]:
        return redirect(url_gestion_producto_app("crear_producto_facturacion", empresa))
    if _puede_ver_modulos(request, empresa):
        return redirect("agenda_mobile_modules_screen", empresa_slug=empresa.slug)
    raise PermissionDenied("No hay una pantalla móvil autorizada para este usuario.")
