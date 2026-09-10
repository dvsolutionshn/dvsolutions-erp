from collections import defaultdict
from decimal import Decimal

from django import forms
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, Factura


class ReportFiltersForm(forms.Form):
    q = forms.CharField(label="Buscar", required=False, max_length=120,
                        widget=forms.TextInput(attrs={"placeholder": "Cliente, RTN o número de factura", "type": "search"}))
    cliente = forms.ModelChoiceField(label="Cliente", queryset=Cliente.objects.none(), required=False,
                                     empty_label="Todos los clientes")
    estado_pago = forms.ChoiceField(label="Estado de pago", required=False,
                                   choices=[("", "Todos los estados"), *Factura.ESTADO_PAGO])
    impuesto = forms.ChoiceField(label="Impuesto", required=False,
                                choices=[("", "Todos los impuestos"), ("15", "ISV 15%"), ("18", "ISV 18%"), ("0", "0% · Exento / exonerado")])
    fecha_desde = forms.DateField(label="Desde", required=False, input_formats=["%Y-%m-%d"],
                                 widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    fecha_hasta = forms.DateField(label="Hasta", required=False, input_formats=["%Y-%m-%d"],
                                 widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))

    def __init__(self, empresa, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa).order_by("nombre", "pk")

    def clean(self):
        data = super().clean()
        start, end = data.get("fecha_desde"), data.get("fecha_hasta")
        if start and end and start > end:
            self.add_error("fecha_hasta", "La fecha final no puede ser anterior a la inicial.")
        return data


def filter_invoices(empresa, params, facturas):
    form = ReportFiltersForm(empresa, data=params)
    # An invalid filter must never silently broaden a report or an export.
    if not form.is_valid():
        return facturas.none()
    data = form.cleaned_data
    if data["cliente"]:
        facturas = facturas.filter(cliente=data["cliente"])
    if data["estado_pago"]:
        facturas = facturas.filter(estado_pago=data["estado_pago"])
    if data["fecha_desde"]:
        facturas = facturas.filter(fecha_emision__gte=data["fecha_desde"])
    if data["fecha_hasta"]:
        facturas = facturas.filter(fecha_emision__lte=data["fecha_hasta"])
    if data["impuesto"]:
        facturas = facturas.filter(lineas__impuesto__porcentaje=data["impuesto"]).distinct()
    if data["q"]:
        facturas = facturas.filter(Q(numero_factura__icontains=data["q"]) |
                                   Q(cliente__nombre__icontains=data["q"]) |
                                   Q(cliente__rtn__icontains=data["q"]))
    return facturas.prefetch_related("lineas__producto", "pagos_facturacion").order_by("-fecha_emision", "-pk")


def report_context(request, empresa, facturas, totales, total_saldo, bi):
    from core.demo_dashboard import dashboard_context

    form = ReportFiltersForm(empresa, data=request.GET)
    valid = form.is_valid()
    query = request.GET.copy()
    for key in ("pagina", "vista", "orden", "reporte"):
        query.pop(key, None)

    def query_url(**updates):
        params = query.copy()
        params.update(updates)
        return "?" + params.urlencode()

    active_view = request.GET.get("vista", "resumen")
    if active_view not in {"resumen", "documentos", "fiscal", "externo"}:
        active_view = "resumen"
    order = request.GET.get("orden", "recientes")
    orders = {
        "recientes": (lambda f: (f.fecha_emision, f.pk), True),
        "antiguos": (lambda f: (f.fecha_emision, f.pk), False),
        "mayor_total": (lambda f: (f.reporte_total_hnl, f.pk), True),
        "mayor_saldo": (lambda f: (f.reporte_saldo_hnl, f.pk), True),
    }
    if order not in orders:
        order = "recientes"
    key, descending = orders[order]
    page = Paginator(sorted(facturas, key=key, reverse=descending), 25).get_page(request.GET.get("pagina"))
    pagination = [{"label": number, "url": query_url(vista="documentos", orden=order, pagina=number),
                   "current": number == page.number} for number in page.paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1)]

    # Chart the selected historical period, including dates outside the old BI window.
    monthly = defaultdict(lambda: {"total": Decimal("0.00"), "documentos": 0})
    for invoice in facturas:
        if invoice.estado != "anulada":
            bucket = monthly[invoice.fecha_emision.replace(day=1)]
            bucket["total"] += invoice.reporte_total_hnl
            bucket["documentos"] += 1
    peak = max((item["total"] for item in monthly.values()), default=Decimal("0.00"))
    trend = [{"periodo": month, **data, "height": round(data["total"] / peak * 150) if peak else 0}
             for month, data in sorted(monthly.items())]
    states = [dict(item) for item in bi["estado_cobro"]]
    count = sum(item["cantidad"] for item in states)
    for item in states:
        item["porcentaje"] = round(item["cantidad"] / count * 100, 1) if count else 0

    chips = []
    if valid:
        for name, value in form.cleaned_data.items():
            if value:
                clean_query = query.copy()
                clean_query.pop(name, None)
                clean_query["vista"] = active_view
                clean_query["orden"] = order
                label = dict(form.fields[name].choices).get(value, value) if name in {"estado_pago", "impuesto"} else value
                if name in {"fecha_desde", "fecha_hasta"}:
                    label = value.strftime("%d/%m/%Y")
                chips.append({"label": f"{form.fields[name].label}: {label}", "url": "?" + clean_query.urlencode()})

    paid = totales["total"] - total_saldo
    recovery = paid / totales["total"] * 100 if totales["total"] else Decimal("0")
    export_query = query.urlencode()
    return {
        **dashboard_context(request, empresa, empresa.modulos_habilitados()),
        "demo_section": "reportes", "report_form": form, "report_valid": valid,
        "report_today": timezone.localdate(), "report_view": active_view,
        "report_tabs": [{"key": value, "title": title, "icon": icon, "url": query_url(vista=value, orden=order)}
                        for value, title, icon in [("resumen", "Resumen", "chart-no-axes-combined"),
                                                   ("documentos", "Documentos", "files"), ("fiscal", "Composición fiscal", "landmark")]],
        "report_external_url": query_url(vista="externo"),
        "report_page": page, "report_pagination": pagination, "report_order": order,
        "report_previous": query_url(vista="documentos", orden=order, pagina=page.previous_page_number()) if page.has_previous() else "",
        "report_next": query_url(vista="documentos", orden=order, pagina=page.next_page_number()) if page.has_next() else "",
        "report_trend": trend, "report_states": states, "report_chips": chips,
        "report_paid": paid, "report_recovery": recovery,
        "report_excel_url": reverse("exportar_excel", args=[empresa.slug]) + "?" + export_query,
        "report_zip_url": reverse("descargar_facturas_filtradas_zip", args=[empresa.slug]) + "?" + export_query,
        "report_can_zip": valid and bool(form.cleaned_data.get("cliente")) and bool(facturas),
        "report_query": export_query,
    }
