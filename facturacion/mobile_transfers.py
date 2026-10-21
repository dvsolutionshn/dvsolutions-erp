"""Permisos, preparación y presentación del traslado rápido existente.

No inicializa inventario ni sustituye sus movimientos. El guardado permanece
en traslado_rapido_farmaceutico con sus dos movimientos y auditoría originales.
"""

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from core.models import ConfiguracionAvanzadaEmpresa
from .models import BodegaInventario, ExistenciaLoteBodega, MovimientoLoteBodega


def es_transferencia_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa)
        and ("1" in request.GET.getlist("app") or "1" in request.POST.getlist("app"))
    )


def transferencia_disponible_app(request, empresa):
    """Catálogo puro: configuración pendiente no concede ni oculta permisos."""
    usuario = request.user
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("facturacion")
    ):
        return False
    permiso = "puede_transferir_inventario" if rol_clinico_granular(usuario, empresa) else "puede_inventario"
    return usuario.tiene_permiso_erp(permiso, empresa)


def _identificador(valor):
    if not valor or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        raise PermissionDenied("Identificador de inventario inválido.")
    numero = int(valor)
    if numero <= 0 or numero > 9223372036854775807 or str(numero) != valor:
        raise PermissionDenied("Identificador de inventario inválido.")
    return numero


def _validar_solicitud(request, empresa):
    if request.method not in {"GET", "HEAD", "POST"}:
        raise PermissionDenied("Método no permitido para el traslado móvil.")
    selectores = empresa.slug == "medical_spa"
    seleccion = {"bodega_origen", "bodega_destino"} if selectores else {"ruta"}
    permitidos_get = {"app"} | seleccion
    if set(request.GET) - permitidos_get or any(len(request.GET.getlist(k)) != 1 for k in request.GET):
        raise PermissionDenied("Selección de traslado inválida.")
    if "app" in request.GET and request.GET.getlist("app") != ["1"]:
        raise PermissionDenied("Modo de app inválido.")
    if request.method == "POST":
        requeridos = {"app", "existencia", "cantidad"} | seleccion
        permitidos = requeridos | {"observacion", "csrfmiddlewaretoken"}
        if (
            set(request.POST) - permitidos or not requeridos.issubset(request.POST)
            or any(len(request.POST.getlist(k)) != 1 for k in request.POST)
            or request.POST.getlist("app") != ["1"]
        ):
            raise PermissionDenied("Datos de traslado inválidos.")
        if any(campo in request.GET and request.GET[campo] != request.POST[campo] for campo in seleccion):
            raise PermissionDenied("La selección del formulario no coincide con la ruta actual.")
    rutas = {"principal_clinica", "clinica_principal"} if empresa.slug == "luque_aestetic" else {
        "principal_provisional", "principal_vitrina", "provisional_vitrina",
    }
    for datos in (request.GET, request.POST if request.method == "POST" else {}):
        if not selectores and "ruta" in datos and datos["ruta"] not in rutas:
            raise PermissionDenied("Ruta de traslado inválida.")
        for campo in ("bodega_origen", "bodega_destino", "existencia"):
            if campo in datos and datos[campo]:
                _identificador(datos[campo])
            elif campo in datos and datos is request.GET:
                raise PermissionDenied("Selecciona una bodega válida.")


def _validar_integridad(empresa):
    # También revisa filas importadas de otra empresa que ocupen una bodega
    # propia: su clave única podría colisionar al registrar la entrada.
    existencias = ExistenciaLoteBodega.objects.filter(Q(empresa=empresa) | Q(bodega__empresa=empresa))
    if existencias.exclude(
        empresa=empresa, bodega__empresa=empresa, lote__empresa=empresa,
        lote__producto__empresa=empresa,
    ).exists() or existencias.filter(lote__proveedor__isnull=False).exclude(lote__proveedor__empresa=empresa).exists():
        raise PermissionDenied("Las relaciones de inventario no pertenecen a la empresa activa.")


def preparar_transferencia_app(request, empresa):
    if not transferencia_disponible_app(request, empresa):
        raise PermissionDenied("No tiene permiso para transferir inventario en esta empresa.")
    _validar_solicitud(request, empresa)
    configuracion = ConfiguracionAvanzadaEmpresa.objects.filter(empresa=empresa).first()
    resultado = {"config_avanzada": configuracion, "bodegas_base": {}, "ready": False, "reason": ""}
    if not configuracion or not configuracion.usa_bodegas_internas:
        resultado["reason"] = "Los traslados internos no están habilitados. Solicita al administrador revisar la configuración existente."
        return resultado
    bodegas = list(BodegaInventario.objects.filter(empresa=empresa, activa=True).order_by("id"))
    if empresa.slug == "medical_spa":
        listas = len(bodegas) >= 2
    else:
        nombres = (("Bodega Principal", "principal"), ("Bodega Clinica", "provisional")) if empresa.slug == "luque_aestetic" else (
            (("Bodega General", "principal"), ("Bodega Hospital", "provisional"), ("Vitrina", "vitrina")) if empresa.slug == "hospital_mia" else
            (("Bodega principal", "principal"), ("Bodega provisional", "provisional"), ("Vitrina", "vitrina"))
        )
        # Mismo registro preferido por _asegurar_bodegas_farmaceuticas, pero
        # sin su creación, cambio de tipo, renombrado ni reactivación.
        for nombre, tipo in nombres:
            resultado["bodegas_base"][tipo] = (
                BodegaInventario.objects.filter(empresa=empresa, nombre__iexact=nombre).first()
                or BodegaInventario.objects.filter(empresa=empresa, tipo=tipo).order_by("id").first()
            )
        listas = all(bodega and bodega.activa and bodega.tipo == tipo for tipo, bodega in resultado["bodegas_base"].items())
    if not listas:
        resultado["reason"] = "Faltan bodegas activas para realizar este traslado. El administrador debe revisar las bodegas existentes; la app no las crea ni modifica."
        return resultado
    _validar_integridad(empresa)
    if request.method == "POST" and request.POST["existencia"] and not ExistenciaLoteBodega.objects.filter(
        empresa=empresa, pk=request.POST["existencia"],
    ).exists():
        raise Http404("El lote no pertenece a esta empresa.")
    propias = {str(bodega.pk) for bodega in bodegas}
    if empresa.slug == "medical_spa":
        for datos in (request.GET, request.POST if request.method == "POST" else {}):
            for campo in ("bodega_origen", "bodega_destino"):
                if datos.get(campo) and datos[campo] not in propias:
                    raise Http404("La bodega no está activa en esta empresa.")
        if request.method != "POST" and request.GET.get("bodega_origen") and request.GET.get("bodega_origen") == request.GET.get("bodega_destino"):
            raise PermissionDenied("Selecciona bodegas diferentes.")
    resultado["ready"] = True
    return resultado


def validar_cantidad_transferencia_app(request, contexto):
    """Evita valores que el DecimalField original no puede almacenar."""
    errores = contexto.setdefault("mobile_transfer_errors", {})
    existencia_id = request.POST["existencia"]
    if not existencia_id:
        errores["existencia"] = "Selecciona un lote disponible y una cantidad mayor que cero."
        return errores["existencia"]
    existencia = ExistenciaLoteBodega.objects.filter(pk=existencia_id, empresa=contexto["empresa"]).first()
    if not existencia or existencia.bodega_id != getattr(contexto["ruta"]["origen"], "pk", None):
        raise Http404("El lote no pertenece a la bodega de origen seleccionada.")
    try:
        raw = request.POST["cantidad"].strip()
        if len(raw) > 128:
            raise ValidationError("Cantidad inválida.")
        cantidad = Decimal(raw)
        if not cantidad.is_finite():
            raise ValidationError("Cantidad inválida.")
        campo = MovimientoLoteBodega._meta.get_field("cantidad")
        campo.run_validators(cantidad)
    except (InvalidOperation, ValidationError, ValueError):
        errores["cantidad"] = "Ingresa una cantidad válida de hasta 10 enteros y 2 decimales."
        return errores["cantidad"]
    destino = contexto["ruta"]["destino"]
    if destino and cantidad > 0 and cantidad <= existencia.cantidad:
        referencia = f"Rapido {contexto['ruta']['origen'].nombre} -> {destino.nombre}"
        try:
            MovimientoLoteBodega._meta.get_field("referencia").run_validators(referencia)
        except ValidationError:
            errores["transferencia"] = "Los nombres de las bodegas exceden la longitud permitida para la referencia del traslado. Solicita al administrador revisar sus nombres."
            return errores["transferencia"]
        anterior = ExistenciaLoteBodega.objects.filter(
            empresa=contexto["empresa"], bodega=destino, lote=existencia.lote,
        ).values_list("cantidad", flat=True).first() or Decimal("0.00")
        try:
            ExistenciaLoteBodega._meta.get_field("cantidad").run_validators(anterior + cantidad)
        except ValidationError:
            errores["cantidad"] = "La cantidad supera la capacidad de existencia de la bodega destino."
            return errores["cantidad"]
    return ""


def url_transferencia_app(empresa, **parametros):
    return f"{reverse('traslado_rapido_farmaceutico', args=[empresa.slug])}?{urlencode({'app': '1', **parametros})}"


def _contexto_app(request, contexto):
    from crm.mobile_modules import inicio_app_disponible, url_modulos_app

    empresa = contexto["empresa"]
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    home = inicio_app_disponible(request, empresa) and empresa.tiene_modulo_activo("agenda_citas")
    # La ayuda general considera el inicio legado disponible. Esta pantalla
    # nueva no envía a un usuario de inventario a una agenda no autorizada.
    if not rol_clinico_granular(request.user, empresa) and not request.user.tiene_permiso_erp("puede_citas", empresa):
        home = False
    modulos = url_modulos_app(request, empresa) if home else reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    ruta = contexto.get("ruta", {})
    parametros = {"ruta": contexto.get("ruta_key", "")} if not contexto.get("usar_selectores_bodega") else {
        key: str(getattr(ruta.get(tipo), "pk", "")) for key, tipo in (("bodega_origen", "origen"), ("bodega_destino", "destino"))
    }
    parametros = {k: v for k, v in parametros.items() if v}
    valores = {key: request.POST.get(key, "") if request.method == "POST" else "" for key in ("existencia", "cantidad", "observacion")}
    valores.update(parametros)
    if request.method == "POST":
        valores.update({key: request.POST.get(key, "") for key in ("ruta", "bodega_origen", "bodega_destino")})
    contexto.update({
        "modo_app": True, "app_base_url": inicio, "app_home_disponible": home,
        "app_invoices_url": f"{inicio}#facturas-app", "app_modules_url": modulos,
        "app_patients_url": f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1",
        "app_transfer_url": url_transferencia_app(empresa), "app_transfer_form_url": url_transferencia_app(empresa, **parametros),
        "valores": valores, "mobile_transfer_quantity_raw": valores["cantidad"],
        "mobile_transfer_ready": contexto.get("mobile_transfer_ready", True),
        "mobile_transfer_unavailable_reason": contexto.get("mobile_transfer_unavailable_reason", ""),
        "mobile_transfer_errors": contexto.get("mobile_transfer_errors", {}),
        "mobile_transfer_routes": [
            {"codigo": key, "nombre": item["label"], "url": url_transferencia_app(empresa, ruta=key), "active": key == contexto.get("ruta_key")}
            for key, item in contexto.get("rutas", {}).items()
        ],
        "mobile_transfer_items": [
            {"existencia": item, "id": item.pk, "producto": item.lote.producto,
             "nombre": item.lote.producto.nombre, "codigo": item.lote.producto.codigo,
             "lote": item.lote.numero_lote, "vencimiento": item.lote.fecha_vencimiento,
             "cantidad": item.cantidad, "vencido": item.lote.vencido, "por_vencer": item.lote.por_vencer,
             "lote_activo": item.lote.activo, "selected": str(item.pk) == valores["existencia"]}
            for item in contexto.get("existencias", ())
        ],
    })


def respuesta_transferencia_app(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def render_transferencia_app(request, contexto):
    _contexto_app(request, contexto)
    return respuesta_transferencia_app(render(request, "facturacion/mobile_transfer.html", contexto))


def volver_transferencia_app(empresa, ruta, ruta_key, usar_selectores_bodega):
    parametros = {"bodega_origen": ruta["origen"].pk, "bodega_destino": ruta["destino"].pk} if usar_selectores_bodega else {"ruta": ruta_key}
    return respuesta_transferencia_app(redirect(url_transferencia_app(empresa, **parametros)))
