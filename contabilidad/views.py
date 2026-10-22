from collections import defaultdict
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from decimal import Decimal
from django.utils import formats, timezone
from django.utils.dateparse import parse_date

from django.http import HttpResponse, HttpResponseBadRequest
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from core.models import Empresa, Usuario
from core.audit_context import audit_scope
from facturacion.models import CompraInventario, Factura, NotaCredito, PagoCompra, PagoFactura, RegistroCompraFiscal

from .forms import (
    AsientoContableForm,
    ClasificacionCompraFiscalForm,
    ClasificacionMovimientoBancoForm,
    ConfiguracionContableEmpresaForm,
    CuentaContableForm,
    CuentaFinancieraForm,
    EnlazarMovimientoFacturaForm,
    EnlazarMovimientoCompraForm,
    ImportarCatalogoCuentasForm,
    ImportarCatalogoCuentasRumiForm,
    ImportarMovimientosBancoForm,
    LineaAsientoFormSet,
    MovimientoBancarioClasificacionForm,
    MovimientoBancarioEdicionForm,
    PeriodoContableForm,
    ReglaClasificacionBancoForm,
)
from .importadores import analizar_catalogo_cuentas_desde_excel, aplicar_catalogo_cuentas, importar_catalogo_cuentas_desde_excel, importar_movimientos_bancarios_desde_excel
from .alcance_catalogo import es_empresa_rumi
from .catalogo_rumi_roles import usa_catalogo_rumi
from .catalogo_consulta import ordenar_catalogo
from .cierre import revisar_cierre_periodo
from .comparacion_reportes import ErrorComparacionPeriodos, datos_comparacion_balance, datos_comparacion_resultados, formulario_comparacion
from .consulta_mayor import bloques_mayor_consulta, resumen_mayor_general
from .dashboard import datos_dashboard_contable
from .listado_asientos import contexto_listado_asientos
from .moneda import convertir_a_lempiras, distribuir_conversion
from .reportes import (
    saldo_natural, saldos_financieros, datos_mayor, datos_balance_comprobacion,
    datos_estado_resultados, datos_balance_general, datos_flujo_efectivo,
    cuentas_efectivo_ids, clasificar_contrapartida,
)
from .models import AsientoContable, ClasificacionCompraFiscal, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, PeriodoContable, ReglaClasificacionBanco
from .services import aplicar_reglas_clasificacion_bancaria, asegurar_clasificaciones_bancarias_base_honduras, cargar_catalogo_base_honduras, registrar_asiento_pago_cliente, registrar_asiento_pago_proveedor


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _verificar_permiso_contable(request, empresa, permiso):
    usuario = request.user
    if not (
        usuario.is_authenticated and usuario.is_active
        and usuario.puede_acceder_empresa(empresa)
        and usuario.tiene_permiso_erp("puede_contabilidad", empresa)
        and usuario.tiene_permiso_erp(permiso, empresa)
    ):
        raise PermissionDenied("Tu usuario no tiene permiso para realizar esta operación contable en esta empresa.")


def _verificar_contabilizacion_solicitada(request, empresa):
    if request.method == "POST" and (
        request.POST.get("estado") == "contabilizado"
        or request.POST.get("contabilizar_ahora", "").lower() not in {"", "false", "0"}
    ):
        _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")


def _manejar_validacion_contable(vista):
    @wraps(vista)
    def protegida(request, empresa_slug, *args, **kwargs):
        try:
            return vista(request, empresa_slug, *args, **kwargs)
        except ValidationError as error:
            messages.error(request, "No se pudo completar la operación: " + "; ".join(error.messages))
            destino = "asientos_contables" if "asiento" in vista.__name__ else "periodos_contables" if "periodo" in vista.__name__ else "movimientos_bancarios"
            return redirect(destino, empresa_slug=empresa_slug)
    return protegida


def _retorno_bancario(request, empresa, vista="movimientos_bancarios"):
    destino = request.POST.get("next", "")
    prefijo = f"/{empresa.slug}/dashboard/contabilidad/"
    if destino.startswith(prefijo) and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return destino
    return redirect(vista, empresa_slug=empresa.slug).url


def _resumen_contabilidad(empresa):
    cuentas = CuentaContable.objects.filter(empresa=empresa)
    asientos = AsientoContable.objects.filter(empresa=empresa)
    return {
        "cuentas": cuentas.count(),
        "cuentas_activas": cuentas.filter(activa=True).count(),
        "asientos": asientos.count(),
        "contabilizados": asientos.filter(estado="contabilizado").count(),
        "borradores": asientos.filter(estado="borrador").count(),
    }


def _pendientes_cierre_periodo(empresa, periodo):
    return revisar_cierre_periodo(empresa, periodo)["control"]


def _detalle_cierre_periodo(empresa, periodo):
    return revisar_cierre_periodo(empresa, periodo)


def _preparar_excel(titulo):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = titulo[:31]
    sheet.freeze_panes = "A3"
    sheet.append([titulo])
    sheet["A1"].font = Font(bold=True, size=14, color="0F2742")
    sheet.append([])
    return workbook, sheet


def _aplicar_encabezado(sheet, fila):
    fill = PatternFill("solid", fgColor="0F2742")
    for cell in sheet[fila]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")


def _respuesta_excel(workbook, nombre_archivo):
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    workbook.save(response)
    return response


def _autoajustar_columnas(sheet):
    for column in sheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        sheet.column_dimensions[column_letter].width = min(max_length + 3, 48)


def _saldo_natural(cuenta, debe, haber):
    return saldo_natural(cuenta, debe, haber)


def _saldo_financiero_por_cuenta(empresa, tipos, fecha_inicio="", fecha_fin="", fecha_corte=""):
    return saldos_financieros(empresa, tipos, fecha_inicio, fecha_fin, fecha_corte)


def _cuentas_efectivo_ids(empresa):
    return cuentas_efectivo_ids(empresa)


def _clasificar_flujo_efectivo(linea_efectivo, lineas_asiento):
    categorias = {clasificar_contrapartida(linea.cuenta) for linea in lineas_asiento if linea.cuenta_id != linea_efectivo.cuenta_id}
    return next(iter(categorias)) if len(categorias) == 1 else "operacion"


def _calcular_flujo_efectivo(empresa, fecha_inicio="", fecha_fin=""):
    datos = datos_flujo_efectivo(empresa, fecha_inicio, fecha_fin)
    return datos["flujo"], datos["resumen"]["total_neto"], datos["cuentas_efectivo"]


@login_required
def contabilidad_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    context = {
        "empresa": empresa,
        **datos_dashboard_contable(empresa),
    }
    return render(request, "contabilidad/dashboard_contabilidad.html", context)


@login_required
def configuracion_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if usa_catalogo_rumi(empresa):
        from .catalogo_rumi_views import configuracion_rumi
        return configuracion_rumi(request, empresa)
    configuracion, _ = ConfiguracionContableEmpresa.objects.get_or_create(empresa=empresa)
    form = ConfiguracionContableEmpresaForm(
        request.POST or None,
        instance=configuracion,
        empresa=empresa,
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Configuracion contable actualizada correctamente.")
            return redirect("contabilidad_dashboard", empresa_slug=empresa.slug)
    return render(request, "contabilidad/configuracion_contable.html", {
        "empresa": empresa,
        "form": form,
        "configuracion": configuracion,
        "cuentas_disponibles": CuentaContable.objects.filter(
            empresa=empresa,
            activa=True,
            acepta_movimientos=True,
        ).count(),
    })


@login_required
def periodos_contables(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    periodos = list(PeriodoContable.objects.filter(empresa=empresa).select_related("cerrado_por"))
    asientos = AsientoContable.objects.filter(empresa=empresa)
    for periodo in periodos:
        periodo.control_cierre = _pendientes_cierre_periodo(empresa, periodo)
    resumen = {
        "total": len(periodos),
        "abiertos": sum(1 for periodo in periodos if periodo.estado == "abierto"),
        "cerrados": sum(1 for periodo in periodos if periodo.estado == "cerrado"),
        "asientos": asientos.count(),
    }
    return render(request, "contabilidad/periodos_contables.html", {
        "empresa": empresa,
        "periodos": periodos,
        "resumen": resumen,
    })


@login_required
@_manejar_validacion_contable
def crear_periodo_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    form = PeriodoContableForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        periodo = form.save(commit=False)
        periodo.empresa = empresa
        periodo.estado = "abierto"
        periodo.save()
        messages.success(request, f"Periodo {periodo.mes}/{periodo.anio} guardado correctamente.")
        return redirect("periodos_contables", empresa_slug=empresa.slug)
    return render(request, "contabilidad/periodo_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nuevo Periodo Contable",
    })


@login_required
@_manejar_validacion_contable
def editar_periodo_contable(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    periodo = get_object_or_404(PeriodoContable, id=periodo_id, empresa=empresa)
    form = PeriodoContableForm(request.POST or None, instance=periodo, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        periodo = form.save(commit=False)
        periodo.empresa = empresa
        periodo.save()
        messages.success(request, f"Periodo {periodo.mes}/{periodo.anio} actualizado correctamente.")
        return redirect("periodos_contables", empresa_slug=empresa.slug)
    return render(request, "contabilidad/periodo_form.html", {
        "empresa": empresa,
        "form": form,
        "periodo": periodo,
        "titulo": f"Editar Periodo {periodo.mes}/{periodo.anio}",
    })


@login_required
@require_POST
@_manejar_validacion_contable
def cerrar_periodo_contable(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        periodo = get_object_or_404(PeriodoContable.objects.select_for_update(), id=periodo_id, empresa=empresa)
        if periodo.esta_cerrado:
            messages.info(request, "Este período ya está cerrado; se conserva su cierre original.")
            return redirect("periodos_contables", empresa_slug=empresa.slug)
        pendientes = _pendientes_cierre_periodo(empresa, periodo)
        if pendientes["total"]:
            messages.error(request, (
                f"No se puede cerrar {periodo.mes}/{periodo.anio}: "
                f"hay {pendientes['asientos_borrador']} asientos borrador, "
                f"{pendientes['movimientos_bancarios']} movimientos bancarios sin contabilizar, "
                f"{pendientes['movimientos_sin_conciliar']} movimientos sin conciliar, "
                f"{pendientes['asientos_invalidos']} asientos inválidos, "
                f"{pendientes['movimientos_sin_asiento']} movimientos sin asiento válido y "
                f"{pendientes['documentos_sin_asiento']} documentos sin asiento y "
                f"{pendientes['conversiones_pendientes']} conversiones históricas pendientes de revisar"
                f"{' y el balance del período no cuadra.' if not pendientes['balance_cuadrado'] else '.'}"
            ))
            return redirect("checklist_cierre_periodo", empresa_slug=empresa.slug, periodo_id=periodo.pk)
        with audit_scope(user=request.user, reason=f"Cierre del período {periodo.mes:02d}/{periodo.anio}"):
            periodo.cambiar_estado("cerrado", cerrado_por=request.user, fecha_cierre=timezone.now())
    messages.success(request, f"Período {periodo.mes}/{periodo.anio} cerrado correctamente.")
    return redirect("periodos_contables", empresa_slug=empresa.slug)


@login_required
def checklist_cierre_periodo(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    periodo = get_object_or_404(PeriodoContable, id=periodo_id, empresa=empresa)
    detalle = _detalle_cierre_periodo(empresa, periodo)
    return render(request, "contabilidad/checklist_cierre_periodo.html", {
        "empresa": empresa,
        "periodo": periodo,
        **detalle,
    })


@login_required
@require_POST
@_manejar_validacion_contable
def abrir_periodo_contable(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        periodo = get_object_or_404(PeriodoContable.objects.select_for_update(), id=periodo_id, empresa=empresa)
        if not periodo.esta_cerrado:
            messages.info(request, "Este período ya está abierto.")
            return redirect("periodos_contables", empresa_slug=empresa.slug)
        motivo = request.POST.get("motivo_reapertura", "").strip()
        if not motivo or len(motivo) > 400:
            messages.error(request, "Indica el motivo de la reapertura, con un máximo de 400 caracteres.")
            return redirect("periodos_contables", empresa_slug=empresa.slug)
        with audit_scope(user=request.user, reason=f"Reapertura del período {periodo.mes:02d}/{periodo.anio}: {motivo}"):
            periodo.cambiar_estado("abierto")
    messages.success(request, f"Período {periodo.mes}/{periodo.anio} reabierto. El motivo quedó registrado en el historial.")
    return redirect("periodos_contables", empresa_slug=empresa.slug)


@login_required
def clasificaciones_compras_fiscales(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    clasificaciones = ClasificacionCompraFiscal.objects.filter(empresa=empresa).select_related("cuenta_contable")
    return render(request, "contabilidad/clasificaciones_compras_fiscales.html", {
        "empresa": empresa,
        "clasificaciones": clasificaciones,
        "resumen": {
            "total": clasificaciones.count(),
            "activas": clasificaciones.filter(activa=True).count(),
            "inactivas": clasificaciones.filter(activa=False).count(),
        },
    })


@login_required
def crear_clasificacion_compra_fiscal(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = ClasificacionCompraFiscalForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        clasificacion = form.save(commit=False)
        clasificacion.empresa = empresa
        clasificacion.full_clean()
        clasificacion.save()
        messages.success(request, f"Clasificacion {clasificacion.nombre} creada correctamente.")
        return redirect("clasificaciones_compras_fiscales", empresa_slug=empresa.slug)
    return render(request, "contabilidad/clasificacion_compra_fiscal_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva Clasificacion de Compra",
    })


@login_required
def editar_clasificacion_compra_fiscal(request, empresa_slug, clasificacion_id):
    empresa = _empresa_desde_slug(empresa_slug)
    clasificacion = get_object_or_404(ClasificacionCompraFiscal, id=clasificacion_id, empresa=empresa)
    form = ClasificacionCompraFiscalForm(request.POST or None, instance=clasificacion, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        clasificacion = form.save(commit=False)
        clasificacion.empresa = empresa
        clasificacion.full_clean()
        clasificacion.save()
        messages.success(request, f"Clasificacion {clasificacion.nombre} actualizada correctamente.")
        return redirect("clasificaciones_compras_fiscales", empresa_slug=empresa.slug)
    return render(request, "contabilidad/clasificacion_compra_fiscal_form.html", {
        "empresa": empresa,
        "form": form,
        "clasificacion": clasificacion,
        "titulo": f"Editar Clasificacion: {clasificacion.nombre}",
    })


@login_required
def bancos_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    cuentas = CuentaFinanciera.objects.filter(empresa=empresa).select_related("cuenta_contable")
    movimientos = MovimientoBancario.objects.filter(empresa=empresa).select_related("cuenta_financiera", "clasificacion")
    import_form = ImportarMovimientosBancoForm(empresa=empresa)
    return render(request, "contabilidad/bancos_dashboard.html", {
        "empresa": empresa,
        "cuentas": cuentas,
        "movimientos": movimientos.order_by("-fecha", "-id")[:50],
        "import_form": import_form,
        "resumen": {
            "cuentas": cuentas.count(),
            "movimientos": movimientos.count(),
            "pendientes": movimientos.filter(estado="pendiente").count(),
            "clasificados": movimientos.filter(estado="clasificado").count(),
            "contabilizados": movimientos.filter(estado="contabilizado").count(),
        },
    })


@login_required
def crear_cuenta_financiera(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CuentaFinancieraForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cuenta = form.save(commit=False)
        cuenta.empresa = empresa
        cuenta.full_clean()
        cuenta.save()
        messages.success(request, f"Cuenta financiera {cuenta.nombre} creada correctamente.")
        return redirect("bancos_dashboard", empresa_slug=empresa.slug)
    return render(request, "contabilidad/cuenta_financiera_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva Cuenta Financiera",
    })


@login_required
def importar_movimientos_bancarios(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = ImportarMovimientosBancoForm(request.POST or None, request.FILES or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = importar_movimientos_bancarios_desde_excel(
                empresa,
                form.cleaned_data["cuenta_financiera"],
                form.cleaned_data["archivo"],
            )
        except ValidationError as error:
            for mensaje in error.messages if hasattr(error, "messages") else [str(error)]:
                messages.error(request, mensaje)
        else:
            reglas = aplicar_reglas_clasificacion_bancaria(
                empresa,
                movimiento_ids=resultado.get("movimiento_ids", []),
            )
            messages.success(
                request,
                f"Estado importado: {resultado['creados']} movimientos creados, {resultado['duplicados']} duplicados omitidos.",
            )
            if reglas["actualizados"]:
                messages.info(request, f"{reglas['actualizados']} movimientos fueron clasificados automaticamente por reglas bancarias.")
            if resultado["omitidos"]:
                messages.warning(request, f"Se omitieron {len(resultado['omitidos'])} filas incompletas.")
    else:
        for campo, errores in form.errors.items():
            etiqueta = form.fields[campo].label if campo in form.fields else "Formulario"
            for error in errores:
                messages.error(request, f"{etiqueta}: {error}")
        if not form.errors:
            messages.error(request, "Revisa la cuenta financiera y el archivo antes de importar.")
    return redirect("bancos_dashboard", empresa_slug=empresa.slug)


@login_required
def clasificaciones_bancarias(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    clasificaciones = ClasificacionMovimientoBanco.objects.filter(empresa=empresa).select_related("cuenta_contable")
    return render(request, "contabilidad/clasificaciones_bancarias.html", {
        "empresa": empresa,
        "clasificaciones": clasificaciones,
        "resumen": {
            "total": clasificaciones.count(),
            "activas": clasificaciones.filter(activa=True).count(),
        },
    })


@login_required
def crear_clasificacion_bancaria(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = ClasificacionMovimientoBancoForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        clasificacion = form.save(commit=False)
        clasificacion.empresa = empresa
        clasificacion.full_clean()
        clasificacion.save()
        messages.success(request, f"Clasificacion bancaria {clasificacion.nombre} creada correctamente.")
        return redirect("clasificaciones_bancarias", empresa_slug=empresa.slug)
    return render(request, "contabilidad/clasificacion_bancaria_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva Clasificacion Bancaria",
    })


@login_required
def reglas_bancarias(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    reglas = ReglaClasificacionBanco.objects.filter(empresa=empresa).select_related("clasificacion", "clasificacion__cuenta_contable")
    return render(request, "contabilidad/reglas_bancarias.html", {
        "empresa": empresa,
        "reglas": reglas,
        "resumen": {
            "total": reglas.count(),
            "activas": reglas.filter(activa=True).count(),
        },
    })


@login_required
def crear_regla_bancaria(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    asegurar_clasificaciones_bancarias_base_honduras(empresa)
    form = ReglaClasificacionBancoForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        regla = form.save(commit=False)
        regla.empresa = empresa
        regla.full_clean()
        regla.save()
        messages.success(request, f"Regla bancaria {regla.nombre} creada correctamente.")
        return redirect("reglas_bancarias", empresa_slug=empresa.slug)
    return render(request, "contabilidad/regla_bancaria_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva Regla Bancaria",
    })


@login_required
def editar_regla_bancaria(request, empresa_slug, regla_id):
    empresa = _empresa_desde_slug(empresa_slug)
    regla = get_object_or_404(ReglaClasificacionBanco, id=regla_id, empresa=empresa)
    form = ReglaClasificacionBancoForm(request.POST or None, instance=regla, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        regla = form.save(commit=False)
        regla.empresa = empresa
        regla.full_clean()
        regla.save()
        messages.success(request, f"Regla bancaria {regla.nombre} actualizada correctamente.")
        return redirect("reglas_bancarias", empresa_slug=empresa.slug)
    return render(request, "contabilidad/regla_bancaria_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Editar Regla: {regla.nombre}",
    })


@login_required
@require_POST
@_manejar_validacion_contable
def aplicar_reglas_bancarias_view(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        resultado = aplicar_reglas_clasificacion_bancaria(empresa)
    if resultado["actualizados"]:
        messages.success(request, f"{resultado['actualizados']} movimientos clasificados automaticamente.")
    else:
        messages.info(request, "No se encontraron movimientos pendientes que coincidan con las reglas activas.")
    return redirect(_retorno_bancario(request, empresa))


@login_required
def movimientos_bancarios(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    asegurar_clasificaciones_bancarias_base_honduras(empresa)
    cuenta_id = request.GET.get("cuenta") or ""
    estado = request.GET.get("estado") or ""
    tipo = request.GET.get("tipo") or ""
    q = request.GET.get("q") or ""
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()
    movimientos = MovimientoBancario.objects.filter(empresa=empresa).select_related("cuenta_financiera", "clasificacion", "asiento").order_by("-fecha", "-id")
    for valor, comparador in ((desde, "fecha__gte"), (hasta, "fecha__lte")):
        if valor:
            try:
                fecha = forms.DateField().clean(valor)
            except ValidationError:
                messages.error(request, "Indica fechas válidas para filtrar los movimientos bancarios.")
                movimientos = movimientos.none()
            else:
                movimientos = movimientos.filter(**{comparador: fecha})
    if cuenta_id:
        movimientos = movimientos.filter(cuenta_financiera_id=cuenta_id)
    if estado:
        movimientos = movimientos.filter(estado=estado)
    if tipo == "ingreso":
        movimientos = movimientos.filter(credito__gt=0)
    elif tipo == "egreso":
        movimientos = movimientos.filter(debito__gt=0)
    if q:
        movimientos = movimientos.filter(
            Q(descripcion__icontains=q) |
            Q(referencia__icontains=q) |
            Q(cuenta_financiera__nombre__icontains=q)
        )
    clasificaciones = ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).order_by("nombre")
    resumen = {
        "total": movimientos.count(),
        "pendientes": movimientos.filter(estado="pendiente").count(),
        "clasificados": movimientos.filter(estado="clasificado").count(),
        "contabilizados": movimientos.filter(estado="contabilizado").count(),
        "debitos": movimientos.aggregate(total=Sum("debito"))["total"] or Decimal("0.00"),
        "creditos": movimientos.aggregate(total=Sum("credito"))["total"] or Decimal("0.00"),
    }
    return render(request, "contabilidad/movimientos_bancarios.html", {
        "empresa": empresa,
        "movimientos": movimientos,
        "cuentas": CuentaFinanciera.objects.filter(empresa=empresa).order_by("nombre"),
        "clasificaciones": clasificaciones,
        "resumen": resumen,
        "filtros": {"cuenta": cuenta_id, "estado": estado, "tipo": tipo, "q": q, "desde": desde, "hasta": hasta},
    })


@login_required
@_manejar_validacion_contable
def clasificar_movimientos_bancarios_lote(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    redirect_url = _retorno_bancario(request, empresa)
    if request.method != "POST":
        return redirect(redirect_url)

    ids = request.POST.getlist("movimiento_ids")
    movimientos = MovimientoBancario.objects.filter(empresa=empresa, id__in=ids).exclude(estado="contabilizado")
    clasificaciones_validas = set(
        ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).values_list("id", flat=True)
    )
    actualizados = 0

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        for movimiento in movimientos:
            valor = request.POST.get(f"clasificacion_{movimiento.id}") or ""
            if valor:
                try:
                    clasificacion_id = int(valor)
                except ValueError:
                    continue
                if clasificacion_id not in clasificaciones_validas:
                    continue
                if movimiento.clasificacion_id != clasificacion_id or movimiento.estado != "clasificado":
                    movimiento.clasificacion_id = clasificacion_id
                    movimiento.estado = "clasificado"
                    movimiento.save(update_fields=["clasificacion", "estado"])
                    actualizados += 1
            elif movimiento.clasificacion_id:
                movimiento.clasificacion = None
                movimiento.estado = "pendiente"
                movimiento.save(update_fields=["clasificacion", "estado"])
                actualizados += 1

    if actualizados:
        messages.success(request, f"{actualizados} movimientos actualizados correctamente.")
    else:
        messages.info(request, "No hubo cambios en las clasificaciones.")
    return redirect(redirect_url)


@login_required
@_manejar_validacion_contable
def clasificar_movimiento_bancario(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    movimiento = get_object_or_404(MovimientoBancario, id=movimiento_id, empresa=empresa)
    form = MovimientoBancarioClasificacionForm(request.POST or None, instance=movimiento, empresa=empresa)
    redirect_url = _retorno_bancario(request, empresa)
    if request.method == "POST" and form.is_valid():
        movimiento = form.save(commit=False)
        movimiento.estado = "clasificado" if movimiento.clasificacion_id else "pendiente"
        movimiento.save(update_fields=["clasificacion", "estado"])
        messages.success(request, "Movimiento clasificado correctamente.")
    else:
        messages.error(request, "Selecciona una clasificacion valida.")
    return redirect(redirect_url)


@login_required
def editar_movimiento_bancario(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    movimiento = get_object_or_404(
        MovimientoBancario.objects.select_related("cuenta_financiera", "clasificacion", "asiento"),
        id=movimiento_id,
        empresa=empresa,
    )
    if movimiento.estado == "contabilizado" or movimiento.asiento_id:
        messages.error(request, "Este movimiento ya fue contabilizado y no se puede editar. Reversa el asiento si necesitas corregirlo.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    form = MovimientoBancarioEdicionForm(request.POST or None, instance=movimiento, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        movimiento = form.save(commit=False)
        movimiento.estado = "clasificado" if movimiento.clasificacion_id else "pendiente"
        try:
            movimiento.full_clean()
            movimiento.save()
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for campo, errores in exc.message_dict.items():
                    for error in errores:
                        form.add_error(campo if campo != "__all__" else None, error)
            else:
                form.add_error(None, exc.messages[0] if exc.messages else str(exc))
        else:
            messages.success(request, "Movimiento bancario actualizado correctamente.")
            return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    return render(request, "contabilidad/movimiento_bancario_form.html", {
        "empresa": empresa,
        "movimiento": movimiento,
        "form": form,
    })


@login_required
@_manejar_validacion_contable
def enlazar_movimiento_factura(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    movimiento = get_object_or_404(
        MovimientoBancario.objects.select_related("cuenta_financiera", "pago_factura", "asiento"),
        id=movimiento_id,
        empresa=empresa,
    )
    if not movimiento.es_ingreso:
        messages.error(request, "Solo los creditos bancarios se pueden enlazar como pago de factura.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)
    if movimiento.estado == "contabilizado" or movimiento.asiento_id or movimiento.pago_factura_id:
        messages.error(request, "Este movimiento ya fue contabilizado o enlazado a un pago.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    form = EnlazarMovimientoFacturaForm(request.POST or None, empresa=empresa, movimiento=movimiento)
    if request.method == "POST" and form.is_valid():
        factura = form.cleaned_data["factura"]
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk, empresa=empresa)
            factura = Factura.objects.select_for_update().get(pk=factura.pk, empresa=empresa)
            if movimiento.estado == "contabilizado" or movimiento.asiento_id or movimiento.pago_factura_id:
                messages.error(request, "El movimiento ya fue enlazado. Vuelve a revisarlo.")
                return redirect("movimientos_bancarios", empresa_slug=empresa.slug)
            importe_original, tasa_cobro = form.datos_aplicacion(factura, movimiento)
            pago = PagoFactura.objects.create(
                factura=factura,
                fecha=movimiento.fecha,
                monto=importe_original,
                tipo_cambio=tasa_cobro,
                metodo="transferencia",
                referencia=movimiento.referencia or movimiento.descripcion[:100],
                cuenta_financiera=movimiento.cuenta_financiera,
            )
            asiento = registrar_asiento_pago_cliente(pago)
            movimiento.pago_factura = pago
            movimiento.asiento = asiento
            movimiento.estado = "contabilizado" if asiento else "clasificado"
            movimiento.save(update_fields=["pago_factura", "asiento", "estado"])
        messages.success(request, f"Deposito enlazado a factura {factura.numero_factura or factura.id} y pago registrado correctamente.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    return render(request, "contabilidad/enlazar_movimiento_factura.html", {
        "empresa": empresa,
        "movimiento": movimiento,
        "form": form,
    })


@login_required
@_manejar_validacion_contable
def enlazar_movimiento_compra(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    movimiento = get_object_or_404(
        MovimientoBancario.objects.select_related("cuenta_financiera", "pago_compra", "asiento"),
        id=movimiento_id,
        empresa=empresa,
    )
    if movimiento.es_ingreso:
        messages.error(request, "Solo los debitos bancarios se pueden enlazar como pago de compra.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)
    if movimiento.estado == "contabilizado" or movimiento.asiento_id or movimiento.pago_compra_id:
        messages.error(request, "Este movimiento ya fue contabilizado o enlazado a un pago.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    form = EnlazarMovimientoCompraForm(request.POST or None, empresa=empresa, movimiento=movimiento)
    if request.method == "POST" and form.is_valid():
        compra = form.cleaned_data["compra"]
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk, empresa=empresa)
            compra = CompraInventario.objects.select_for_update().get(pk=compra.pk, empresa=empresa)
            if movimiento.estado == "contabilizado" or movimiento.asiento_id or movimiento.pago_compra_id:
                messages.error(request, "El movimiento ya fue enlazado. Vuelve a revisarlo.")
                return redirect("movimientos_bancarios", empresa_slug=empresa.slug)
            if compra.saldo_pendiente < movimiento.debito:
                messages.error(request, "El saldo de la compra cambió. Revisa el importe antes de enlazar el débito.")
                return redirect("movimientos_bancarios", empresa_slug=empresa.slug)
            pago = PagoCompra.objects.create(
                compra=compra,
                fecha=movimiento.fecha,
                monto=movimiento.debito,
                metodo="transferencia",
                referencia=movimiento.referencia or movimiento.descripcion[:100],
                cuenta_financiera=movimiento.cuenta_financiera,
                observacion=f"Generado desde movimiento bancario {movimiento.id}",
            )
            asiento = registrar_asiento_pago_proveedor(pago)
            movimiento.pago_compra = pago
            movimiento.asiento = asiento
            movimiento.estado = "contabilizado" if asiento else "clasificado"
            movimiento.save(update_fields=["pago_compra", "asiento", "estado"])
        messages.success(request, f"Debito bancario enlazado a compra {compra.numero_compra or compra.id} y pago registrado correctamente.")
        return redirect("movimientos_bancarios", empresa_slug=empresa.slug)

    return render(request, "contabilidad/enlazar_movimiento_compra.html", {
        "empresa": empresa,
        "movimiento": movimiento,
        "form": form,
    })


@login_required
@require_POST
@_manejar_validacion_contable
def contabilizar_movimiento_bancario(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    redirect_url = _retorno_bancario(request, empresa)
    movimiento = get_object_or_404(
        MovimientoBancario.objects.select_related("cuenta_financiera", "clasificacion__cuenta_contable"),
        id=movimiento_id,
        empresa=empresa,
    )
    if movimiento.estado == "contabilizado":
        messages.warning(request, "Este movimiento ya fue contabilizado.")
        return redirect(redirect_url)
    if not movimiento.clasificacion_id:
        messages.error(request, "Clasifica el movimiento antes de contabilizarlo.")
        return redirect(redirect_url)
    if PeriodoContable.fecha_bloqueada(empresa, movimiento.fecha):
        messages.error(request, "No se puede contabilizar porque el periodo contable esta cerrado.")
        return redirect(redirect_url)

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk, empresa=empresa)
        if movimiento.estado == "contabilizado" or movimiento.asiento_id:
            messages.warning(request, "El movimiento ya tiene un asiento; vuelve a revisarlo.")
            return redirect(redirect_url)
        asiento = AsientoContable.objects.create(
            empresa=empresa,
            fecha=movimiento.fecha,
            descripcion=f"Movimiento bancario: {movimiento.descripcion}",
            referencia=movimiento.referencia or str(movimiento.id),
            origen_modulo="bancos",
            documento_tipo="movimiento_bancario",
            documento_id=movimiento.id,
            evento="contabilizacion",
            creado_por=request.user,
            estado="borrador",
        )
        if movimiento.es_ingreso:
            lineas = [
                (movimiento.cuenta_financiera.cuenta_contable, "Ingreso en banco", movimiento.credito, Decimal("0.00")),
                (movimiento.clasificacion.cuenta_contable, movimiento.clasificacion.nombre, Decimal("0.00"), movimiento.credito),
            ]
        else:
            lineas = [
                (movimiento.clasificacion.cuenta_contable, movimiento.clasificacion.nombre, movimiento.debito, Decimal("0.00")),
                (movimiento.cuenta_financiera.cuenta_contable, "Salida de banco/tarjeta", Decimal("0.00"), movimiento.debito),
            ]
        for cuenta, detalle, debe, haber in lineas:
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=cuenta, detalle=detalle, debe=debe, haber=haber)
        asiento.generar_numero()
        asiento.estado = "contabilizado"
        asiento.save(update_fields=["numero", "estado"])
        movimiento.asiento = asiento
        movimiento.estado = "contabilizado"
        movimiento.save(update_fields=["asiento", "estado"])
    messages.success(request, f"Movimiento contabilizado en asiento {asiento.numero}.")
    return redirect(redirect_url)


@login_required
def conciliacion_bancaria(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    cuenta_id = request.GET.get("cuenta") or ""
    estado = request.GET.get("estado") or ""
    conciliado = request.GET.get("conciliado") or ""
    fecha_desde = parse_date(request.GET.get("desde") or "")
    fecha_hasta = parse_date(request.GET.get("hasta") or "")

    movimientos = MovimientoBancario.objects.filter(empresa=empresa).select_related(
        "cuenta_financiera",
        "clasificacion",
        "asiento",
        "conciliado_por",
    ).order_by("-fecha", "-id")

    if cuenta_id:
        movimientos = movimientos.filter(cuenta_financiera_id=cuenta_id)
    if estado:
        movimientos = movimientos.filter(estado=estado)
    if conciliado == "si":
        movimientos = movimientos.filter(conciliado=True)
    elif conciliado == "no":
        movimientos = movimientos.filter(conciliado=False)
    if fecha_desde:
        movimientos = movimientos.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        movimientos = movimientos.filter(fecha__lte=fecha_hasta)

    resumen_base = MovimientoBancario.objects.filter(empresa=empresa)
    resumen_filtrado = movimientos.aggregate(
        debitos=Sum("debito"),
        creditos=Sum("credito"),
    )
    resumen_cuentas = []
    for cuenta in CuentaFinanciera.objects.filter(empresa=empresa).select_related("cuenta_contable").order_by("nombre"):
        movimientos_cuenta = MovimientoBancario.objects.filter(empresa=empresa, cuenta_financiera=cuenta)
        ult_movimiento = movimientos_cuenta.order_by("-fecha", "-id").first()
        totales_cuenta = movimientos_cuenta.aggregate(debitos=Sum("debito"), creditos=Sum("credito"))
        lineas_cuenta = LineaAsientoContable.objects.filter(
            asiento__empresa=empresa,
            asiento__estado="contabilizado",
            cuenta=cuenta.cuenta_contable,
        ).aggregate(debe=Sum("debe"), haber=Sum("haber"))
        saldo_contable = (lineas_cuenta["debe"] or Decimal("0.00")) - (lineas_cuenta["haber"] or Decimal("0.00"))
        saldo_banco = ult_movimiento.saldo if ult_movimiento else Decimal("0.00")
        resumen_cuentas.append({
            "cuenta": cuenta,
            "movimientos": movimientos_cuenta.count(),
            "debitos": totales_cuenta["debitos"] or Decimal("0.00"),
            "creditos": totales_cuenta["creditos"] or Decimal("0.00"),
            "saldo_banco": saldo_banco,
            "saldo_contable": saldo_contable,
            "diferencia": saldo_banco - saldo_contable,
            "sin_conciliar": movimientos_cuenta.filter(estado="contabilizado", conciliado=False).count(),
            "pendientes": movimientos_cuenta.exclude(estado="contabilizado").count(),
        })

    return render(request, "contabilidad/conciliacion_bancaria.html", {
        "empresa": empresa,
        "movimientos": movimientos,
        "cuentas": CuentaFinanciera.objects.filter(empresa=empresa).order_by("nombre"),
        "clasificaciones": ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).order_by("nombre"),
        "resumen_cuentas": resumen_cuentas,
        "filtros": {
            "cuenta": cuenta_id,
            "estado": estado,
            "conciliado": conciliado,
            "desde": request.GET.get("desde") or "",
            "hasta": request.GET.get("hasta") or "",
        },
        "resumen": {
            "pendientes": resumen_base.filter(estado="pendiente").count(),
            "clasificados": resumen_base.filter(estado="clasificado").count(),
            "contabilizados": resumen_base.filter(estado="contabilizado").count(),
            "conciliados": resumen_base.filter(conciliado=True).count(),
            "sin_conciliar": resumen_base.filter(estado="contabilizado", conciliado=False).count(),
            "debitos": resumen_filtrado["debitos"] or Decimal("0.00"),
            "creditos": resumen_filtrado["creditos"] or Decimal("0.00"),
        },
    })


@login_required
@_manejar_validacion_contable
def conciliar_movimiento_bancario(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    movimiento = get_object_or_404(MovimientoBancario, id=movimiento_id, empresa=empresa)
    redirect_url = _retorno_bancario(request, empresa, "conciliacion_bancaria")

    if request.method != "POST":
        return redirect(redirect_url)
    if movimiento.estado != "contabilizado":
        messages.error(request, "Solo se pueden conciliar movimientos ya contabilizados.")
        return redirect(redirect_url)

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk, empresa=empresa)
        if PeriodoContable.fecha_bloqueada(empresa, movimiento.fecha):
            messages.error(request, "No se puede conciliar un movimiento de un período cerrado. Primero solicita su reapertura.")
            return redirect(redirect_url)
        if movimiento.estado != "contabilizado" or not movimiento.asiento_id or movimiento.asiento.empresa_id != empresa.pk or movimiento.asiento.estado != "contabilizado":
            messages.error(request, "El movimiento debe tener un asiento contabilizado de esta empresa antes de conciliarse.")
            return redirect(redirect_url)
        movimiento.conciliado = True
        movimiento.fecha_conciliacion = timezone.now()
        movimiento.conciliado_por = request.user
        movimiento.save(update_fields=["conciliado", "fecha_conciliacion", "conciliado_por"])
    messages.success(request, "Movimiento conciliado correctamente.")
    return redirect(redirect_url)


@login_required
@_manejar_validacion_contable
def desconciliar_movimiento_bancario(request, empresa_slug, movimiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    movimiento = get_object_or_404(MovimientoBancario, id=movimiento_id, empresa=empresa)
    redirect_url = _retorno_bancario(request, empresa, "conciliacion_bancaria")

    if request.method != "POST":
        return redirect(redirect_url)

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk, empresa=empresa)
        if PeriodoContable.fecha_bloqueada(empresa, movimiento.fecha):
            messages.error(request, "No se puede desconciliar un movimiento de un período cerrado. Primero solicita su reapertura.")
            return redirect(redirect_url)
        movimiento.conciliado = False
        movimiento.fecha_conciliacion = None
        movimiento.conciliado_por = None
        movimiento.save(update_fields=["conciliado", "fecha_conciliacion", "conciliado_por"])
    messages.success(request, "Movimiento marcado como no conciliado.")
    return redirect(redirect_url)


@login_required
def catalogo_cuentas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    cuentas = ordenar_catalogo(CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "nombre", "id"))
    context = {
        "empresa": empresa,
        "es_rumi": es_empresa_rumi(empresa),
        "catalogo_rumi_instalado": usa_catalogo_rumi(empresa),
        "cuentas": cuentas,
        "resumen": {
            "total": len(cuentas),
            "activas": sum(cuenta.activa for cuenta in cuentas),
            "movimiento": sum(cuenta.acepta_movimientos for cuenta in cuentas),
            "agrupadoras": sum(not cuenta.acepta_movimientos for cuenta in cuentas),
        },
    }
    return render(request, "contabilidad/catalogo_cuentas.html", context)


@login_required
def cargar_catalogo_base_honduras_view(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if request.method != "POST":
        return redirect("catalogo_cuentas", empresa_slug=empresa.slug)

    try:
        resultado = cargar_catalogo_base_honduras(empresa)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
        return redirect("catalogo_cuentas", empresa_slug=empresa.slug)
    if resultado["creadas"] or resultado["cuentas_financieras_creadas"]:
        messages.success(
            request,
            f"Catalogo base Honduras cargado: {resultado['creadas']} cuentas creadas, {resultado['existentes']} ya existentes, {resultado['cuentas_financieras_creadas']} cuentas financieras listas y {resultado['clasificaciones_bancarias_creadas']} clasificaciones bancarias listas.",
        )
    else:
        messages.info(request, "El catalogo base Honduras ya estaba cargado para esta empresa.")
    return redirect("catalogo_cuentas", empresa_slug=empresa.slug)


@login_required
def crear_cuenta_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CuentaContableForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cuenta = form.save(commit=False)
        cuenta.empresa = empresa
        try:
            cuenta.full_clean()
            cuenta.save()
        except ValidationError as error:
            _agregar_errores_validacion_form(form, error)
        except IntegrityError:
            form.add_error("codigo", "Ya existe una cuenta contable con este codigo en esta empresa.")
        else:
            messages.success(request, f"Cuenta {cuenta.codigo} creada correctamente.")
            return redirect("catalogo_cuentas", empresa_slug=empresa.slug)
    return render(request, "contabilidad/cuenta_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva Cuenta Contable",
    })


def _agregar_errores_validacion_form(form, error):
    if hasattr(error, "message_dict"):
        for campo, mensajes in error.message_dict.items():
            destino = campo if campo in form.fields else None
            for mensaje in mensajes:
                form.add_error(destino, mensaje)
        return
    mensajes = error.messages if hasattr(error, "messages") else [str(error)]
    for mensaje in mensajes:
        form.add_error(None, mensaje)


FIRMA_REVISION_CATALOGO = "contabilidad.importacion-catalogo.v1"


def _vista_previa_catalogo(datos_firmados):
    resultado = datos_firmados["resultado"]
    tipos = dict(CuentaContable.TIPO_CHOICES)
    prefijo = datos_firmados["opciones"].get("prefijo_codigos", "")
    filas = []
    for datos in resultado["datos"]:
        fila = dict(datos)
        fila["tipo_nombre"] = tipos.get(datos["tipo"], datos["tipo"])
        if prefijo:
            fila["codigo_original"] = datos["codigo"].removeprefix(prefijo)
        for campo in ("activa", "acepta_movimientos"):
            fila[f"{campo}_texto"] = (
                "Sí" if datos[campo] else "No"
            ) if campo in datos else "Sin modificar / predeterminado"
        filas.append(fila)
    return {
        **{clave: valor for clave, valor in resultado.items() if clave not in {"datos", "firma_existentes"}},
        "archivo_nombre": datos_firmados["archivo_nombre"],
        "formato_nombre": {
            "rumi": "Propuesta RUMI", "estandar": "Estándar", "jerarquico": "Jerárquico",
        }.get(resultado["formato"], resultado["formato"]),
        "opciones": datos_firmados["opciones"],
        "filas": filas[:50],
        "filas_adicionales": filas[50:],
        "total": len(resultado["datos"]),
        "no_mostradas": max(len(filas) - 50, 0),
    }


@login_required
@require_http_methods(["GET", "POST"])
def importar_catalogo_cuentas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_catalogo_cuentas")
    if es_empresa_rumi(empresa):
        return _importar_catalogo_rumi(request, empresa)

    form = ImportarCatalogoCuentasForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = importar_catalogo_cuentas_desde_excel(
                empresa,
                form.cleaned_data["archivo"],
                actualizar_existentes=form.cleaned_data["actualizar_existentes"],
            )
        except ValidationError as error:
            mensajes = error.messages if hasattr(error, "messages") else [str(error)]
            for mensaje in mensajes:
                messages.error(request, mensaje)
        else:
            messages.success(
                request,
                f"Catalogo importado: {resultado['creadas']} creadas, {resultado['actualizadas']} actualizadas.",
            )
            return redirect("catalogo_cuentas", empresa_slug=empresa.slug)
    return render(request, "contabilidad/importar_catalogo_cuentas.html", {
        "empresa": empresa,
        "form": form,
    })


def _importar_catalogo_rumi(request, empresa):
    accion = request.POST.get("accion", "revisar") if request.method == "POST" else "revisar"
    form = ImportarCatalogoCuentasRumiForm(request.POST or None, request.FILES or None)
    vista_previa, revision_token, error_importacion = None, "", ""

    if request.method == "POST" and accion == "importar":
        form = ImportarCatalogoCuentasRumiForm()
        try:
            try:
                datos_firmados = signing.loads(
                    request.POST.get("revision_token", ""),
                    salt=FIRMA_REVISION_CATALOGO,
                    max_age=1800,
                )
            except signing.SignatureExpired:
                raise ValidationError("La revisión venció. Vuelve a subir y revisar el archivo antes de importarlo.")
            except signing.BadSignature:
                raise ValidationError("La revisión falta o fue modificada. Vuelve a revisar el archivo antes de importarlo.")
            if (
                datos_firmados.get("empresa_id") != empresa.pk
                or datos_firmados.get("usuario_id") != request.user.pk
                or datos_firmados.get("version") != 1
            ):
                raise ValidationError("Esta revisión pertenece a otra empresa o usuario. Revisa el archivo desde esta empresa.")

            form = ImportarCatalogoCuentasRumiForm(initial=datos_firmados["opciones"])
            vista_previa = _vista_previa_catalogo(datos_firmados)
            revision_token = request.POST["revision_token"]
            if not forms.BooleanField(required=False).clean(request.POST.get("confirmar")):
                error_importacion = "Marca la confirmación después de revisar las cuentas y las opciones de importación."
            else:
                try:
                    resultado = aplicar_catalogo_cuentas(
                        empresa,
                        datos_firmados["resultado"]["datos"],
                        actualizar_existentes=datos_firmados["opciones"]["actualizar_existentes"],
                        firma_existentes=datos_firmados["resultado"]["firma_existentes"],
                    )
                except ValidationError as error:
                    vista_previa, revision_token = None, ""
                    raise ValidationError(error.messages + ["Vuelve a subir y revisar el archivo para preparar una importación actualizada."])
                messages.success(
                    request,
                    f"Catálogo importado en {empresa.nombre}: {resultado['creadas']} cuentas creadas, "
                    f"{resultado['actualizadas']} actualizadas y {resultado['sin_cambios']} conservadas.",
                )
                return redirect("catalogo_cuentas", empresa_slug=empresa.slug)
        except ValidationError as error:
            error_importacion = "; ".join(error.messages)

    elif request.method == "POST" and accion == "revisar" and form.is_valid():
        try:
            opciones = {
                "actualizar_existentes": form.cleaned_data["actualizar_existentes"],
                "incluir_controles": form.cleaned_data["incluir_controles"],
                "hoja": form.cleaned_data["hoja"],
                "prefijo_codigos": form.cleaned_data["prefijo_codigos"],
            }
            archivo = form.cleaned_data["archivo"]
            resultado = analizar_catalogo_cuentas_desde_excel(empresa, archivo, **opciones)
            datos_firmados = {
                "version": 1,
                "empresa_id": empresa.pk,
                "usuario_id": request.user.pk,
                "archivo_nombre": archivo.name,
                "opciones": opciones,
                "resultado": resultado,
            }
            vista_previa = _vista_previa_catalogo(datos_firmados)
            revision_token = signing.dumps(datos_firmados, salt=FIRMA_REVISION_CATALOGO, compress=True)
        except ValidationError as error:
            form.add_error(None, error)
    elif request.method == "POST" and accion != "revisar":
        error_importacion = "Elige revisar el archivo o confirmar una revisión válida."

    return render(request, "contabilidad/importar_catalogo_cuentas_rumi.html", {
        "empresa": empresa,
        "form": form,
        "vista_previa": vista_previa,
        "revision_token": revision_token,
        "error_importacion": error_importacion,
    })


@login_required
def editar_cuenta_contable(request, empresa_slug, cuenta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cuenta = get_object_or_404(CuentaContable, id=cuenta_id, empresa=empresa)
    form = CuentaContableForm(request.POST or None, instance=cuenta, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cuenta = form.save(commit=False)
        try:
            cuenta.full_clean()
            cuenta.save()
        except ValidationError as error:
            _agregar_errores_validacion_form(form, error)
        except IntegrityError:
            form.add_error("codigo", "Ya existe una cuenta contable con este codigo en esta empresa.")
        else:
            messages.success(request, f"Cuenta {cuenta.codigo} actualizada correctamente.")
            return redirect("catalogo_cuentas", empresa_slug=empresa.slug)
    return render(request, "contabilidad/cuenta_form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Editar Cuenta: {cuenta.codigo}",
    })


@login_required
def asientos_contables(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilidad")
    return render(request, "contabilidad/asientos_contables.html", contexto_listado_asientos(empresa, request.GET))




def _totales_lineas_formset(formset):
    total_debe = Decimal("0.00")
    total_haber = Decimal("0.00")
    lineas_validas = 0
    for form in formset.forms:
        if not getattr(form, "cleaned_data", None):
            continue
        if form.cleaned_data.get("DELETE"):
            continue
        cuenta = form.cleaned_data.get("cuenta")
        debe = form.cleaned_data.get("debe") or Decimal("0.00")
        haber = form.cleaned_data.get("haber") or Decimal("0.00")
        if not cuenta and debe == 0 and haber == 0:
            continue
        lineas_validas += 1
        total_debe += debe
        total_haber += haber
    return lineas_validas, total_debe, total_haber


def _formset_asiento_balanceado(formset):
    lineas_validas, total_debe, total_haber = _totales_lineas_formset(formset)
    return lineas_validas >= 2 and total_debe > 0 and total_debe == total_haber


def _contexto_form_asiento(empresa, form, formset, titulo, cuentas_disponibles):
    return {
        "empresa": empresa,
        "form": form,
        "formset": formset,
        "titulo": titulo,
        "cuentas_busqueda": [
            {"id": cuenta.id, "codigo": cuenta.codigo, "nombre": cuenta.nombre}
            for cuenta in cuentas_disponibles
        ],
    }


@login_required
@_manejar_validacion_contable
def crear_asiento_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_crear_asientos")
    _verificar_contabilizacion_solicitada(request, empresa)
    asiento = AsientoContable(empresa=empresa, creado_por=request.user)
    form = AsientoContableForm(request.POST or None, instance=asiento, usuario=request.user, empresa=empresa)
    cuentas_disponibles = CuentaContable.objects.filter(
        empresa=empresa,
        activa=True,
        acepta_movimientos=True,
    ).order_by("codigo")
    formset = LineaAsientoFormSet(request.POST or None, instance=asiento)
    for child_form in formset.forms:
        if "cuenta" in child_form.fields:
            child_form.fields["cuenta"].queryset = cuentas_disponibles
    if hasattr(formset, "empty_form") and "cuenta" in formset.empty_form.fields:
        formset.empty_form.fields["cuenta"].queryset = cuentas_disponibles

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        if PeriodoContable.fecha_bloqueada(empresa, form.cleaned_data["fecha"]):
            messages.error(request, "No se puede crear el asiento porque el periodo contable esta cerrado.")
            return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Nuevo Asiento Contable", cuentas_disponibles))
        if not _formset_asiento_balanceado(formset):
            messages.error(request, "No se puede guardar el asiento: el total del Debe debe ser igual al total del Haber y debe tener al menos dos lineas validas.")
            return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Nuevo Asiento Contable", cuentas_disponibles))

        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            asiento = form.save(commit=False)
            asiento.empresa = empresa
            asiento.creado_por = request.user
            asiento.estado = "borrador"
            asiento.save()
            formset.instance = asiento
            lineas = formset.save(commit=False)
            for deleted in formset.deleted_objects:
                deleted.delete()
            for linea in lineas:
                linea.asiento = asiento
                linea.full_clean()
                linea.save()
            if form.cleaned_data.get("contabilizar_ahora") or form.cleaned_data.get("estado") == "contabilizado":
                asiento.generar_numero()
                asiento.estado = "contabilizado"
                asiento.save(update_fields=["numero", "estado"])
                messages.success(request, f"Asiento {asiento.numero} contabilizado correctamente.")
            else:
                messages.success(request, "Asiento guardado correctamente.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)

    return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Nuevo Asiento Contable", cuentas_disponibles))


@login_required
@_manejar_validacion_contable
def editar_asiento_contable(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_crear_asientos")
    _verificar_contabilizacion_solicitada(request, empresa)
    asiento = get_object_or_404(AsientoContable.objects.prefetch_related("lineas"), id=asiento_id, empresa=empresa)
    if asiento.estado != "borrador":
        messages.error(request, "Solo se pueden editar asientos en borrador. Para corregir un asiento contabilizado, reversalo o duplicalo.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)

    form = AsientoContableForm(request.POST or None, instance=asiento, usuario=request.user, empresa=empresa)
    cuentas_disponibles = CuentaContable.objects.filter(
        empresa=empresa,
        activa=True,
        acepta_movimientos=True,
    ).order_by("codigo")
    formset = LineaAsientoFormSet(request.POST or None, instance=asiento)
    for child_form in formset.forms:
        if "cuenta" in child_form.fields:
            child_form.fields["cuenta"].queryset = cuentas_disponibles
    if hasattr(formset, "empty_form") and "cuenta" in formset.empty_form.fields:
        formset.empty_form.fields["cuenta"].queryset = cuentas_disponibles

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        if PeriodoContable.fecha_bloqueada(empresa, form.cleaned_data["fecha"]):
            messages.error(request, "No se puede editar el asiento porque el periodo contable esta cerrado.")
            return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Editar Asiento Contable", cuentas_disponibles))
        if not _formset_asiento_balanceado(formset):
            messages.error(request, "No se puede guardar el asiento: el total del Debe debe ser igual al total del Haber y debe tener al menos dos lineas validas.")
            return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Editar Asiento Contable", cuentas_disponibles))

        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            actual = AsientoContable.objects.select_for_update().get(pk=asiento.pk, empresa=empresa)
            if actual.estado != "borrador":
                messages.error(request, "El asiento cambió mientras lo editabas. Vuelve a revisarlo.")
                return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.pk)
            asiento = form.save(commit=False)
            asiento.empresa = empresa
            asiento.estado = "borrador"
            asiento.save()
            formset.instance = asiento
            lineas = formset.save(commit=False)
            for deleted in formset.deleted_objects:
                deleted.delete()
            for linea in lineas:
                linea.asiento = asiento
                linea.full_clean()
                linea.save()
            if form.cleaned_data.get("contabilizar_ahora") or form.cleaned_data.get("estado") == "contabilizado":
                asiento.generar_numero()
                asiento.estado = "contabilizado"
                asiento.save(update_fields=["numero", "estado"])
                messages.success(request, f"Asiento {asiento.numero} contabilizado correctamente.")
            else:
                messages.success(request, "Asiento actualizado correctamente.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)

    return render(request, "contabilidad/asiento_form.html", _contexto_form_asiento(empresa, form, formset, "Editar Asiento Contable", cuentas_disponibles))


@login_required
def ver_asiento_contable(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    asiento = get_object_or_404(AsientoContable.objects.prefetch_related("lineas__cuenta"), id=asiento_id, empresa=empresa)
    from .models import RegularizacionMonedaFactura, RevisionConversionAsiento
    revision_moneda = RegularizacionMonedaFactura.objects.filter(empresa=empresa, asiento_origen=asiento).select_related("asiento_ajuste").first()
    revision_integral = RevisionConversionAsiento.objects.filter(empresa=empresa).filter(
        Q(origen=asiento) | Q(ajuste=asiento)
    ).select_related("origen", "ajuste", "creado_por").first()
    puede_regularizar_moneda = (
        not revision_moneda and not revision_integral and asiento.estado == "contabilizado" and not asiento.conversion_verificada
        and asiento.documento_tipo == "factura" and asiento.evento == "emision"
        and Factura.objects.filter(pk=asiento.documento_id, empresa=empresa, cliente__empresa=empresa, moneda="USD", estado="emitida").exists()
    )
    return render(request, "contabilidad/ver_asiento.html", {
        "empresa": empresa,
        "asiento": asiento,
        "revision_moneda": revision_moneda,
        "revision_integral": revision_integral,
        "puede_regularizar_moneda": puede_regularizar_moneda,
    })


@login_required
@require_POST
@_manejar_validacion_contable
def duplicar_asiento_contable(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_crear_asientos")
    asiento_origen = get_object_or_404(
        AsientoContable.objects.prefetch_related("lineas"),
        id=asiento_id,
        empresa=empresa,
    )
    fecha = timezone.localdate()
    if PeriodoContable.fecha_bloqueada(empresa, fecha):
        messages.error(request, "No se puede duplicar el asiento porque el periodo actual esta cerrado.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.id)
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        nuevo = AsientoContable.objects.create(
            empresa=empresa,
            fecha=fecha,
            descripcion=f"Copia de {asiento_origen.descripcion}",
            referencia=asiento_origen.referencia,
            origen_modulo=asiento_origen.origen_modulo or "manual",
            documento_tipo="asiento_duplicado",
            documento_id=asiento_origen.id,
            evento=None,
            creado_por=request.user,
            estado="borrador",
        )
        for linea in asiento_origen.lineas.all():
            LineaAsientoContable.objects.create(
                asiento=nuevo,
                cuenta=linea.cuenta,
                detalle=linea.detalle,
                debe=linea.debe,
                haber=linea.haber,
            )
    messages.success(request, "Asiento duplicado como borrador para revision.")
    return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=nuevo.id)


@login_required
@require_POST
@_manejar_validacion_contable
def reversar_asiento_contable(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    asiento_origen = get_object_or_404(
        AsientoContable.objects.prefetch_related("lineas"),
        id=asiento_id,
        empresa=empresa,
    )
    if asiento_origen.estado != "contabilizado":
        messages.error(request, "Solo se pueden reversar asientos contabilizados.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.id)
    if AsientoContable.objects.filter(
        empresa=empresa,
        documento_tipo="asiento_contable",
        documento_id=asiento_origen.id,
        evento="reversion",
        estado="contabilizado",
    ).exists():
        messages.warning(request, "Este asiento ya tiene una reversion contabilizada.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.id)
    fecha = timezone.localdate()
    if PeriodoContable.fecha_bloqueada(empresa, fecha):
        messages.error(request, "No se puede reversar el asiento porque el periodo actual esta cerrado.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.id)
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        asiento_origen = AsientoContable.objects.select_for_update().get(pk=asiento_origen.pk, empresa=empresa)
        if asiento_origen.documento_tipo == "pago_factura" and PagoFactura.objects.filter(
            pk=asiento_origen.documento_id, factura__empresa=empresa, anulado=True,
        ).exists():
            raise ValidationError(
                "El asiento respalda un cobro anulado. Conserve el cobro original y su reversión; no se pueden reversar por separado."
            )
        from .proteccion_conversion import asiento_tiene_revision
        if asiento_tiene_revision(asiento_origen.pk):
            messages.error(request, "El asiento respalda una revisión integral documentada. Registre un nuevo ajuste para conservar su respaldo.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.pk)
        if asiento_origen.estado != "contabilizado" or AsientoContable.objects.filter(
            empresa=empresa, documento_tipo="asiento_contable", documento_id=asiento_origen.pk,
            evento="reversion", estado="contabilizado",
        ).exists():
            messages.warning(request, "El asiento cambió o ya fue reversado. Vuelve a revisarlo.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento_origen.pk)
        reversion = AsientoContable.objects.create(
            empresa=empresa,
            fecha=fecha,
            descripcion=f"Reversion de {asiento_origen.numero or asiento_origen.descripcion}",
            referencia=asiento_origen.referencia,
            origen_modulo=asiento_origen.origen_modulo or "manual",
            documento_tipo="asiento_contable",
            documento_id=asiento_origen.id,
            evento="reversion",
            creado_por=request.user,
            estado="borrador",
        )
        for linea in asiento_origen.lineas.all():
            LineaAsientoContable.objects.create(
                asiento=reversion,
                cuenta=linea.cuenta,
                detalle=f"Reversion {linea.detalle or asiento_origen.descripcion}",
                debe=linea.haber,
                haber=linea.debe,
            )
        reversion.generar_numero()
        reversion.estado = "contabilizado"
        reversion.save(update_fields=["numero", "estado"])
    messages.success(request, f"Reversion contabilizada en asiento {reversion.numero}.")
    return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=reversion.id)


@login_required
@require_POST
@_manejar_validacion_contable
def regresar_asiento_borrador(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    asiento = get_object_or_404(AsientoContable, id=asiento_id, empresa=empresa)
    if asiento.estado != "contabilizado":
        messages.warning(request, "Solo los asientos contabilizados pueden regresar a borrador.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)
    if PeriodoContable.fecha_bloqueada(empresa, asiento.fecha):
        messages.error(request, "No se puede regresar a borrador porque el periodo contable esta cerrado.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        asiento = AsientoContable.objects.select_for_update().get(pk=asiento.pk, empresa=empresa)
        if asiento.estado != "contabilizado" or PeriodoContable.fecha_bloqueada(empresa, asiento.fecha):
            messages.error(request, "El asiento cambió o su período está cerrado. Vuelve a revisarlo.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.pk)
        asiento.estado = "borrador"
        asiento.save(update_fields=["estado"])
    messages.success(request, f"Asiento {asiento.numero or asiento.id} regresado a borrador. Ya puedes editarlo y volver a contabilizarlo.")
    return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)


@login_required
@require_POST
@_manejar_validacion_contable
def contabilizar_asiento_contable(request, empresa_slug, asiento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_contabilizar_asientos")
    asiento = get_object_or_404(AsientoContable, id=asiento_id, empresa=empresa)
    if asiento.estado != "borrador":
        messages.warning(request, "Solo los asientos en borrador pueden contabilizarse.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)
    if not asiento.esta_balanceado:
        messages.error(request, "El asiento no esta balanceado. Revisa debe y haber antes de contabilizar.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)
    if PeriodoContable.fecha_bloqueada(empresa, asiento.fecha):
        messages.error(request, "No se puede contabilizar porque el periodo contable esta cerrado.")
        return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)
    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        asiento = AsientoContable.objects.select_for_update().get(pk=asiento.pk, empresa=empresa)
        if asiento.estado != "borrador" or not asiento.esta_balanceado or PeriodoContable.fecha_bloqueada(empresa, asiento.fecha):
            messages.error(request, "El asiento cambió o su período está cerrado. Vuelve a revisarlo.")
            return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.pk)
        asiento.generar_numero()
        asiento.estado = "contabilizado"
        asiento.save(update_fields=["numero", "estado"])
    messages.success(request, f"Asiento {asiento.numero} contabilizado correctamente.")
    return redirect("ver_asiento_contable", empresa_slug=empresa.slug, asiento_id=asiento.id)


@login_required
def reportes_contables(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    asientos = AsientoContable.objects.filter(empresa=empresa, estado="contabilizado")
    cuentas = CuentaContable.objects.filter(empresa=empresa)
    context = {
        "empresa": empresa,
        "resumen": {
            "asientos_contabilizados": asientos.count(),
            "debe_total": sum((asiento.total_debe for asiento in asientos), 0),
            "haber_total": sum((asiento.total_haber for asiento in asientos), 0),
            "cuentas_activas": cuentas.filter(activa=True).count(),
        },
        "cuentas_por_tipo": (
            cuentas.values("tipo").annotate(total=Count("id")).order_by("tipo")
        ),
    }
    return render(request, "contabilidad/reportes_contables.html", context)


def _aplicar_filtros_fecha_documentos(queryset, campo_fecha, fecha_inicio, fecha_fin):
    if fecha_inicio:
        queryset = queryset.filter(**{f"{campo_fecha}__gte": fecha_inicio})
    if fecha_fin:
        queryset = queryset.filter(**{f"{campo_fecha}__lte": fecha_fin})
    return queryset


def _variacion_porcentual(actual, anterior):
    actual = actual or Decimal("0.00")
    anterior = anterior or Decimal("0.00")
    if anterior == 0:
        return Decimal("100.00") if actual > 0 else Decimal("0.00")
    return ((actual - anterior) / anterior) * Decimal("100.00")


@login_required
def dashboard_bi_financiero(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    hoy = timezone.localdate()
    inicio_mes_actual = hoy.replace(day=1)
    if inicio_mes_actual.month == 1:
        inicio_mes_anterior = inicio_mes_actual.replace(year=inicio_mes_actual.year - 1, month=12)
    else:
        inicio_mes_anterior = inicio_mes_actual.replace(month=inicio_mes_actual.month - 1)

    compras_qs = (
        CompraInventario.objects.filter(empresa=empresa, estado="aplicada", fecha_documento__lte=hoy)
        .select_related("proveedor")
        .prefetch_related("lineas")
        .order_by("fecha_documento", "id")
    )
    compras = list(compras_qs)
    pagos_qs = (
        PagoCompra.objects.filter(compra__empresa=empresa, compra__estado="aplicada", fecha__lte=hoy)
        .select_related("compra__proveedor", "cuenta_financiera")
        .order_by("fecha", "id")
    )
    pagos = list(pagos_qs)
    movimientos_qs = (
        MovimientoBancario.objects.filter(empresa=empresa, fecha__lte=hoy)
        .select_related("cuenta_financiera")
        .order_by("fecha", "id")
    )
    movimientos = list(movimientos_qs)

    compras_mes_actual = [compra for compra in compras if compra.fecha_documento >= inicio_mes_actual]
    compras_mes_anterior = [
        compra for compra in compras
        if inicio_mes_anterior <= compra.fecha_documento < inicio_mes_actual
    ]
    pagos_mes_actual = [pago for pago in pagos if pago.fecha >= inicio_mes_actual]
    pagos_mes_anterior = [
        pago for pago in pagos
        if inicio_mes_anterior <= pago.fecha < inicio_mes_actual
    ]
    movimientos_mes_actual = [mov for mov in movimientos if mov.fecha >= inicio_mes_actual]

    total_compras_actual = sum((compra.total_documento for compra in compras_mes_actual), Decimal("0.00"))
    total_compras_anterior = sum((compra.total_documento for compra in compras_mes_anterior), Decimal("0.00"))
    total_pagos_actual = sum((pago.monto for pago in pagos_mes_actual), Decimal("0.00"))
    total_pagos_anterior = sum((pago.monto for pago in pagos_mes_anterior), Decimal("0.00"))
    saldo_total = sum((compra.saldo_pendiente for compra in compras), Decimal("0.00"))
    total_documentos_actual = len(compras_mes_actual)
    ticket_promedio = (
        total_compras_actual / Decimal(str(total_documentos_actual))
        if total_documentos_actual else Decimal("0.00")
    )
    tasa_pago_actual = (
        (total_pagos_actual / total_compras_actual) * Decimal("100.00")
        if total_compras_actual > 0 else Decimal("0.00")
    )

    compras_por_mes_map = defaultdict(lambda: {"comprado": Decimal("0.00"), "pagado": Decimal("0.00"), "documentos": 0})
    for compra in compras:
        clave = compra.fecha_documento.replace(day=1)
        compras_por_mes_map[clave]["comprado"] += compra.total_documento
        compras_por_mes_map[clave]["documentos"] += 1
    for pago in pagos:
        clave = pago.fecha.replace(day=1)
        compras_por_mes_map[clave]["pagado"] += pago.monto

    compras_por_mes = []
    if compras_por_mes_map:
        max_comprado = max((item["comprado"] for item in compras_por_mes_map.values()), default=Decimal("0.00"))
        max_pagado = max((item["pagado"] for item in compras_por_mes_map.values()), default=Decimal("0.00"))
        max_valor = max(max_comprado, max_pagado, Decimal("1.00"))
        for mes in sorted(compras_por_mes_map.keys())[-6:]:
            item = compras_por_mes_map[mes]
            compras_por_mes.append({
                "mes": mes,
                "etiqueta": formats.date_format(mes, "M Y"),
                "comprado": item["comprado"],
                "pagado": item["pagado"],
                "documentos": item["documentos"],
                "alto_comprado": float((item["comprado"] / max_valor) * Decimal("100.00")) if max_valor else 0,
                "alto_pagado": float((item["pagado"] / max_valor) * Decimal("100.00")) if max_valor else 0,
            })

    top_proveedores_map = defaultdict(lambda: {"total": Decimal("0.00"), "documentos": 0})
    for compra in compras:
        nombre = (compra.proveedor.nombre if compra.proveedor_id else compra.proveedor_nombre) or "Proveedor sin nombre"
        top_proveedores_map[nombre]["total"] += compra.total_documento
        top_proveedores_map[nombre]["documentos"] += 1
    top_proveedores = [
        {"nombre": nombre, "total": data["total"], "documentos": data["documentos"]}
        for nombre, data in top_proveedores_map.items()
    ]
    top_proveedores.sort(key=lambda item: item["total"], reverse=True)
    top_proveedores = top_proveedores[:6]
    max_proveedor = max((item["total"] for item in top_proveedores), default=Decimal("1.00"))
    for item in top_proveedores:
        item["ancho"] = float((item["total"] / max_proveedor) * Decimal("100.00")) if max_proveedor else 0

    proveedores_vencidos = []
    for compra in compras:
        if not compra.esta_vencida:
            continue
        proveedores_vencidos.append({
            "nombre": (compra.proveedor.nombre if compra.proveedor_id else compra.proveedor_nombre) or "Proveedor sin nombre",
            "saldo": compra.saldo_pendiente,
            "dias_vencido": (hoy - compra.fecha_control_cxp).days,
            "numero_compra": compra.numero_compra or f"COM-{compra.id:08d}",
        })
    proveedores_vencidos.sort(key=lambda item: item["saldo"], reverse=True)
    proveedores_vencidos = proveedores_vencidos[:6]
    max_vencido = max((item["saldo"] for item in proveedores_vencidos), default=Decimal("1.00"))
    for item in proveedores_vencidos:
        item["ancho"] = float((item["saldo"] / max_vencido) * Decimal("100.00")) if max_vencido else 0

    egresos_por_banco_map = defaultdict(lambda: {"total": Decimal("0.00"), "operaciones": 0, "tipo": "Caja"})
    for pago in pagos:
        nombre = "Caja operativa"
        tipo = "Caja"
        if pago.cuenta_financiera_id:
            nombre = pago.cuenta_financiera.nombre
            tipo = pago.cuenta_financiera.get_tipo_display()
        egresos_por_banco_map[nombre]["total"] += pago.monto
        egresos_por_banco_map[nombre]["operaciones"] += 1
        egresos_por_banco_map[nombre]["tipo"] = tipo
    egresos_por_banco = [
        {"nombre": nombre, "total": data["total"], "operaciones": data["operaciones"], "tipo": data["tipo"]}
        for nombre, data in egresos_por_banco_map.items()
    ]
    egresos_por_banco.sort(key=lambda item: item["total"], reverse=True)
    egresos_por_banco = egresos_por_banco[:6]
    max_banco = max((item["total"] for item in egresos_por_banco), default=Decimal("1.00"))
    for item in egresos_por_banco:
        item["ancho"] = float((item["total"] / max_banco) * Decimal("100.00")) if max_banco else 0

    resumen_movimientos = []
    estados_mov = [
        ("pendiente", "Pendiente", "#f59e0b"),
        ("clasificado", "Clasificado", "#2968f2"),
        ("contabilizado", "Contabilizado", "#14b8a6"),
    ]
    total_movimientos = len(movimientos)
    pendientes_conciliar = sum(1 for mov in movimientos if mov.estado == "contabilizado" and not mov.conciliado)
    conciliados = sum(1 for mov in movimientos if mov.conciliado)
    for codigo, etiqueta, color in estados_mov:
        cantidad = sum(1 for mov in movimientos if mov.estado == codigo)
        monto = sum((mov.monto for mov in movimientos if mov.estado == codigo), Decimal("0.00"))
        porcentaje = (Decimal(cantidad) / Decimal(total_movimientos) * Decimal("100.00")) if total_movimientos else Decimal("0.00")
        resumen_movimientos.append({
            "codigo": codigo,
            "etiqueta": etiqueta,
            "cantidad": cantidad,
            "monto": monto,
            "color": color,
            "porcentaje": porcentaje,
        })

    total_volumen_bancario = sum((mov.monto for mov in movimientos_mes_actual), Decimal("0.00"))

    context = {
        "empresa": empresa,
        "compras": compras,
        "compras_borrador": CompraInventario.objects.filter(empresa=empresa, estado="borrador").count(),
        "inicio_mes_actual": inicio_mes_actual,
        "kpis_bi": {
            "total_compras_actual": total_compras_actual,
            "variacion_compras": _variacion_porcentual(total_compras_actual, total_compras_anterior),
            "documentos_actual": total_documentos_actual,
            "pagos_actual": total_pagos_actual,
            "variacion_pagos": _variacion_porcentual(total_pagos_actual, total_pagos_anterior),
            "saldo_total": saldo_total,
            "ticket_promedio": ticket_promedio,
            "tasa_pago_actual": tasa_pago_actual,
            "movimientos_mes": len(movimientos_mes_actual),
            "conciliados": conciliados,
            "pendientes_conciliar": pendientes_conciliar,
            "total_volumen_bancario": total_volumen_bancario,
        },
        "compras_por_mes": compras_por_mes,
        "top_proveedores": top_proveedores,
        "proveedores_vencidos": proveedores_vencidos,
        "egresos_por_banco": egresos_por_banco,
        "resumen_movimientos": resumen_movimientos,
    }
    return render(request, "contabilidad/dashboard_bi_financiero.html", context)


def _fiscal_documento_lempiras(documento):
    fiscal = documento.resumen_fiscal()
    claves = ("base_15", "base_18", "base_exento", "base_exonerado", "isv_15", "isv_18")
    convertidos = distribuir_conversion(
        [fiscal.get(clave) or Decimal("0.00") for clave in claves],
        documento.moneda, documento.tipo_cambio,
        total_lempiras=convertir_a_lempiras(documento.total, documento.moneda, documento.tipo_cambio),
    )
    resultado = dict(zip(claves, convertidos))
    resultado.update(
        subtotal=sum((resultado[clave] for clave in claves[:4]), Decimal("0.00")),
        descuento_total=convertir_a_lempiras(fiscal.get("descuento_total"), documento.moneda, documento.tipo_cambio),
        impuesto=resultado["isv_15"] + resultado["isv_18"],
        total=sum(convertidos, Decimal("0.00")),
    )
    return resultado


def _resumen_fiscal_documentos(documentos):
    resumen = {
        "subtotal": Decimal("0.00"),
        "descuento_total": Decimal("0.00"),
        "base_15": Decimal("0.00"),
        "base_18": Decimal("0.00"),
        "base_exento": Decimal("0.00"),
        "base_exonerado": Decimal("0.00"),
        "isv_15": Decimal("0.00"),
        "isv_18": Decimal("0.00"),
        "impuesto": Decimal("0.00"),
        "total": Decimal("0.00"),
        "documentos": documentos.count(),
    }
    for documento in documentos:
        fiscal = _fiscal_documento_lempiras(documento)
        documento.fiscal_lempiras = fiscal
        for clave in fiscal:
            resumen[clave] += fiscal[clave]
    return resumen


def _reporte_impuestos_data(empresa, fecha_inicio="", fecha_fin=""):
    facturas = Factura.objects.filter(
        empresa=empresa,
        estado="emitida",
    ).select_related("cliente").prefetch_related("lineas__impuesto").order_by("fecha_emision", "numero_factura", "id")
    notas_credito = NotaCredito.objects.filter(
        empresa=empresa,
        estado="emitida",
    ).select_related("cliente", "factura_origen").prefetch_related("lineas__impuesto").order_by("fecha_emision", "numero_nota", "id")
    compras = RegistroCompraFiscal.objects.filter(cliente_contable__isnull=True,
        empresa=empresa,
        estado="registrada",
    ).select_related("proveedor").order_by("fecha_documento", "numero_factura", "id")

    facturas = _aplicar_filtros_fecha_documentos(facturas, "fecha_emision", fecha_inicio, fecha_fin)
    notas_credito = _aplicar_filtros_fecha_documentos(notas_credito, "fecha_emision", fecha_inicio, fecha_fin)
    if empresa.slug == 'demo_1':
        # El crédito de compras se agrupa por el libro donde fue incorporado.
        for valor, inferior in ((fecha_inicio, True), (fecha_fin, False)):
            if not valor:
                continue
            fecha = parse_date(str(valor))
            if fecha:
                comparador = 'gte' if inferior else 'lte'
                estricto = 'gt' if inferior else 'lt'
                compras = compras.filter(Q(**{f'periodo_anio__{estricto}': fecha.year}) |
                    Q(periodo_anio=fecha.year, **{f'periodo_mes__{comparador}': fecha.month}))
    else:
        compras = _aplicar_filtros_fecha_documentos(compras, "fecha_documento", fecha_inicio, fecha_fin)

    ventas = _resumen_fiscal_documentos(facturas)
    creditos = _resumen_fiscal_documentos(notas_credito)
    compras_resumen = compras.aggregate(
        base_15=Sum("base_15"),
        isv_15=Sum("isv_15"),
        base_18=Sum("base_18"),
        isv_18=Sum("isv_18"),
        base_exento=Sum("exento"),
        base_exonerado=Sum("exonerado"),
        total=Sum("total"),
    )
    compras_resumen = {clave: valor or Decimal("0.00") for clave, valor in compras_resumen.items()}
    compras_resumen["documentos"] = compras.count()
    compras_resumen["impuesto"] = compras_resumen["isv_15"] + compras_resumen["isv_18"]
    neto = {
        "base_15": ventas["base_15"] - creditos["base_15"],
        "base_18": ventas["base_18"] - creditos["base_18"],
        "base_exento": ventas["base_exento"] - creditos["base_exento"],
        "base_exonerado": ventas["base_exonerado"] - creditos["base_exonerado"],
        "isv_15": ventas["isv_15"] - creditos["isv_15"],
        "isv_18": ventas["isv_18"] - creditos["isv_18"],
        "impuesto": ventas["impuesto"] - creditos["impuesto"],
        "total": ventas["total"] - creditos["total"],
    }
    isv_a_pagar = neto["impuesto"] - compras_resumen["impuesto"]
    return {
        "facturas": facturas,
        "notas_credito": notas_credito,
        "compras": compras,
        "ventas": ventas,
        "creditos": creditos,
        "compras_resumen": compras_resumen,
        "neto": neto,
        "isv_a_pagar": isv_a_pagar,
        "filtros": {
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
        },
    }


@login_required
def reporte_impuestos(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    data = _reporte_impuestos_data(
        empresa,
        request.GET.get("fecha_inicio") or "",
        request.GET.get("fecha_fin") or "",
    )
    data["empresa"] = empresa
    return _render_reporte(request, "contabilidad/reporte_impuestos.html", data)


@login_required
def exportar_reporte_impuestos_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    data = _reporte_impuestos_data(
        empresa,
        request.GET.get("fecha_inicio") or "",
        request.GET.get("fecha_fin") or "",
    )
    workbook, sheet = _preparar_excel("Reporte de Impuestos")

    sheet.append(["Resumen", "Documentos", "Base 15%", "ISV 15%", "Base 18%", "ISV 18%", "Exento", "Exonerado", "ISV total", "Total"])
    _aplicar_encabezado(sheet, 3)
    sheet.append(["Facturas emitidas", data["ventas"]["documentos"], data["ventas"]["base_15"], data["ventas"]["isv_15"], data["ventas"]["base_18"], data["ventas"]["isv_18"], data["ventas"]["base_exento"], data["ventas"]["base_exonerado"], data["ventas"]["impuesto"], data["ventas"]["total"]])
    sheet.append(["Notas de credito", data["creditos"]["documentos"], data["creditos"]["base_15"], data["creditos"]["isv_15"], data["creditos"]["base_18"], data["creditos"]["isv_18"], data["creditos"]["base_exento"], data["creditos"]["base_exonerado"], data["creditos"]["impuesto"], data["creditos"]["total"]])
    sheet.append(["Neto fiscal", "", data["neto"]["base_15"], data["neto"]["isv_15"], data["neto"]["base_18"], data["neto"]["isv_18"], data["neto"]["base_exento"], data["neto"]["base_exonerado"], data["neto"]["impuesto"], data["neto"]["total"]])
    sheet.append(["Compras acreditables", data["compras_resumen"]["documentos"], data["compras_resumen"]["base_15"], data["compras_resumen"]["isv_15"], data["compras_resumen"]["base_18"], data["compras_resumen"]["isv_18"], data["compras_resumen"]["base_exento"], data["compras_resumen"]["base_exonerado"], data["compras_resumen"]["impuesto"], data["compras_resumen"]["total"]])
    sheet.append(["ISV estimado a pagar", "", "", "", "", "", "", "", data["isv_a_pagar"], ""])
    sheet.append([])
    sheet.append(["Facturas emitidas"])
    sheet.append(["Fecha", "Numero", "Cliente", "Base 15%", "ISV 15%", "Base 18%", "ISV 18%", "Exento", "Exonerado", "ISV", "Total"])
    _aplicar_encabezado(sheet, sheet.max_row)
    for factura in data["facturas"]:
        fiscal = _fiscal_documento_lempiras(factura)
        sheet.append([
            factura.fecha_emision,
            factura.numero_factura or "Sin numero",
            factura.cliente.nombre if factura.cliente else "",
            fiscal["base_15"],
            fiscal["isv_15"],
            fiscal["base_18"],
            fiscal["isv_18"],
            fiscal["base_exento"],
            fiscal["base_exonerado"],
            fiscal["impuesto"],
            fiscal["total"],
        ])
    sheet.append([])
    sheet.append(["Notas de credito"])
    sheet.append(["Fecha", "Numero", "Factura origen", "Cliente", "Base 15%", "ISV 15%", "Base 18%", "ISV 18%", "Exento", "Exonerado", "ISV", "Total"])
    _aplicar_encabezado(sheet, sheet.max_row)
    for nota in data["notas_credito"]:
        fiscal = _fiscal_documento_lempiras(nota)
        sheet.append([
            nota.fecha_emision,
            nota.numero_nota or "Sin numero",
            nota.factura_origen.numero_factura if nota.factura_origen else "",
            nota.cliente.nombre if nota.cliente else "",
            fiscal["base_15"],
            fiscal["isv_15"],
            fiscal["base_18"],
            fiscal["isv_18"],
            fiscal["base_exento"],
            fiscal["base_exonerado"],
            fiscal["impuesto"],
            fiscal["total"],
        ])
    sheet.append([])
    sheet.append(["Compras fiscales"])
    sheet.append(["Fecha", "Factura", "Proveedor", "Base 15%", "ISV 15%", "Base 18%", "ISV 18%", "Exento", "Exonerado", "ISV", "Total"])
    _aplicar_encabezado(sheet, sheet.max_row)
    for compra in data["compras"]:
        sheet.append([
            compra.fecha_documento,
            compra.numero_factura,
            compra.proveedor_nombre,
            compra.base_15,
            compra.isv_15,
            compra.base_18,
            compra.isv_18,
            compra.exento,
            compra.exonerado,
            compra.impuesto_total,
            compra.total,
        ])
    _advertencias_excel_reporte(sheet, empresa, data["filtros"])
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Reporte_Impuestos_{empresa.slug}.xlsx")


def _auditoria_asientos_queryset(empresa, filtros):
    asientos = AsientoContable.objects.filter(empresa=empresa).select_related("creado_por").prefetch_related("lineas")
    if filtros.get("fecha_inicio"):
        asientos = asientos.filter(fecha__gte=filtros["fecha_inicio"])
    if filtros.get("fecha_fin"):
        asientos = asientos.filter(fecha__lte=filtros["fecha_fin"])
    if filtros.get("estado"):
        asientos = asientos.filter(estado=filtros["estado"])
    if filtros.get("origen"):
        asientos = asientos.filter(origen_modulo=filtros["origen"])
    if filtros.get("documento_tipo"):
        asientos = asientos.filter(documento_tipo=filtros["documento_tipo"])
    if filtros.get("usuario"):
        asientos = asientos.filter(creado_por_id=filtros["usuario"])
    return asientos.order_by("-fecha", "-id")


@login_required
def auditoria_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = {
        "fecha_inicio": request.GET.get("fecha_inicio") or "",
        "fecha_fin": request.GET.get("fecha_fin") or "",
        "estado": request.GET.get("estado") or "",
        "origen": request.GET.get("origen") or "",
        "documento_tipo": request.GET.get("documento_tipo") or "",
        "usuario": request.GET.get("usuario") or "",
    }
    asientos = _auditoria_asientos_queryset(empresa, filtros)
    base_asientos = AsientoContable.objects.filter(empresa=empresa)
    usuarios_ids = base_asientos.exclude(creado_por__isnull=True).values_list("creado_por_id", flat=True).distinct()
    context = {
        "empresa": empresa,
        "asientos": asientos,
        "filtros": filtros,
        "estados": AsientoContable.ESTADO_CHOICES,
        "origenes": base_asientos.exclude(origen_modulo__isnull=True).exclude(origen_modulo__exact="").values_list("origen_modulo", flat=True).distinct().order_by("origen_modulo"),
        "documentos": base_asientos.exclude(documento_tipo__isnull=True).exclude(documento_tipo__exact="").values_list("documento_tipo", flat=True).distinct().order_by("documento_tipo"),
        "usuarios": Usuario.objects.filter(id__in=usuarios_ids).order_by("username"),
        "resumen": {
            "total": asientos.count(),
            "contabilizados": asientos.filter(estado="contabilizado").count(),
            "borradores": asientos.filter(estado="borrador").count(),
            "anulados": asientos.filter(estado="anulado").count(),
        },
    }
    return render(request, "contabilidad/auditoria_contable.html", context)


@login_required
def exportar_auditoria_contable_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = {
        "fecha_inicio": request.GET.get("fecha_inicio") or "",
        "fecha_fin": request.GET.get("fecha_fin") or "",
        "estado": request.GET.get("estado") or "",
        "origen": request.GET.get("origen") or "",
        "documento_tipo": request.GET.get("documento_tipo") or "",
        "usuario": request.GET.get("usuario") or "",
    }
    asientos = _auditoria_asientos_queryset(empresa, filtros)
    workbook, sheet = _preparar_excel("Auditoría Contable · HNL")
    sheet.append(["Fecha", "Número", "Estado", "Origen", "Documento", "Evento", "Referencia", "Usuario", "Debe HNL", "Haber HNL", "Creado", "Moneda origen", "Tipo de cambio a HNL", "Conversión verificada"])
    _aplicar_encabezado(sheet, 3)
    for asiento in asientos:
        sheet.append([
            asiento.fecha,
            asiento.numero or "Sin numero",
            asiento.get_estado_display(),
            asiento.origen_modulo or "",
            f"{asiento.documento_tipo or ''} {asiento.documento_id or ''}".strip(),
            asiento.evento or "",
            asiento.referencia or "",
            asiento.creado_por.username if asiento.creado_por else "",
            asiento.total_debe,
            asiento.total_haber,
            asiento.fecha_creacion.replace(tzinfo=None) if asiento.fecha_creacion else "",
            asiento.moneda_origen,
            asiento.tipo_cambio,
            "Sí" if asiento.conversion_verificada else "Pendiente de revisión",
        ])
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Auditoria_Contable_{empresa.slug}.xlsx")


@login_required
def libro_diario(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    fecha_inicio = request.GET.get("fecha_inicio") or ""
    fecha_fin = request.GET.get("fecha_fin") or ""
    cuenta_id = request.GET.get("cuenta") or ""

    lineas = LineaAsientoContable.objects.filter(
        asiento__empresa=empresa,
        asiento__estado="contabilizado",
    ).select_related("asiento", "cuenta").order_by("asiento__fecha", "asiento__numero", "id")

    if fecha_inicio:
        lineas = lineas.filter(asiento__fecha__gte=fecha_inicio)
    if fecha_fin:
        lineas = lineas.filter(asiento__fecha__lte=fecha_fin)
    if cuenta_id:
        lineas = lineas.filter(cuenta_id=cuenta_id)

    totales = lineas.aggregate(debe=Sum("debe"), haber=Sum("haber"))
    context = {
        "empresa": empresa,
        "lineas": lineas,
        "cuentas": CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "nombre"),
        "filtros": {
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "cuenta": cuenta_id,
        },
        "resumen": {
            "debe_total": totales["debe"] or Decimal("0.00"),
            "haber_total": totales["haber"] or Decimal("0.00"),
            "lineas": lineas.count(),
        },
    }
    return _render_reporte(request, "contabilidad/libro_diario.html", context)


@login_required
def exportar_libro_diario_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    fecha_inicio = request.GET.get("fecha_inicio") or ""
    fecha_fin = request.GET.get("fecha_fin") or ""
    cuenta_id = request.GET.get("cuenta") or ""

    lineas = LineaAsientoContable.objects.filter(
        asiento__empresa=empresa,
        asiento__estado="contabilizado",
    ).select_related("asiento", "cuenta").order_by("asiento__fecha", "asiento__numero", "id")
    if fecha_inicio:
        lineas = lineas.filter(asiento__fecha__gte=fecha_inicio)
    if fecha_fin:
        lineas = lineas.filter(asiento__fecha__lte=fecha_fin)
    if cuenta_id:
        lineas = lineas.filter(cuenta_id=cuenta_id)

    workbook, sheet = _preparar_excel("Libro Diario · HNL")
    sheet.append(["Fecha", "Asiento", "Cuenta", "Detalle", "Referencia", "Debe", "Haber"])
    _aplicar_encabezado(sheet, 3)
    for linea in lineas:
        sheet.append([
            linea.asiento.fecha,
            linea.asiento.numero or "Sin numero",
            f"{linea.cuenta.codigo} - {linea.cuenta.nombre}",
            linea.detalle or linea.asiento.descripcion,
            linea.asiento.referencia or "",
            linea.debe,
            linea.haber,
        ])
    _advertencias_excel_reporte(sheet, empresa, {"fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin})
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Libro_Diario_{empresa.slug}.xlsx")


def _filtros_reportes(request):
    return {"fecha_inicio": request.GET.get("fecha_inicio") or "", "fecha_fin": request.GET.get("fecha_fin") or ""}


def _conversiones_pendientes_reporte(empresa, filtros):
    from datetime import date
    from .estado_conversion import asientos_conversion_pendiente
    corte = filtros.get("fecha_fin") or filtros.get("fecha_corte") or None
    if isinstance(corte, str):
        corte = date.fromisoformat(corte)
    asientos = asientos_conversion_pendiente(empresa, fecha_corte=corte)
    # La apertura también forma parte del mayor y del balance; no se oculta por «Desde».
    return asientos.count()


def _render_reporte(request, plantilla, datos):
    datos["conversiones_pendientes"] = _conversiones_pendientes_reporte(datos["empresa"], datos.get("filtros", {}))
    return render(request, plantilla, datos)


def _advertencias_excel_reporte(sheet, empresa, filtros):
    sheet.append([])
    sheet.append(["Empresa", empresa.nombre])
    sheet.append(["Moneda funcional", "Lempiras (HNL)"])
    if filtros.get("fecha_inicio"):
        sheet.append(["Desde", filtros["fecha_inicio"]])
    if filtros.get("fecha_fin") or filtros.get("fecha_corte"):
        sheet.append(["Hasta", filtros.get("fecha_fin") or filtros["fecha_corte"]])
    pendientes = _conversiones_pendientes_reporte(empresa, filtros)
    if pendientes:
        sheet.append(["Conversión histórica pendiente", f"{pendientes} asientos en dólares requieren revisión en un período abierto; se conservan los importes registrados."])


def _filas_excel_mayor(sheet, bloques, fecha_inicio):
    for bloque in bloques:
        cuenta = bloque["cuenta"]
        etiqueta = f"{cuenta.codigo} - {cuenta.nombre}"
        if fecha_inicio:
            sheet.append(["", "", etiqueta, "Saldo inicial anterior al periodo", "", "", "", bloque["saldo_inicial"]])
        for movimiento in bloque["movimientos"]:
            linea = movimiento["linea"]
            sheet.append([
                linea.asiento.fecha, linea.asiento.numero or "Sin número", etiqueta,
                linea.detalle or linea.asiento.descripcion, linea.asiento.referencia or "",
                linea.debe, linea.haber, movimiento["saldo"],
            ])
        sheet.append(["", "", etiqueta, "Totales del periodo / saldo final", "", bloque["debe"], bloque["haber"], bloque["saldo"]])


def _mayor_desde_estado_resultados(request, cuenta):
    return bool(
        cuenta and cuenta.tipo in ("ingreso", "costo", "gasto")
        and request.GET.get("origen") == "estado_resultados"
    )


@login_required
def mayor_cuenta(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros["cuenta"] = request.GET.get("cuenta") or ""
    filtros["q"] = (request.GET.get("q") or "").strip()
    cuenta = get_object_or_404(CuentaContable, pk=filtros["cuenta"], empresa=empresa) if filtros["cuenta"] else None
    desde_estado_resultados = _mayor_desde_estado_resultados(request, cuenta)
    bloques = bloques_mayor_consulta(
        empresa, filtros, cuenta=cuenta,
        excluir_cierres=desde_estado_resultados,
    )
    bloque = bloques[0] if cuenta and bloques else {"movimientos": [], "debe": Decimal("0.00"), "haber": Decimal("0.00"), "saldo": Decimal("0.00"), "saldo_inicial": Decimal("0.00")}
    # La selección sólo puede apuntar a una línea ya incluida en este mayor.
    movimiento_solicitado = request.GET.get("movimiento") or ""
    movimiento_seleccionado = next((
        movimiento["linea"].pk for movimiento in bloque["movimientos"]
        if str(movimiento["linea"].pk) == movimiento_solicitado
    ), None)
    return _render_reporte(request, "contabilidad/mayor_cuenta.html", {
        "empresa": empresa, "cuenta": cuenta, "movimientos": bloque["movimientos"],
        "cuentas": CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "nombre"),
        "filtros": filtros, "movimiento_seleccionado": movimiento_seleccionado,
        "desde_estado_resultados": desde_estado_resultados,
        "cuentas_mayor": bloques, "modo_general": cuenta is None,
        "resumen": (
            {"debe_total": bloque["debe"], "haber_total": bloque["haber"], "saldo": bloque["saldo"], "saldo_inicial": bloque["saldo_inicial"], "movimientos": len(bloque["movimientos"])}
            if cuenta else resumen_mayor_general(bloques, vista_parcial=bool(filtros["q"]))
        ),
    })


@login_required
def exportar_mayor_cuenta_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros["q"] = (request.GET.get("q") or "").strip()
    cuenta_id = request.GET.get("cuenta") or ""
    cuenta = get_object_or_404(CuentaContable, pk=cuenta_id, empresa=empresa) if cuenta_id else None
    desde_estado_resultados = _mayor_desde_estado_resultados(request, cuenta)
    bloques = bloques_mayor_consulta(
        empresa, filtros, cuenta=cuenta,
        excluir_cierres=desde_estado_resultados,
    )
    workbook, sheet = _preparar_excel("Mayor por Cuenta · HNL" if cuenta else "Libro Mayor General · HNL")
    sheet.append(["Fecha", "Asiento", "Cuenta", "Detalle", "Referencia", "Debe", "Haber", "Saldo natural"])
    _aplicar_encabezado(sheet, 3)
    _filas_excel_mayor(sheet, bloques, filtros["fecha_inicio"])
    for fila in sheet.iter_rows(min_row=4, min_col=6, max_col=8):
        for celda in fila:
            if isinstance(celda.value, Decimal):
                celda.number_format = '#,##0.00;[Red]-#,##0.00'
    if desde_estado_resultados:
        sheet.append(["Consulta desde Estado de Resultados", "Se omiten los asientos de cierre del período. El saldo final incluye la apertura anterior al período."])
    _advertencias_excel_reporte(sheet, empresa, filtros)
    _autoajustar_columnas(sheet)
    nombre = "Mayor_Cuenta" if cuenta else "Libro_Mayor_General"
    return _respuesta_excel(workbook, f"{nombre}_{empresa.slug}.xlsx")


@login_required
def libro_mayor_general(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros["tipo"] = request.GET.get("tipo") or ""
    filtros["q"] = (request.GET.get("q") or "").strip()
    bloques = bloques_mayor_consulta(empresa, filtros)
    return _render_reporte(request, "contabilidad/libro_mayor_general.html", {
        "empresa": empresa, "cuentas_mayor": bloques, "tipos_cuenta": CuentaContable.TIPO_CHOICES, "filtros": filtros,
        "resumen": resumen_mayor_general(bloques, vista_parcial=bool(filtros["q"] or filtros["tipo"])),
    })


@login_required
def exportar_libro_mayor_general_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros["tipo"] = request.GET.get("tipo") or ""
    filtros["q"] = (request.GET.get("q") or "").strip()
    bloques = bloques_mayor_consulta(empresa, filtros)
    workbook, sheet = _preparar_excel("Libro Mayor General · HNL")
    sheet.append(["Fecha", "Asiento", "Cuenta", "Detalle", "Referencia", "Debe", "Haber", "Saldo natural"])
    _aplicar_encabezado(sheet, 3)
    _filas_excel_mayor(sheet, bloques, filtros["fecha_inicio"])
    for fila in sheet.iter_rows(min_row=4, min_col=6, max_col=8):
        for celda in fila:
            if isinstance(celda.value, Decimal):
                celda.number_format = '#,##0.00;[Red]-#,##0.00'
    _advertencias_excel_reporte(sheet, empresa, filtros)
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Libro_Mayor_General_{empresa.slug}.xlsx")


@login_required
def balance_comprobacion(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros.update(tipo=request.GET.get("tipo") or "", incluir_ceros=request.GET.get("incluir_ceros") == "1")
    data = datos_balance_comprobacion(empresa, **filtros)
    data.update(empresa=empresa, filtros=filtros, tipos_cuenta=CuentaContable.TIPO_CHOICES)
    return _render_reporte(request, "contabilidad/balance_comprobacion.html", data)


@login_required
def exportar_balance_comprobacion_excel(request, empresa_slug):
    from copy import copy

    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    filtros.update(tipo=request.GET.get("tipo") or "", incluir_ceros=request.GET.get("incluir_ceros") == "1")
    data = datos_balance_comprobacion(empresa, **filtros)
    workbook, sheet = _preparar_excel("Balance de Comprobación · HNL")
    _advertencias_excel_reporte(sheet, empresa, filtros)
    for fila in sheet.iter_rows(min_col=1, max_col=1):
        if fila[0].value is not None:
            fuente = copy(fila[0].font)
            fuente.bold = True
            fila[0].font = fuente
    sheet.append(["Cuenta", "Tipo", "Apertura deudora", "Apertura acreedora", "Debe del periodo", "Haber del periodo", "Saldo final deudor", "Saldo final acreedor"])
    fila_encabezado = sheet.max_row
    _aplicar_encabezado(sheet, fila_encabezado)
    sheet.freeze_panes = f"A{fila_encabezado + 1}"
    agrupadoras = {
        cuenta.pk for cuenta in ordenar_catalogo(CuentaContable.objects.filter(empresa=empresa))
        if cuenta.tiene_subcuentas_catalogo
    }
    for fila in data["filas"]:
        sheet.append([f"{fila['cuenta'].codigo} - {fila['cuenta'].nombre}", fila["cuenta"].get_tipo_display(), fila["apertura_deudora"], fila["apertura_acreedora"], fila["debe"], fila["haber"], fila["saldo_deudor"], fila["saldo_acreedor"]])
        for celda in sheet[sheet.max_row]:
            fuente = copy(celda.font)
            fuente.bold = fila["cuenta"].pk in agrupadoras
            celda.font = fuente
    resumen = data["resumen"]
    sheet.append(["Totales", "", resumen["apertura_deudora_total"], resumen["apertura_acreedora_total"], resumen["debe_total"], resumen["haber_total"], resumen["saldo_deudor_total"], resumen["saldo_acreedor_total"]])
    for celda in sheet[sheet.max_row]:
        fuente = copy(celda.font)
        fuente.bold = True
        celda.font = fuente
    for fila in sheet.iter_rows(min_row=fila_encabezado + 1, min_col=3, max_col=8):
        for celda in fila:
            celda.number_format = '#,##0.00;[Red]-#,##0.00'
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Balance_Comprobacion_{empresa.slug}.xlsx")


@login_required
def estado_resultados(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    data = _datos_estado_financiero(request, empresa, "resultados")
    return _render_reporte(request, "contabilidad/estado_resultados.html", data)


def _datos_estado_financiero(request, empresa, modo, *, exportacion=False):
    filtros = {"fecha_corte": request.GET.get("fecha_corte") or ""} if modo == "balance" else _filtros_reportes(request)
    formulario = formulario_comparacion(request.GET, filtros)
    comparacion, error = None, ""
    if request.GET.get("comparar") == "1":
        calcular = datos_comparacion_balance if modo == "balance" else datos_comparacion_resultados
        try:
            comparacion = calcular(empresa, **formulario)
        except ErrorComparacionPeriodos as excepcion:
            if exportacion:
                raise
            error = str(excepcion)
            # Un rango mensual inválido nunca se convierte en filtros de fecha
            # para los saldos ni provoca una consulta con valores incompletos.
            filtros = {clave: "" for clave in filtros}
    if comparacion:
        data = comparacion["datos_actuales"].copy()
        filtros = comparacion["filtros"]
    elif modo == "balance":
        data = datos_balance_general(empresa, **filtros)
    else:
        data = datos_estado_resultados(empresa, **filtros)
    data.update(
        empresa=empresa, filtros=filtros, comparacion=comparacion,
        comparacion_form=formulario, comparacion_error=error,
    )
    return data


def _exportar_comparacion_excel(empresa, comparacion, modo):
    titulo = "Balance General" if modo == "balance" else "Estado de Resultados"
    workbook, sheet = _preparar_excel(f"{titulo} · Comparativo HNL")
    sheet["A2"] = f"{comparacion['etiqueta_inicio']} → {comparacion['etiqueta_fin']}"
    sheet.append(["Código", "Cuenta", *[
        columna["etiqueta"] if columna["es_acumulado"] else f"{columna['etiqueta']} {columna['anio']}"
        for columna in comparacion["columnas"]
    ]])
    _aplicar_encabezado(sheet, 3)
    sheet.freeze_panes = "C4"
    for grupo in comparacion["grupos"]:
        sheet.append([grupo["titulo"]])
        sheet.cell(sheet.max_row, 1).font = Font(bold=True)
        for fila in grupo["filas"]:
            cuenta = fila["cuenta"]
            sheet.append([cuenta.codigo if cuenta else "", cuenta.nombre if cuenta else fila["nombre"], *fila["saldos"]])
            sheet.cell(sheet.max_row, 2).alignment = Alignment(indent=min(fila["nivel"], 250))
            for celda in sheet[sheet.max_row]:
                celda.font = Font(bold=bool(fila["es_agrupadora"]))
        sheet.append(["", f"Total {grupo['titulo'].lower()}", *grupo["totales"]])
        for celda in sheet[sheet.max_row]:
            celda.font = Font(bold=True)
        sheet.append([])
    for fila in comparacion["resumen_filas"]:
        sheet.append(["", fila["etiqueta"], *fila["saldos"]])
        for celda in sheet[sheet.max_row]:
            celda.font = Font(bold=True)
    for fila in sheet.iter_rows(min_row=4, min_col=3):
        for celda in fila:
            if isinstance(celda.value, Decimal):
                celda.number_format = '#,##0.00;[Red]-#,##0.00'
    sheet.append([])
    sheet.append([
        "Cada mes muestra su saldo de cierre; los meses no se suman entre sí."
        if modo == "balance" else "Cada mes muestra sus movimientos; Acumulado corresponde al rango completo."
    ])
    sheet.append(["Las cuentas principales acumulan sus subcuentas; los totales cuentan cada movimiento una sola vez."])
    _advertencias_excel_reporte(sheet, empresa, comparacion["filtros"])
    _autoajustar_columnas(sheet)
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 48
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:3"
    sheet.print_options.horizontalCentered = True
    sheet.print_area = sheet.dimensions
    nombre = "Balance_General" if modo == "balance" else "Estado_Resultados"
    return _respuesta_excel(workbook, f"{nombre}_Comparativo_{empresa.slug}.xlsx")


def _agregar_seccion_estado_financiero_excel(sheet, titulo, filas):
    """Presenta la jerarquía ya calculada; no vuelve a sumar sus saldos."""
    sheet.append([titulo])
    sheet.cell(sheet.max_row, 1).font = Font(bold=True)
    for fila in filas:
        cuenta = fila["cuenta"]
        sheet.append([cuenta.codigo, cuenta.nombre, fila["saldo"]])
        sheet.cell(sheet.max_row, 2).alignment = Alignment(indent=min(fila["nivel"], 250))
        for celda in sheet[sheet.max_row]:
            celda.font = Font(bold=bool(fila["es_agrupadora"]))


@login_required
def exportar_estado_resultados_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    try:
        data = _datos_estado_financiero(request, empresa, "resultados", exportacion=True)
    except ErrorComparacionPeriodos as error:
        return HttpResponseBadRequest(str(error))
    if data["comparacion"]:
        return _exportar_comparacion_excel(empresa, data["comparacion"], "resultados")
    workbook, sheet = _preparar_excel("Estado de Resultados · HNL")
    sheet.append(["Código", "Cuenta", "Saldo"])
    _aplicar_encabezado(sheet, 3)
    for grupo, titulo in (("ingresos", "Ingresos"), ("costos", "Costos"), ("gastos", "Gastos")):
        _agregar_seccion_estado_financiero_excel(sheet, titulo, data[f"{grupo}_jerarquia"])
    sheet.append([])
    for etiqueta, clave in (("Total ingresos", "total_ingresos"), ("Total costos", "total_costos"), ("Utilidad bruta", "utilidad_bruta"), ("Total gastos", "total_gastos"), ("Utilidad neta", "utilidad")):
        sheet.append([etiqueta, "", data["resumen"][clave]])
    for celda in sheet["C"]:
        if isinstance(celda.value, Decimal):
            celda.number_format = '#,##0.00;[Red]-#,##0.00'
    _advertencias_excel_reporte(sheet, empresa, _filtros_reportes(request))
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Estado_Resultados_{empresa.slug}.xlsx")


@login_required
def balance_general(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    data = _datos_estado_financiero(request, empresa, "balance")
    return _render_reporte(request, "contabilidad/balance_general.html", data)


@login_required
def exportar_balance_general_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    try:
        data = _datos_estado_financiero(request, empresa, "balance", exportacion=True)
    except ErrorComparacionPeriodos as error:
        return HttpResponseBadRequest(str(error))
    if data["comparacion"]:
        return _exportar_comparacion_excel(empresa, data["comparacion"], "balance")
    workbook, sheet = _preparar_excel("Balance General · HNL")
    sheet.append(["Código", "Cuenta", "Saldo"])
    _aplicar_encabezado(sheet, 3)
    for grupo, titulo in (("activos", "Activos"), ("pasivos", "Pasivos"), ("patrimonio", "Patrimonio")):
        _agregar_seccion_estado_financiero_excel(sheet, titulo, data[f"{grupo}_jerarquia"])
    resumen = data["resumen"]
    sheet.append(["", "Resultado acumulado no traspasado a patrimonio", resumen["resultado_no_traspasado"]])
    sheet.append([])
    for etiqueta, clave in (("Total activos", "total_activos"), ("Total pasivos", "total_pasivos"), ("Patrimonio contabilizado", "patrimonio_contabilizado"), ("Resultado no traspasado", "resultado_no_traspasado"), ("Total patrimonio", "total_patrimonio"), ("Pasivo + patrimonio", "total_pasivo_patrimonio"), ("Diferencia", "diferencia")):
        sheet.append([etiqueta, "", resumen[clave]])
    for celda in sheet["C"]:
        if isinstance(celda.value, Decimal):
            celda.number_format = '#,##0.00;[Red]-#,##0.00'
    sheet.append(["Las cuentas principales acumulan sus subcuentas; los totales generales cuentan cada movimiento una sola vez."])
    _advertencias_excel_reporte(sheet, empresa, {"fecha_corte": request.GET.get("fecha_corte") or ""})
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Balance_General_{empresa.slug}.xlsx")


@login_required
def estado_flujo_efectivo(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    data = datos_flujo_efectivo(empresa, **filtros)
    data.update(empresa=empresa, filtros=filtros)
    return _render_reporte(request, "contabilidad/estado_flujo_efectivo.html", data)


@login_required
def exportar_estado_flujo_efectivo_excel(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    data = datos_flujo_efectivo(empresa, **_filtros_reportes(request))
    workbook, sheet = _preparar_excel("Flujo de Efectivo · HNL")
    sheet.append(["Actividad", "Fecha", "Asiento", "Cuenta de efectivo", "Detalle", "Entrada", "Salida", "Neto"])
    _aplicar_encabezado(sheet, 3)
    etiquetas = {"operacion": "Actividades de operación", "inversion": "Actividades de inversión", "financiamiento": "Actividades de financiamiento"}
    for clave, grupo in data["flujo"].items():
        for movimiento in grupo["movimientos"]:
            sheet.append([etiquetas[clave], movimiento["asiento"].fecha, movimiento["asiento"].numero or "Sin número", f"{movimiento['cuenta'].codigo} - {movimiento['cuenta'].nombre}", movimiento["detalle"], movimiento["entrada"], movimiento["salida"], movimiento["neto"]])
        sheet.append([f"Neto {etiquetas[clave].lower()}", "", "", "", "", grupo["entradas"], grupo["salidas"], grupo["neto"]])
    sheet.append([])
    for ajuste in data["ajustes_historicos"]:
        sheet.append(["Ajuste histórico de saldo", ajuste["asiento"].fecha, ajuste["asiento"].numero, "", "Corrección documentada; no es un cobro ni un pago nuevo", "", "", ajuste["neto"]])
    for etiqueta, clave in (("Efectivo al inicio", "saldo_inicial"), ("Variación neta de efectivo", "total_neto"), ("Ajustes históricos del saldo", "ajustes_historicos"), ("Efectivo al cierre", "saldo_final"), ("Diferencia por revisar", "diferencia")):
        sheet.append([etiqueta, "", "", "", "", "", "", data["resumen"][clave]])
    if data["resumen"]["cuentas_financieras_incompatibles"]:
        sheet.append(["Cuentas financieras por revisar", data["resumen"]["cuentas_financieras_incompatibles"], "Su tipo financiero y cuenta contable no coinciden; se conservan los saldos de bancos configurados."])
    _advertencias_excel_reporte(sheet, empresa, _filtros_reportes(request))
    _autoajustar_columnas(sheet)
    return _respuesta_excel(workbook, f"Flujo_Efectivo_{empresa.slug}.xlsx")


@login_required
def resumen_ejecutivo_contable(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    filtros = _filtros_reportes(request)
    resultados = datos_estado_resultados(empresa, **filtros)
    balance = datos_balance_general(empresa, filtros["fecha_fin"])
    flujo = datos_flujo_efectivo(empresa, **filtros)
    resumen = resultados["resumen"].copy()
    resumen.update(balance["resumen"])
    total_ingresos, utilidad = resumen["total_ingresos"], resumen["utilidad"]
    asientos = AsientoContable.objects.filter(empresa=empresa, estado="contabilizado")
    if filtros["fecha_inicio"]:
        asientos = asientos.filter(fecha__gte=filtros["fecha_inicio"])
    if filtros["fecha_fin"]:
        asientos = asientos.filter(fecha__lte=filtros["fecha_fin"])
    resumen.update(
        total_egresos_resultado=resumen["total_costos"] + resumen["total_gastos"],
        margen_neto=(utilidad / total_ingresos * Decimal("100.00")) if total_ingresos else Decimal("0.00"),
        diferencia_balance=resumen["diferencia"], flujo_neto=flujo["resumen"]["total_neto"],
        flujo_operacion=flujo["resumen"]["operacion"], efectivo_inicial=flujo["resumen"]["saldo_inicial"],
        efectivo_final=flujo["resumen"]["saldo_final"], diferencia_flujo=flujo["resumen"]["diferencia"],
        ajustes_historicos_efectivo=flujo["resumen"]["ajustes_historicos"],
        asientos_contabilizados=asientos.count(),
        asientos_pendientes=AsientoContable.objects.filter(empresa=empresa, estado="borrador").count(),
        cuentas_efectivo=flujo["cuentas_efectivo"].count(),
    )
    filas_resultados = resultados["ingresos"] + resultados["costos"] + resultados["gastos"]
    filas_balance = balance["activos"] + balance["pasivos"] + balance["patrimonio"]
    return _render_reporte(request, "contabilidad/resumen_ejecutivo_contable.html", {
        "empresa": empresa, "filtros": filtros, "resumen": resumen,
        "top_resultados": sorted(filas_resultados, key=lambda fila: abs(fila["saldo"]), reverse=True)[:8],
        "top_balance": sorted(filas_balance, key=lambda fila: abs(fila["saldo"]), reverse=True)[:8],
    })
