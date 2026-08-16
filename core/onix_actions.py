from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from facturacion.models import (
    EMPRESAS_FACTURACION_SOLO_CONTADO,
    EMPRESAS_PRECIO_FINAL_CON_IMPUESTO,
    Cliente,
    Factura,
    LineaFactura,
    Producto,
    TipoImpuesto,
)

from .models import AccionOnix, ConfiguracionOnix


DOS_DECIMALES = Decimal("0.01")
MAXIMO_LINEAS_FACTURA = 30
MAXIMO_TOTAL_FACTURA = Decimal("9999999999.99")


def _decimal(valor, etiqueta, *, minimo=None, maximo=None, predeterminado=None, decimales=None):
    if valor in (None, ""):
        if predeterminado is not None:
            return Decimal(str(predeterminado))
        raise ValidationError(f"Falta indicar {etiqueta}.")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{etiqueta.capitalize()} no es un numero valido.") from exc
    if not numero.is_finite():
        raise ValidationError(f"{etiqueta.capitalize()} no es un numero valido.")
    if decimales is not None and max(-numero.as_tuple().exponent, 0) > decimales:
        raise ValidationError(f"{etiqueta.capitalize()} admite como maximo {decimales} decimales.")
    if minimo is not None and numero < Decimal(str(minimo)):
        raise ValidationError(f"{etiqueta.capitalize()} debe ser mayor o igual a {minimo}.")
    if maximo is not None and numero > Decimal(str(maximo)):
        raise ValidationError(f"{etiqueta.capitalize()} no puede superar {maximo}.")
    return numero


def _fecha(valor, etiqueta, *, predeterminada=None):
    if valor in (None, ""):
        return predeterminada
    fecha = parse_date(str(valor).strip())
    if not fecha:
        raise ValidationError(f"{etiqueta.capitalize()} debe usar el formato AAAA-MM-DD.")
    return fecha


def _calcular_linea(*, cantidad, precio_unitario, descuento_porcentaje, impuesto_porcentaje, precio_incluye_impuesto):
    subtotal_base = (cantidad * precio_unitario).quantize(DOS_DECIMALES)
    descuento = descuento_porcentaje or Decimal("0")
    if precio_incluye_impuesto and impuesto_porcentaje:
        divisor = Decimal("1") + (impuesto_porcentaje / Decimal("100"))
        total_final = (
            subtotal_base - (subtotal_base * (descuento / Decimal("100"))).quantize(DOS_DECIMALES)
        ).quantize(DOS_DECIMALES)
        subtotal_sin_descuento = (subtotal_base / divisor).quantize(DOS_DECIMALES)
        subtotal = (total_final / divisor).quantize(DOS_DECIMALES)
        descuento_monto = (subtotal_sin_descuento - subtotal).quantize(DOS_DECIMALES)
        impuesto_monto = (total_final - subtotal).quantize(DOS_DECIMALES)
    else:
        descuento_monto = (subtotal_base * (descuento / Decimal("100"))).quantize(DOS_DECIMALES)
        subtotal = (subtotal_base - descuento_monto).quantize(DOS_DECIMALES)
        impuesto_monto = (subtotal * (impuesto_porcentaje / Decimal("100"))).quantize(DOS_DECIMALES)
    return {
        "descuento_monto": descuento_monto,
        "subtotal": subtotal,
        "impuesto_monto": impuesto_monto,
        "total": (subtotal + impuesto_monto).quantize(DOS_DECIMALES),
    }


def _acciones_habilitadas(empresa):
    return ConfiguracionOnix.objects.filter(
        empresa=empresa,
        activo=True,
        herramientas_accion_activas=True,
    ).exists()


def _validar_acceso_acciones(empresa, usuario):
    if not empresa.tiene_modulo_activo("facturacion"):
        raise ValidationError("La empresa no tiene activo el modulo de facturacion.")
    if empresa.slug in EMPRESAS_FACTURACION_SOLO_CONTADO:
        raise ValidationError(
            "Esta empresa emite facturas al contado automaticamente; ese flujo se habilitara en una etapa posterior de Onix."
        )
    if not _acciones_habilitadas(empresa):
        raise PermissionDenied("Las acciones de Onix no estan habilitadas para esta empresa.")
    if not usuario.tiene_permiso_erp("puede_crear_facturas", empresa):
        raise PermissionDenied("El usuario no tiene permiso para crear facturas.")


def _serializar_accion(accion):
    datos = {
        "id": str(accion.id),
        "type": accion.tipo,
        "status": accion.estado,
        "expires_at": accion.expira_en.isoformat(),
        "decision_url": reverse(
            "asistente_accion",
            args=[accion.empresa.slug, accion.id],
        ),
        **(accion.vista_previa or {}),
    }
    if accion.resultado:
        datos["result"] = accion.resultado
    if accion.detalle_error:
        datos["error"] = accion.detalle_error
    return datos


def preparar_borrador_factura(*, argumentos, empresa, usuario, conversacion):
    _validar_acceso_acciones(empresa, usuario)

    try:
        cliente_id = int(argumentos.get("cliente_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Selecciona un cliente valido antes de preparar la factura.") from exc
    cliente = Cliente.objects.filter(empresa=empresa, activo=True, pk=cliente_id).first()
    if not cliente:
        raise ValidationError("El cliente no existe, esta inactivo o pertenece a otra empresa.")

    moneda = str(argumentos.get("moneda") or "HNL").upper()
    if moneda not in {"HNL", "USD"}:
        raise ValidationError("La moneda debe ser HNL o USD.")
    tipo_cambio = _decimal(
        argumentos.get("tipo_cambio"),
        "el tipo de cambio",
        minimo="0.0001",
        maximo="999999.9999",
        predeterminado="1",
        decimales=4,
    )
    if moneda == "HNL":
        tipo_cambio = Decimal("1")

    fecha_emision = _fecha(
        argumentos.get("fecha_emision"),
        "la fecha de emision",
        predeterminada=timezone.localdate(),
    )
    fecha_vencimiento = _fecha(
        argumentos.get("fecha_vencimiento"),
        "la fecha de vencimiento",
    )
    if fecha_vencimiento and fecha_vencimiento < fecha_emision:
        raise ValidationError("La fecha de vencimiento no puede ser anterior a la fecha de emision.")

    items = argumentos.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("La factura debe incluir al menos un producto o servicio.")
    if len(items) > MAXIMO_LINEAS_FACTURA:
        raise ValidationError(f"Onix puede preparar hasta {MAXIMO_LINEAS_FACTURA} lineas por factura.")

    precio_incluye_impuesto = empresa.slug in EMPRESAS_PRECIO_FINAL_CON_IMPUESTO
    lineas = []
    subtotal = Decimal("0")
    impuesto_total = Decimal("0")
    total = Decimal("0")
    for indice, item in enumerate(items, start=1):
        try:
            producto_id = int(item.get("producto_id"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValidationError(f"La linea {indice} no tiene un producto valido.") from exc
        producto = Producto.objects.select_related("impuesto_predeterminado").filter(
            empresa=empresa,
            activo=True,
            eliminado=False,
            pk=producto_id,
        ).first()
        if not producto:
            raise ValidationError(f"El producto de la linea {indice} no existe o pertenece a otra empresa.")
        impuesto = producto.impuesto_predeterminado
        if not impuesto or not impuesto.activo:
            raise ValidationError(
                f"{producto.nombre} no tiene un impuesto predeterminado activo. Configuralo antes de facturar."
            )

        cantidad = _decimal(
            item.get("cantidad"),
            f"la cantidad de {producto.nombre}",
            minimo="0.01",
            maximo="99999999.99",
            decimales=2,
        )
        precio_unitario = _decimal(
            item.get("precio_unitario"),
            f"el precio de {producto.nombre}",
            minimo="0",
            maximo="9999999999.99",
            predeterminado=producto.precio,
            decimales=2,
        ).quantize(DOS_DECIMALES)
        descuento = _decimal(
            item.get("descuento_porcentaje"),
            f"el descuento de {producto.nombre}",
            minimo="0",
            maximo="100",
            predeterminado="0",
            decimales=2,
        )
        calculo = _calcular_linea(
            cantidad=cantidad,
            precio_unitario=precio_unitario,
            descuento_porcentaje=descuento,
            impuesto_porcentaje=impuesto.porcentaje,
            precio_incluye_impuesto=precio_incluye_impuesto,
        )
        subtotal += calculo["subtotal"]
        impuesto_total += calculo["impuesto_monto"]
        total += calculo["total"]
        lineas.append(
            {
                "producto_id": producto.id,
                "producto": producto.nombre,
                "codigo": producto.codigo or "",
                "cantidad": str(cantidad),
                "precio_unitario": str(precio_unitario),
                "descuento_porcentaje": str(descuento),
                "impuesto_id": impuesto.id,
                "impuesto": impuesto.nombre,
                "impuesto_porcentaje": str(impuesto.porcentaje),
                "precio_incluye_impuesto": precio_incluye_impuesto,
                "comentario": str(item.get("comentario") or "").strip()[:1000],
                "subtotal": str(calculo["subtotal"]),
                "impuesto_monto": str(calculo["impuesto_monto"]),
                "total": str(calculo["total"]),
            }
        )

    if total > MAXIMO_TOTAL_FACTURA:
        raise ValidationError(
            f"El total supera el maximo admitido por una factura ({MAXIMO_TOTAL_FACTURA})."
        )

    subtotal = subtotal.quantize(DOS_DECIMALES)
    impuesto_total = impuesto_total.quantize(DOS_DECIMALES)
    total = total.quantize(DOS_DECIMALES)
    datos = {
        "cliente_id": cliente.id,
        "moneda": moneda,
        "tipo_cambio": str(tipo_cambio),
        "fecha_emision": fecha_emision.isoformat(),
        "fecha_vencimiento": fecha_vencimiento.isoformat() if fecha_vencimiento else None,
        "lineas": lineas,
        "subtotal": str(subtotal),
        "impuesto": str(impuesto_total),
        "total": str(total),
    }
    vista_previa = {
        "title": "Borrador de factura listo para confirmar",
        "description": "Onix no guardara nada hasta que confirmes esta vista previa.",
        "client": {
            "id": cliente.id,
            "name": cliente.nombre,
            "rtn": cliente.rtn or "",
        },
        "currency": moneda,
        "exchange_rate": str(tipo_cambio),
        "issue_date": fecha_emision.isoformat(),
        "due_date": fecha_vencimiento.isoformat() if fecha_vencimiento else None,
        "items": lineas,
        "subtotal": str(subtotal),
        "tax": str(impuesto_total),
        "total": str(total),
    }
    accion = AccionOnix.objects.create(
        empresa=empresa,
        usuario=usuario,
        conversacion=conversacion,
        tipo=AccionOnix.TIPO_CREAR_BORRADOR_FACTURA,
        datos=datos,
        vista_previa=vista_previa,
        expira_en=timezone.now() + timedelta(minutes=30),
    )
    return {
        "ok": True,
        "requires_confirmation": True,
        "message": "La vista previa esta lista. El usuario debe confirmarla en Onix antes de crear la factura.",
        "action": _serializar_accion(accion),
    }


def _validar_propietario(accion, usuario):
    if not accion.usuario_id or accion.usuario_id != usuario.id:
        raise PermissionDenied("Solo el usuario que preparo la accion puede confirmarla o descartarla.")


def cancelar_accion(*, accion_id, empresa, usuario):
    with transaction.atomic():
        accion = AccionOnix.objects.select_for_update().filter(pk=accion_id, empresa=empresa).first()
        if not accion:
            raise ValidationError("La accion de Onix no existe en esta empresa.")
        _validar_propietario(accion, usuario)
        if accion.estado == AccionOnix.ESTADO_EJECUTADA:
            raise ValidationError("La accion ya fue ejecutada y no puede descartarse.")
        if accion.estado == AccionOnix.ESTADO_CANCELADA:
            return _serializar_accion(accion)
        accion.estado = AccionOnix.ESTADO_CANCELADA
        accion.fecha_confirmacion = timezone.now()
        accion.save(update_fields=["estado", "fecha_confirmacion", "fecha_actualizacion"])
        return _serializar_accion(accion)


def ejecutar_accion(*, accion_id, empresa, usuario):
    with transaction.atomic():
        accion = AccionOnix.objects.select_for_update().filter(pk=accion_id, empresa=empresa).first()
        if not accion:
            raise ValidationError("La accion de Onix no existe en esta empresa.")
        _validar_propietario(accion, usuario)
        if accion.estado == AccionOnix.ESTADO_EJECUTADA:
            return _serializar_accion(accion)
        if accion.estado == AccionOnix.ESTADO_EXPIRADA:
            return _serializar_accion(accion)
        if accion.estado != AccionOnix.ESTADO_PENDIENTE:
            raise ValidationError(f"La accion ya esta {accion.get_estado_display().lower()}.")
        if timezone.now() >= accion.expira_en:
            accion.estado = AccionOnix.ESTADO_EXPIRADA
            accion.detalle_error = "La vista previa vencio. Pidele a Onix que prepare la factura nuevamente."
            accion.save(update_fields=["estado", "detalle_error", "fecha_actualizacion"])
            return _serializar_accion(accion)

        _validar_acceso_acciones(empresa, usuario)
        if accion.tipo != AccionOnix.TIPO_CREAR_BORRADOR_FACTURA:
            raise ValidationError("Onix no reconoce el tipo de accion solicitado.")

        datos = accion.datos or {}
        cliente = Cliente.objects.filter(
            empresa=empresa,
            activo=True,
            pk=datos.get("cliente_id"),
        ).first()
        if not cliente:
            raise ValidationError("El cliente dejo de estar disponible. Prepara la factura nuevamente.")

        factura = Factura.objects.create(
            empresa=empresa,
            cliente=cliente,
            vendedor=usuario,
            moneda=datos["moneda"],
            tipo_cambio=Decimal(datos["tipo_cambio"]),
            fecha_emision=parse_date(datos["fecha_emision"]),
            fecha_vencimiento=parse_date(datos["fecha_vencimiento"]) if datos.get("fecha_vencimiento") else None,
            estado="borrador",
            estado_pago="pendiente",
        )
        for linea_datos in datos.get("lineas", []):
            producto = Producto.objects.filter(
                empresa=empresa,
                activo=True,
                eliminado=False,
                pk=linea_datos["producto_id"],
            ).first()
            impuesto = TipoImpuesto.objects.filter(
                activo=True,
                pk=linea_datos["impuesto_id"],
            ).first()
            if not producto or not impuesto:
                raise ValidationError(
                    "Un producto o impuesto cambio despues de la vista previa. Prepara la factura nuevamente."
                )
            if str(impuesto.porcentaje) != linea_datos["impuesto_porcentaje"]:
                raise ValidationError(
                    f"El impuesto de {producto.nombre} cambio despues de la vista previa. Prepara la factura nuevamente."
                )
            LineaFactura.objects.create(
                factura=factura,
                producto=producto,
                cantidad=Decimal(linea_datos["cantidad"]),
                precio_unitario=Decimal(linea_datos["precio_unitario"]),
                precio_incluye_impuesto=bool(linea_datos["precio_incluye_impuesto"]),
                descuento_porcentaje=Decimal(linea_datos["descuento_porcentaje"]),
                comentario=linea_datos.get("comentario") or "",
                impuesto=impuesto,
            )

        if not factura.lineas.exists():
            raise ValidationError("La vista previa no contiene lineas validas.")
        factura.calcular_totales()
        if (
            factura.subtotal.quantize(DOS_DECIMALES) != Decimal(datos["subtotal"])
            or factura.impuesto.quantize(DOS_DECIMALES) != Decimal(datos["impuesto"])
            or factura.total.quantize(DOS_DECIMALES) != Decimal(datos["total"])
        ):
            raise ValidationError(
                "Los totales cambiaron despues de la vista previa. No se creo la factura; preparala nuevamente."
            )
        factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])

        resultado = {
            "invoice_id": factura.id,
            "number": f"Borrador #{factura.id}",
            "status": factura.estado,
            "total": str(factura.total),
            "currency": factura.moneda,
            "url": reverse(
                "ver_factura",
                kwargs={"empresa_slug": empresa.slug, "factura_id": factura.id},
            ),
        }
        accion.estado = AccionOnix.ESTADO_EJECUTADA
        accion.resultado = resultado
        accion.fecha_confirmacion = timezone.now()
        accion.detalle_error = ""
        accion.save(
            update_fields=[
                "estado",
                "resultado",
                "fecha_confirmacion",
                "detalle_error",
                "fecha_actualizacion",
            ]
        )
        return _serializar_accion(accion)
