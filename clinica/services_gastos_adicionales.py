"""Conversión de documentos clínicos internos al motor de Facturación."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from facturacion.models import Cliente, Factura, LineaFactura
from facturacion.services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos

from .models import GastoAdicional, Paciente


def _cliente_local_para_paciente(paciente):
    """Reutiliza el vínculo local y el adaptador existente de pacientes del POS."""
    if paciente.cliente_id:
        if paciente.cliente.empresa_id != paciente.empresa_id:
            raise ValidationError("El cliente asociado al paciente pertenece a otra empresa.")
        return paciente.cliente

    identidad = (paciente.identidad or "").strip()
    cliente = None
    if identidad:
        cliente = Cliente.objects.filter(empresa=paciente.empresa, rtn__iexact=identidad).first()
    if not cliente:
        por_nombre = Cliente.objects.filter(
            empresa=paciente.empresa, nombre__iexact=paciente.nombre.strip(),
        ).first()
        if por_nombre and identidad and (por_nombre.rtn or "").strip() not in ("", identidad):
            raise ValidationError(
                "Existe un cliente con el mismo nombre y otra identidad. Revise el vínculo del paciente antes de convertir."
            )
        cliente = por_nombre
    if cliente:
        # No se actualiza la ficha comercial para preparar una factura: esas
        # señales comparten perfiles y la conversión debe ser solo local.
        paciente.cliente = cliente
        paciente.save(update_fields=["cliente"])
        return cliente

    from facturacion.views import _cliente_desde_paciente_pos
    with suspender_sincronizacion_clientes_compartidos():
        return _cliente_desde_paciente_pos(paciente)


@transaction.atomic
def convertir_gasto_adicional(gasto, usuario):
    """Devuelve (factura, creada), siempre creando un borrador sin efectos fiscales.

    El bloqueo del documento serializa intentos repetidos. El borrador queda
    ligado al original y se revisa/emite en el flujo normal de Facturación.
    """
    gasto = (
        GastoAdicional.objects.select_for_update(of=("self",))
        .select_related("empresa")
        .get(pk=gasto.pk)
    )
    permisos = (
        "puede_convertir_gastos_adicionales_factura",
        "puede_crear_facturas",
        "puede_editar_facturas",
        "puede_ver_facturas",
    )
    if (
        not usuario or not usuario.is_active
        or not usuario.puede_acceder_empresa(gasto.empresa)
        or not all(usuario.tiene_permiso_erp(permiso, gasto.empresa) for permiso in permisos)
    ):
        raise PermissionDenied("No tiene permiso para convertir este gasto adicional a factura.")
    gasto.full_clean()
    if gasto.factura_id:
        return gasto.factura, False

    lineas = list(gasto.lineas.select_related("producto", "producto__impuesto_predeterminado"))
    if not lineas:
        raise ValidationError("Agregue al menos un producto antes de convertir a factura.")
    for linea in lineas:
        linea.full_clean()
        if not linea.producto.impuesto_predeterminado_id:
            raise ValidationError(
                f"Configure el impuesto del producto o servicio «{linea.descripcion}» antes de convertir."
            )
        if not linea.producto.impuesto_predeterminado.activo:
            raise ValidationError(
                f"El impuesto del producto o servicio «{linea.descripcion}» está inactivo. Revise su configuración."
            )

    # Las relaciones opcionales se leen después del bloqueo, en una consulta
    # nueva: otro intento puede haberlas creado mientras esperábamos la fila.
    paciente = Paciente.objects.select_for_update(of=("self",)).select_related("empresa").get(pk=gasto.paciente_id)
    cliente = _cliente_local_para_paciente(paciente)
    factura = Factura.objects.create(
        empresa=gasto.empresa,
        cliente=cliente,
        vendedor=usuario,
        fecha_emision=gasto.fecha,
        estado="borrador",
        moneda="HNL",
        tipo_cambio=1,
    )
    referencia = f"Origen: Gastos Adicionales {gasto.numero}."
    for indice, linea in enumerate(lineas):
        comentario = referencia
        if indice == 0 and gasto.observacion:
            comentario += f"\nObservación clínica: {gasto.observacion}"
        LineaFactura.objects.create(
            factura=factura,
            producto=linea.producto,
            descripcion_manual=linea.descripcion,
            cantidad=linea.cantidad,
            precio_unitario=linea.precio_unitario,
            precio_incluye_impuesto=gasto.precio_incluye_impuesto,
            impuesto=linea.producto.impuesto_predeterminado,
            comentario=comentario,
        )
    factura.calcular_totales()
    factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
    gasto.factura = factura
    gasto.convertido_por = usuario
    gasto.fecha_conversion = timezone.now()
    gasto.save(update_fields=["factura", "convertido_por", "fecha_conversion"])
    return factura, True
