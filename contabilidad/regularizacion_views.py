from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from facturacion.models import Factura
from facturacion.costos_inventario import valor_contable_linea_inventario
from .models import AsientoContable, RegularizacionMonedaFactura
from .regularizacion_forms import RegularizacionMonedaFacturaForm
from .views import _empresa_desde_slug, _manejar_validacion_contable, _verificar_permiso_contable


FIRMA_REVISION_MONEDA = "contabilidad.regularizacion-moneda-factura.v1"


def _firma_datos_revision(request, asiento, datos, vista_previa):
    return {
        "empresa": asiento.empresa_id, "asiento": asiento.pk, "usuario": request.user.pk,
        "fecha": datos["fecha"].isoformat(), "tipo_cambio": str(datos["tipo_cambio"]),
        "fecha_minima": str(vista_previa.get("fecha_minima", asiento.fecha)),
        "cuenta": datos["cuenta_contrapartida"].pk, "motivo": datos["motivo"],
        "saldos": {
            nombre: str(vista_previa[nombre]) for nombre in (
                "total_original", "total_hnl", "saldo_original", "saldo_hnl_anterior", "saldo_hnl_corregido", "diferencia",
                "aplicado_original", "aplicado_hnl_anterior", "aplicado_hnl_corregido",
                "notas_original", "notas_hnl_anterior", "notas_hnl_corregido",
                "aplicaciones_pendientes",
            )
        },
    }


@login_required
@require_http_methods(["GET", "POST"])
@_manejar_validacion_contable
def regularizar_moneda_factura(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    asiento = get_object_or_404(
        AsientoContable.objects.prefetch_related("lineas__cuenta"),
        pk=asiento_id, empresa=empresa, documento_tipo="factura", evento="emision",
        estado="contabilizado", conversion_verificada=False,
    )
    factura = get_object_or_404(Factura, pk=asiento.documento_id, empresa=empresa, cliente__empresa=empresa, moneda="USD", estado="emitida")
    revision_existente = RegularizacionMonedaFactura.objects.filter(empresa=empresa, asiento_origen=asiento).select_related("asiento_ajuste").first()
    if revision_existente:
        messages.info(request, "La factura ya tiene una regularización registrada; conserva su revisión original.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=revision_existente.asiento_ajuste_id or asiento.pk)

    from .regularizacion_moneda import regularizar_factura_historica, vista_previa_regularizacion

    previa, error_fuente = None, ""
    try:
        previa = vista_previa_regularizacion(asiento)
    except ValidationError as error:
        error_fuente = "; ".join(error.messages)
    finalizar = request.method == "POST" and request.POST.get("accion") == "registrar"
    form = RegularizacionMonedaFacturaForm(
        request.POST if request.method == "POST" else None, empresa=empresa, cuenta_cliente=previa.get("cuenta_cliente") if previa else None,
        fecha_origen=previa.get("fecha_minima", asiento.fecha) if previa else asiento.fecha,
        exigir_confirmacion=finalizar, initial={"tipo_cambio": factura.tipo_cambio},
    )
    lista_para_confirmar = False
    revision_token = ""
    if request.method == "POST" and form.is_valid():
        try:
            previa = vista_previa_regularizacion(asiento, tipo_cambio=form.cleaned_data["tipo_cambio"])
            datos_firmados = _firma_datos_revision(request, asiento, form.cleaned_data, previa)
            if finalizar:
                try:
                    datos_confirmados = signing.loads(form.cleaned_data["revision_token"], salt=FIRMA_REVISION_MONEDA, max_age=1800)
                except signing.BadSignature:
                    raise ValidationError("Revisa el ajuste antes de confirmarlo. La vista previa falta o ha vencido.")
                if datos_confirmados != datos_firmados:
                    raise ValidationError("Los datos del ajuste cambiaron. Revisa la nueva vista previa antes de confirmarlos.")
                revision = regularizar_factura_historica(
                    asiento=asiento, fecha=form.cleaned_data["fecha"], tipo_cambio=form.cleaned_data["tipo_cambio"],
                    cuenta_contrapartida=form.cleaned_data["cuenta_contrapartida"], motivo=form.cleaned_data["motivo"], usuario=request.user,
                    vista_previa_esperada=previa,
                )
                if revision.asiento_ajuste_id:
                    messages.success(request, "Regularización registrada. El ajuste de la cuenta por cobrar quedó contabilizado en la fecha autorizada.")
                    return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=revision.asiento_ajuste_id)
                messages.success(request, "Revisión registrada sin asiento de ajuste: el saldo por cobrar ya coincidía con la conversión autorizada.")
                return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.pk)
            lista_para_confirmar = True
            revision_token = signing.dumps(datos_firmados, salt=FIRMA_REVISION_MONEDA, compress=True)
        except ValidationError as error:
            form.add_error(None, error)

    costo_inventario = sum((valor_contable_linea_inventario(linea) for linea in factura.lineas.select_related("producto") if linea.producto_id and linea.producto.controla_inventario), Decimal("0.00"))
    return render(request, "contabilidad/regularizar_moneda_factura.html", {
        "empresa": empresa, "asiento": asiento, "factura": factura, "form": form,
        "vista_previa": previa, "costo_inventario": costo_inventario,
        "lista_para_confirmar": lista_para_confirmar, "revision_token": revision_token, "error_fuente": error_fuente,
    })
