from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.forms import modelform_factory, inlineformset_factory
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum
from weasyprint import HTML
from datetime import datetime, date
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.chart import BarChart, Reference
import os

from core.models import Empresa, Usuario
from .models import Factura, LineaFactura, Producto, TipoImpuesto, Cliente, PagoFactura


# =====================================================
# DASHBOARD
# =====================================================

@login_required
def facturacion_dashboard(request, empresa_slug):
    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    facturas = Factura.objects.filter(empresa=empresa).order_by('-fecha_creacion')

    return render(request, "facturacion/dashboard.html", {
        "empresa": empresa,
        "facturas": facturas
    })


# =====================================================
# CREAR FACTURA
# =====================================================

@login_required
def crear_factura(request, empresa_slug):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)

    FacturaForm = modelform_factory(
        Factura,
        fields=[
            'cliente',
            'fecha_emision',
            'fecha_vencimiento',
            'vendedor',
            'tipo_cambio',
            'moneda',
            'estado'
        ]
    )

    LineaFormSet = inlineformset_factory(
        Factura,
        LineaFactura,
        fields=['producto', 'cantidad', 'precio_unitario', 'comentario', 'impuesto'],
        extra=1,
        can_delete=True
    )

    if request.method == "POST":

        form = FacturaForm(request.POST)
        form.fields['cliente'].queryset = Cliente.objects.filter(empresa=empresa)
        form.fields['vendedor'].queryset = Usuario.objects.filter(empresa=empresa)

        formset = LineaFormSet(request.POST)
        for f in formset:
            f.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True
            )

        if form.is_valid() and formset.is_valid():

            factura = form.save(commit=False)
            factura.empresa = empresa
            factura.save()

            formset.instance = factura
            lineas = formset.save(commit=False)

            for linea in lineas:
                linea.factura = factura
                linea.save()

            for obj in formset.deleted_objects:
                obj.delete()

            factura.calcular_totales()
            factura.save(update_fields=[
                'subtotal',
                'impuesto',
                'total',
                'total_lempiras'
            ])

            return redirect("facturacion_dashboard", empresa_slug=empresa.slug)

    else:
        form = FacturaForm()
        form.fields['cliente'].queryset = Cliente.objects.filter(empresa=empresa)
        form.fields['vendedor'].queryset = Usuario.objects.filter(empresa=empresa)

        formset = LineaFormSet()
        for f in formset:
            f.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True
            )

    productos = Producto.objects.filter(empresa=empresa, activo=True)
    impuestos = TipoImpuesto.objects.filter(activo=True)

    return render(request, "facturacion/crear_factura.html", {
        "empresa": empresa,
        "form": form,
        "formset": formset,
        "productos": productos,
        "impuestos": impuestos,
    })


# =====================================================
# EDITAR FACTURA
# =====================================================

@login_required
def editar_factura(request, empresa_slug, factura_id):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)

    FacturaForm = modelform_factory(
        Factura,
        fields=[
            'cliente',
            'fecha_emision',
            'fecha_vencimiento',
            'vendedor',
            'tipo_cambio',
            'moneda',
            'estado'
        ]
    )

    LineaFormSet = inlineformset_factory(
        Factura,
        LineaFactura,
        fields=['producto', 'cantidad', 'precio_unitario', 'comentario', 'impuesto'],
        extra=0,
        can_delete=True
    )

    if request.method == "POST":

        form = FacturaForm(request.POST, instance=factura)
        form.fields['cliente'].queryset = Cliente.objects.filter(empresa=empresa)
        form.fields['vendedor'].queryset = Usuario.objects.filter(empresa=empresa)

        formset = LineaFormSet(request.POST, instance=factura)

        for f in formset:
            f.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True
            )

        if form.is_valid() and formset.is_valid():

            factura = form.save()

            lineas = formset.save(commit=False)

            for linea in lineas:
                linea.factura = factura
                linea.save()

            for obj in formset.deleted_objects:
                obj.delete()

            factura.calcular_totales()
            factura.save(update_fields=[
                'subtotal',
                'impuesto',
                'total',
                'total_lempiras'
            ])

            return redirect("facturacion_dashboard", empresa_slug=empresa.slug)

    else:
        form = FacturaForm(instance=factura)
        form.fields['cliente'].queryset = Cliente.objects.filter(empresa=empresa)
        form.fields['vendedor'].queryset = Usuario.objects.filter(empresa=empresa)

        formset = LineaFormSet(instance=factura)

        for f in formset:
            f.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True
            )

    productos = Producto.objects.filter(empresa=empresa, activo=True)
    impuestos = TipoImpuesto.objects.filter(activo=True)

    return render(request, "facturacion/crear_factura.html", {
        "empresa": empresa,
        "form": form,
        "formset": formset,
        "productos": productos,
        "impuestos": impuestos,
    })


# =====================================================
# REGISTRAR PAGO
# =====================================================

@login_required
def registrar_pago(request, empresa_slug, factura_id):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)

    if request.method == "POST":

        monto = request.POST.get("monto", "").strip()
        metodo = request.POST.get("metodo")
        referencia = request.POST.get("referencia", "").strip()
        fecha_pago = request.POST.get("fecha")

        if monto:
            try:
                monto_decimal = Decimal(monto)

                if monto_decimal > 0:
                    fecha_convertida = datetime.strptime(fecha_pago, "%Y-%m-%d").date() if fecha_pago else timezone.now().date()

                    PagoFactura.objects.create(
                        factura=factura,
                        monto=monto_decimal,
                        metodo=metodo,
                        referencia=referencia,
                        fecha=fecha_convertida
                    )
            except:
                pass

        return redirect("ver_factura", empresa_slug=empresa.slug, factura_id=factura.id)

    return render(request, "facturacion/registrar_pago.html", {
        "factura": factura,
        "empresa": empresa,
        "today": timezone.now().date()
    })


# =====================================================
# VER FACTURA
# =====================================================

@login_required
def ver_factura(request, empresa_slug, factura_id):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)

    resumen = factura.resumen_fiscal()

    return render(request, "facturacion/ver_factura.html", {
        "empresa": empresa,
        "factura": factura,
        "resumen": resumen,
    })


# =====================================================
# PDF
# =====================================================

@login_required
def descargar_factura_pdf(request, empresa_slug, factura_id):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    factura = get_object_or_404(Factura, id=factura_id, empresa=empresa)

    resumen = factura.resumen_fiscal()

    logo_url = None

    if empresa.logo:
        logo_path = os.path.join(settings.MEDIA_ROOT, empresa.logo.name)
        logo_path = logo_path.replace("\\", "/")
        logo_url = "file:///" + logo_path

    html_string = render_to_string(
        "facturacion/factura_pdf.html",
        {
            "empresa": empresa,
            "factura": factura,
            "resumen": resumen,
            "logo_url": logo_url,
        }
    )

    html = HTML(
        string=html_string,
        base_url=str(settings.BASE_DIR)
    )

    pdf_file = html.write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Factura_{factura.numero_factura}.pdf"'

    return response


# =====================================================
# REPORTES
# =====================================================

@login_required
def reportes_facturacion(request, empresa_slug):
    empresa = get_object_or_404(Empresa, slug=empresa_slug)

    facturas = Factura.objects.filter(empresa=empresa)

    cliente_id = request.GET.get("cliente")
    estado_pago = request.GET.get("estado_pago")
    fecha_desde = request.GET.get("fecha_desde")
    fecha_hasta = request.GET.get("fecha_hasta")
    impuesto = request.GET.get("impuesto")

    if cliente_id:
        facturas = facturas.filter(cliente_id=cliente_id)

    if estado_pago:
        facturas = facturas.filter(estado_pago=estado_pago)

    if fecha_desde:
        facturas = facturas.filter(fecha_emision__gte=fecha_desde)

    if fecha_hasta:
        facturas = facturas.filter(fecha_emision__lte=fecha_hasta)

    if impuesto:
        facturas = facturas.filter(lineas__impuesto__porcentaje=impuesto).distinct()

    totales = facturas.aggregate(
        subtotal=Sum('subtotal'),
        impuesto=Sum('impuesto'),
        total=Sum('total')
    )

    total_saldo = sum((f.saldo_pendiente for f in facturas), Decimal('0.00'))

    total_base_15 = Decimal('0.00')
    total_isv_15 = Decimal('0.00')
    total_base_18 = Decimal('0.00')
    total_isv_18 = Decimal('0.00')
    total_exento = Decimal('0.00')
    total_exonerado = Decimal('0.00')

    for f in facturas:
        r = f.resumen_fiscal()
        total_base_15 += Decimal(str(r["base_15"]))
        total_isv_15 += Decimal(str(r["isv_15"]))
        total_base_18 += Decimal(str(r["base_18"]))
        total_isv_18 += Decimal(str(r["isv_18"]))
        total_exento += Decimal(str(r["base_exento"]))
        total_exonerado += Decimal(str(r["base_exonerado"]))

    clientes = Cliente.objects.filter(empresa=empresa)

    return render(request, "facturacion/reportes_facturacion.html", {
        "empresa": empresa,
        "clientes": clientes,
        "facturas": facturas,
        "totales": totales,
        "total_saldo": total_saldo,
        "total_base_15": total_base_15,
        "total_isv_15": total_isv_15,
        "total_base_18": total_base_18,
        "total_isv_18": total_isv_18,
        "total_exento": total_exento,
        "total_exonerado": total_exonerado,
    })


# =====================================================
# REPORTES CXC
# =====================================================

@login_required
def reporte_cxc(request, empresa_slug):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)

    facturas = Factura.objects.filter(
        empresa=empresa,
        estado='emitida'
    )

    hoy = date.today()

    data = {}

    for f in facturas:

        saldo = f.saldo_pendiente

        if saldo <= 0:
            continue

        cliente = f.cliente.nombre

        dias = (hoy - f.fecha_emision).days

        if cliente not in data:
            data[cliente] = {
                "0_30": 0,
                "31_60": 0,
                "61_90": 0,
                "90_mas": 0,
                "total": 0
            }

        if dias <= 30:
            data[cliente]["0_30"] += saldo
        elif dias <= 60:
            data[cliente]["31_60"] += saldo
        elif dias <= 90:
            data[cliente]["61_90"] += saldo
        else:
            data[cliente]["90_mas"] += saldo

        data[cliente]["total"] += saldo

    return render(request, "facturacion/reporte_cxc.html", {
        "empresa": empresa,
        "data": data
    })


# =====================================================
# EXPORTAR EXCEL
# =====================================================

@login_required
def exportar_excel_reportes(request, empresa_slug):

    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    facturas = Factura.objects.filter(empresa=empresa)

    wb = Workbook()

    ws_resumen = wb.active
    ws_resumen.title = "Resumen Ejecutivo"

    total_facturado = sum((f.total for f in facturas), Decimal('0.00'))
    total_saldo = sum((f.saldo_pendiente for f in facturas), Decimal('0.00'))
    total_cobrado = total_facturado - total_saldo
    porcentaje = (total_cobrado / total_facturado * 100) if total_facturado > 0 else 0

    ws_resumen["A1"] = f"EMPRESA: {empresa.nombre}"
    ws_resumen["A3"] = "INDICADORES CLAVE"

    data = [
        ("Total Facturado", total_facturado),
        ("Total Cobrado", total_cobrado),
        ("Total Pendiente", total_saldo),
        ("% Recuperación", round(porcentaje, 2)),
    ]

    row = 5
    for label, value in data:
        ws_resumen.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws_resumen.cell(row=row, column=2, value=float(value))
        row += 1

    ws_detalle = wb.create_sheet("Detalle Facturas")

    headers = [
        "Cliente", "Fecha", "Número",
        "Base 15%", "ISV 15%",
        "Base 18%", "ISV 18%",
        "Exento", "Exonerado",
        "Total", "Saldo", "Estado"
    ]

    for col, h in enumerate(headers, 1):
        cell = ws_detalle.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1e2a38", fill_type="solid")

    row = 2

    for f in facturas:
        r = f.resumen_fiscal()

        ws_detalle.cell(row=row, column=1, value=f.cliente.nombre)
        ws_detalle.cell(row=row, column=2, value=str(f.fecha_emision))
        ws_detalle.cell(row=row, column=3, value=f.numero_factura)

        ws_detalle.cell(row=row, column=4, value=float(r["base_15"]))
        ws_detalle.cell(row=row, column=5, value=float(r["isv_15"]))
        ws_detalle.cell(row=row, column=6, value=float(r["base_18"]))
        ws_detalle.cell(row=row, column=7, value=float(r["isv_18"]))
        ws_detalle.cell(row=row, column=8, value=float(r["base_exento"]))
        ws_detalle.cell(row=row, column=9, value=float(r["base_exonerado"]))
        ws_detalle.cell(row=row, column=10, value=float(f.total))
        ws_detalle.cell(row=row, column=11, value=float(f.saldo_pendiente))
        ws_detalle.cell(row=row, column=12, value=f.estado_pago)

        row += 1

    ws_cliente = wb.create_sheet("Clientes")
    ws_cliente.append(["Cliente", "Total", "Saldo"])

    data_clientes = {}

    for f in facturas:
        c = f.cliente.nombre
        if c not in data_clientes:
            data_clientes[c] = {"total": 0, "saldo": 0}

        data_clientes[c]["total"] += f.total
        data_clientes[c]["saldo"] += f.saldo_pendiente

    for c, v in data_clientes.items():
        ws_cliente.append([c, float(v["total"]), float(v["saldo"])])

    ws_cxc = wb.create_sheet("Cuentas por Cobrar")
    ws_cxc.append(["Cliente", "0-30", "31-60", "61-90", "90+", "Total"])

    hoy = date.today()
    aging = {}

    for f in facturas:
        saldo = f.saldo_pendiente
        if saldo <= 0:
            continue

        cliente = f.cliente.nombre
        dias = (hoy - f.fecha_emision).days

        if cliente not in aging:
            aging[cliente] = {"0": 0, "30": 0, "60": 0, "90": 0, "total": 0}

        if dias <= 30:
            aging[cliente]["0"] += saldo
        elif dias <= 60:
            aging[cliente]["30"] += saldo
        elif dias <= 90:
            aging[cliente]["60"] += saldo
        else:
            aging[cliente]["90"] += saldo

        aging[cliente]["total"] += saldo

    for c, v in aging.items():
        ws_cxc.append([
            c,
            float(v["0"]),
            float(v["30"]),
            float(v["60"]),
            float(v["90"]),
            float(v["total"])
        ])

    ws_chart = wb.create_sheet("Gráficos")
    ws_chart["A1"] = "Facturación por Cliente"

    chart = BarChart()
    data_ref = Reference(ws_cliente, min_col=2, min_row=1, max_row=len(data_clientes) + 1)
    cats = Reference(ws_cliente, min_col=1, min_row=2, max_row=len(data_clientes) + 1)

    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats)

    ws_chart.add_chart(chart, "A3")

    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_length = 0
            col_letter = col[0].column_letter

            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass

            sheet.column_dimensions[col_letter].width = max_length + 3

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=Reporte_Financiero.xlsx"

    wb.save(response)
    return response