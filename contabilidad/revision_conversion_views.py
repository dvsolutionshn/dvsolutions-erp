import csv
import json
from datetime import date, datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .historicos_usd import (ENCABEZADOS_CSV, consulta_asientos_historicos, consulta_documentos_sin_asiento,
    datos_fuente, fila_asiento, fila_sin_asiento, iterar_diagnostico, resumen_historicos_usd, valores_csv)
from .models import AsientoContable, CuentaContable, RevisionConversionAsiento
from .revision_conversion_forms import FiltrosHistoricosUSDForm, PartidasRevisionFormSet, RevisionConversionForm
from .views import _empresa_desde_slug, _verificar_permiso_contable


FIRMA_REVISION_PARTIDAS = "contabilidad.revision-conversion-partidas.v1"


def _json_revision(valor):
    if isinstance(valor, (Decimal, date, datetime)):
        return str(valor)
    if isinstance(valor, dict):
        return {str(clave): _json_revision(dato) for clave, dato in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_revision(dato) for dato in valor]
    return valor


def _firma_revision(request, asiento, datos, previa):
    return {"empresa": asiento.empresa_id, "asiento": asiento.pk, "usuario": request.user.pk,
        "fecha": datos["fecha"].isoformat(), "cuenta": datos["cuenta_contrapartida"].pk if datos["cuenta_contrapartida"] else None,
        "motivo": datos["motivo"], "vista_previa": _json_revision(previa)}


@login_required
@require_GET
def historicos_usd(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_reportes_contables")
    form = FiltrosHistoricosUSDForm(request.GET)
    filas, pagina, resumen = [], None, {}
    if form.is_valid():
        filtros = form.cleaned_data
        sin_asiento = filtros.get("estado") == "sin_asiento"
        consulta = consulta_documentos_sin_asiento(empresa, filtros) if sin_asiento else consulta_asientos_historicos(empresa, filtros)
        pagina = Paginator(consulta, 30).get_page(request.GET.get("pagina"))
        filas = [fila_sin_asiento(empresa, registro) if sin_asiento else fila_asiento(empresa, registro) for registro in pagina]
        resumen = resumen_historicos_usd(empresa, filtros)
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    return render(request, "contabilidad/historicos_usd.html", {"empresa": empresa, "form": form,
        "filas": filas, "pagina": pagina, "resumen": resumen, "parametros": parametros.urlencode()})


@login_required
@require_GET
def exportar_historicos_usd_csv(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_reportes_contables")
    form = FiltrosHistoricosUSDForm(request.GET)
    if not form.is_valid():
        return HttpResponse("Corrige los filtros antes de exportar la revisión.", status=400, content_type="text/plain; charset=utf-8")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="Historicos_USD_{empresa.slug}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(ENCABEZADOS_CSV)
    for fila in iterar_diagnostico(empresa, form.cleaned_data):
        writer.writerow(valores_csv(empresa, fila))
    return response


@login_required
@require_http_methods(["GET", "POST"])
def revisar_conversion_asiento(request, empresa_slug, asiento_id):
    from .revision_conversion import registrar_revision_conversion, revision_conversion_vigente, vista_previa_revision

    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    asiento = get_object_or_404(AsientoContable.objects.prefetch_related("lineas__cuenta"),
        empresa=empresa, pk=asiento_id, estado="contabilizado", conversion_verificada=False)
    existente = RevisionConversionAsiento.objects.filter(empresa=empresa, origen=asiento).select_related("ajuste", "creado_por").first()
    lineas = list(asiento.lineas.order_by("pk").select_related("cuenta"))
    fuente = datos_fuente(empresa, asiento.documento_tipo, asiento.documento_id)
    error_fuente = ""
    if not fuente or any(linea.cuenta.empresa_id != empresa.pk for linea in lineas):
        error_fuente = "La fuente o sus cuentas no pertenecen íntegramente a esta empresa. Revisa sus vínculos antes de autorizar la conversión."
    if existente:
        try:
            revision_conversion_vigente(asiento)
        except ValidationError as error:
            error_fuente = "; ".join(error.messages)
    finalizar = request.method == "POST" and request.POST.get("accion") == "registrar"
    datos_post = request.POST if request.method == "POST" else None
    form = RevisionConversionForm(datos_post, empresa=empresa, exigir_confirmacion=finalizar)
    formset = PartidasRevisionFormSet(datos_post, prefix="partidas", lineas=lineas, initial=[{
        "linea_id": linea.pk, "moneda": "HNL", "importe_original": linea.debe + linea.haber, "tipo_cambio": Decimal("1"),
    } for linea in lineas])
    previa, token, confirmar = None, "", False
    if request.method == "POST" and not existente and not error_fuente:
        formulario_valido, partidas_validas = form.is_valid(), formset.is_valid()
        if formulario_valido and partidas_validas:
            try:
                partidas = [partida.cleaned_data for partida in formset]
                previa = vista_previa_revision(asiento, partidas, form.cleaned_data["cuenta_contrapartida"])
                fecha_minima = previa["fecha_minima"]
                if isinstance(fecha_minima, str):
                    fecha_minima = date.fromisoformat(fecha_minima)
                if form.cleaned_data["fecha"] < fecha_minima:
                    raise ValidationError("La fecha de revisión debe ser igual o posterior al origen y a los ajustes que ya lo respaldan.")
                if previa["requiere_contrapartida"] and not form.cleaned_data["cuenta_contrapartida"]:
                    form.add_error("cuenta_contrapartida", "Selecciona una contrapartida autorizada para balancear las diferencias revisadas.")
                    raise ValidationError("Comprueba la contrapartida y vuelve a revisar las partidas antes de confirmar.")
                firma = _firma_revision(request, asiento, form.cleaned_data, previa)
                # Verifica aquí que toda la vista previa puede firmarse de forma estable.
                json.dumps(firma, sort_keys=True)
                if finalizar:
                    try:
                        confirmada = signing.loads(form.cleaned_data["revision_token"], salt=FIRMA_REVISION_PARTIDAS, max_age=1800)
                    except signing.BadSignature:
                        raise ValidationError("Revisa todas las partidas antes de confirmar. La vista previa falta o ha vencido.")
                    if confirmada != firma:
                        raise ValidationError("Los datos o las cuentas cambiaron. Revisa la nueva vista previa antes de confirmar.")
                    revision = registrar_revision_conversion(asiento=asiento, partidas=partidas,
                        fecha=form.cleaned_data["fecha"], cuenta_contrapartida=form.cleaned_data["cuenta_contrapartida"],
                        motivo=form.cleaned_data["motivo"], usuario=request.user, vista_previa_esperada=previa)
                    messages.success(request, "Revisión integral registrada. Las partidas originales y los cierres anteriores conservan su respaldo.")
                    return redirect("revisar_conversion_asiento", empresa_slug=empresa.slug, asiento_id=revision.origen_id)
                confirmar = True
                token = signing.dumps(firma, salt=FIRMA_REVISION_PARTIDAS, compress=True)
            except ValidationError as error:
                form.add_error(None, error)
    por_id = {linea.pk: linea for linea in lineas}
    partidas_contexto = []
    for campo in formset:
        try:
            linea = por_id.get(int(campo["linea_id"].value()))
        except (ValueError, TypeError):
            linea = None
        partidas_contexto.append({"form": campo, "linea": linea})
    partidas_vista = []
    ajustes_vista = []
    if previa:
        for partida in previa["partidas"]:
            partidas_vista.append({**_json_revision(partida), "cuenta": por_id[partida["linea_id"]].cuenta})
        cuentas_ajuste = {cuenta.pk: cuenta for cuenta in CuentaContable.objects.filter(
            empresa=empresa, pk__in=[linea["cuenta_id"] for linea in previa["lineas_ajuste"]])}
        for linea in previa["lineas_ajuste"]:
            ajustes_vista.append({**_json_revision(linea), "cuenta": cuentas_ajuste.get(linea["cuenta_id"])})
    return render(request, "contabilidad/revisar_conversion_asiento.html", {"empresa": empresa,
        "asiento": asiento, "fuente": fuente, "error_fuente": error_fuente, "revision_existente": existente,
        "form": form, "formset": formset, "partidas_contexto": partidas_contexto,
        "vista_previa": _json_revision(previa) if previa else None, "lista_para_confirmar": confirmar,
        "revision_token": token, "partidas_vista": partidas_vista, "ajustes_vista": ajustes_vista})
