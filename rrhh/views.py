from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from weasyprint import HTML

from core.models import Empresa
from crm.models import ConfiguracionCRM
from crm.services import WhatsAppAPIError, enviar_mensaje_whatsapp_texto

from .forms import (
    ConfiguracionRRHHEmpresaForm,
    DetallePlanillaForm,
    EmpleadoForm,
    MovimientoPlanillaForm,
    PeriodoPlanillaForm,
    VacacionEmpleadoForm,
)
from .models import DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado
from .services import configuracion_rrhh, generar_planilla, recalcular_detalle_planilla


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_crm(empresa):
    return ConfiguracionCRM.objects.get_or_create(empresa=empresa)[0]


@login_required
def rrhh_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    empleados = Empleado.objects.filter(empresa=empresa)
    periodos = PeriodoPlanilla.objects.filter(empresa=empresa)
    config_crm = _configuracion_crm(empresa)
    return render(request, "rrhh/dashboard_rrhh.html", {
        "empresa": empresa,
        "empleados_recientes": empleados.order_by("-fecha_creacion")[:8],
        "periodos_recientes": periodos[:8],
        "config_crm": config_crm,
        "resumen": {
            "empleados": empleados.count(),
            "activos": empleados.filter(estado="activo").count(),
            "periodos": periodos.count(),
            "planillas_abiertas": periodos.exclude(estado__in=["cerrada", "pagada"]).count(),
        },
    })


@login_required
def configuracion_rrhh_view(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    config = configuracion_rrhh(empresa)
    form = ConfiguracionRRHHEmpresaForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuracion de RRHH actualizada correctamente.")
        return redirect("rrhh_dashboard", empresa_slug=empresa.slug)
    return render(request, "rrhh/form_rrhh.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Configuracion de Planilla",
        "form_kind": "configuracion_rrhh",
    })


@login_required
def empleados_rrhh(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    q = request.GET.get("q", "").strip()
    empleados = Empleado.objects.filter(empresa=empresa).order_by("nombres", "apellidos")
    if q:
        empleados = empleados.filter(nombres__icontains=q) | empleados.filter(apellidos__icontains=q) | empleados.filter(identidad__icontains=q)
    return render(request, "rrhh/empleados.html", {"empresa": empresa, "empleados": empleados, "q": q})


@login_required
def crear_empleado(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = EmpleadoForm(request.POST or None, request.FILES or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        empleado = form.save(commit=False)
        empleado.empresa = empresa
        empleado.save()
        messages.success(request, f"Empleado {empleado.nombre_completo} creado correctamente.")
        return redirect("empleados_rrhh", empresa_slug=empresa.slug)
    return render(request, "rrhh/form_rrhh.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nuevo Empleado",
        "form_kind": "empleado",
    })


@login_required
def editar_empleado(request, empresa_slug, empleado_id):
    empresa = _empresa_desde_slug(empresa_slug)
    empleado = get_object_or_404(Empleado, id=empleado_id, empresa=empresa)
    form = EmpleadoForm(request.POST or None, request.FILES or None, instance=empleado, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Empleado {empleado.nombre_completo} actualizado correctamente.")
        return redirect("ver_empleado", empresa_slug=empresa.slug, empleado_id=empleado.id)
    return render(request, "rrhh/form_rrhh.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Editar {empleado.nombre_completo}",
        "form_kind": "empleado",
        "empleado": empleado,
    })


@login_required
def ver_empleado(request, empresa_slug, empleado_id):
    empresa = _empresa_desde_slug(empresa_slug)
    empleado = get_object_or_404(Empleado, id=empleado_id, empresa=empresa)
    return render(request, "rrhh/ver_empleado.html", {"empresa": empresa, "empleado": empleado})


@login_required
def planillas_rrhh(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    periodos = PeriodoPlanilla.objects.filter(empresa=empresa)
    return render(request, "rrhh/planillas.html", {
        "empresa": empresa,
        "periodos": periodos,
        "resumen_planillas": {
            "total": periodos.count(),
            "abiertas": periodos.exclude(estado__in=["cerrada", "pagada"]).count(),
        },
    })


@login_required
def crear_periodo_planilla(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = PeriodoPlanillaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        periodo = form.save(commit=False)
        periodo.empresa = empresa
        periodo.creado_por = request.user
        periodo.save()
        messages.success(request, "Periodo de planilla creado correctamente.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=periodo.id)
    return render(request, "rrhh/periodo_planilla_form.html", {"empresa": empresa, "form": form, "titulo": "Nuevo Periodo de Planilla"})


@login_required
def ver_planilla(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    periodo = get_object_or_404(PeriodoPlanilla, id=periodo_id, empresa=empresa)
    config_crm = _configuracion_crm(empresa)
    return render(request, "rrhh/ver_planilla.html", {
        "empresa": empresa,
        "periodo": periodo,
        "detalles": periodo.detalles.select_related("empleado"),
        "config_crm": config_crm,
    })


@login_required
@require_POST
def generar_planilla_view(request, empresa_slug, periodo_id):
    empresa = _empresa_desde_slug(empresa_slug)
    periodo = get_object_or_404(PeriodoPlanilla, id=periodo_id, empresa=empresa)
    creados = generar_planilla(periodo)
    messages.success(request, f"Planilla calculada correctamente para {creados} empleado(s).")
    return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=periodo.id)


@login_required
def editar_detalle_planilla(request, empresa_slug, detalle_id):
    empresa = _empresa_desde_slug(empresa_slug)
    detalle = get_object_or_404(
        DetallePlanilla.objects.select_related("periodo", "empleado"),
        id=detalle_id,
        periodo__empresa=empresa,
    )
    if detalle.periodo.estado in ["cerrada", "pagada"]:
        messages.error(request, "Esta planilla ya esta cerrada o pagada. No se pueden editar sus valores.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)

    form = DetallePlanillaForm(request.POST or None, instance=detalle)
    if request.method == "POST" and form.is_valid():
        detalle = form.save(commit=False)
        recalcular_detalle_planilla(detalle)
        detalle.save()
        messages.success(request, f"Detalle de {detalle.empleado.nombre_completo} actualizado correctamente.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)

    return render(
        request,
        "rrhh/detalle_planilla_form.html",
        {"empresa": empresa, "periodo": detalle.periodo, "detalle": detalle, "form": form},
    )


@login_required
def movimientos_planilla(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = MovimientoPlanillaForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Movimiento de planilla registrado correctamente.")
        return redirect("movimientos_planilla", empresa_slug=empresa.slug)
    movimientos = MovimientoPlanilla.objects.filter(empleado__empresa=empresa).select_related("empleado", "periodo")
    return render(request, "rrhh/movimientos_planilla.html", {"empresa": empresa, "form": form, "movimientos": movimientos})


@login_required
def vacaciones_rrhh(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = VacacionEmpleadoForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        vacacion = form.save(commit=False)
        if vacacion.estado == "aprobada":
            vacacion.aprobado_por = request.user
        vacacion.save()
        messages.success(request, "Registro de vacaciones guardado correctamente.")
        return redirect("vacaciones_rrhh", empresa_slug=empresa.slug)
    vacaciones = VacacionEmpleado.objects.filter(empleado__empresa=empresa).select_related("empleado")
    return render(request, "rrhh/vacaciones.html", {"empresa": empresa, "form": form, "vacaciones": vacaciones})


@login_required
def voucher_planilla_pdf(request, empresa_slug, detalle_id):
    empresa = _empresa_desde_slug(empresa_slug)
    detalle = get_object_or_404(DetallePlanilla.objects.select_related("periodo", "empleado"), id=detalle_id, periodo__empresa=empresa)
    html = render_to_string("rrhh/voucher_pdf.html", {"empresa": empresa, "detalle": detalle})
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Voucher_{detalle.periodo.nombre}_{detalle.empleado.nombre_completo}.pdf"'
    return response


@login_required
@require_POST
def enviar_voucher_whatsapp_api(request, empresa_slug, detalle_id):
    empresa = _empresa_desde_slug(empresa_slug)
    detalle = get_object_or_404(
        DetallePlanilla.objects.select_related("periodo", "empleado"),
        id=detalle_id,
        periodo__empresa=empresa,
    )
    config = _configuracion_crm(empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp Cloud API en CRM antes de enviar vouchers por este canal.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)

    telefono = detalle.telefono_whatsapp_normalizado()
    if not telefono:
        messages.error(request, "Este empleado no tiene telefono valido para WhatsApp.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)

    try:
        enviar_mensaje_whatsapp_texto(config, telefono, detalle.resumen_voucher_texto())
        messages.success(request, f"Voucher enviado por WhatsApp API a {detalle.empleado.nombre_completo}.")
    except WhatsAppAPIError as exc:
        messages.error(request, f"No se pudo enviar el voucher por WhatsApp API. Detalle: {exc}")
    return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)


@login_required
def enviar_voucher_email(request, empresa_slug, detalle_id):
    empresa = _empresa_desde_slug(empresa_slug)
    detalle = get_object_or_404(DetallePlanilla.objects.select_related("periodo", "empleado"), id=detalle_id, periodo__empresa=empresa)
    if not detalle.empleado.correo:
        messages.error(request, "Este empleado no tiene correo registrado.")
        return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)
    html = render_to_string("rrhh/voucher_pdf.html", {"empresa": empresa, "detalle": detalle})
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    email = EmailMessage(
        subject=f"Voucher de pago - {detalle.periodo.nombre}",
        body=f"Hola {detalle.empleado.nombres}, adjuntamos tu voucher de pago.",
        to=[detalle.empleado.correo],
    )
    email.attach(f"Voucher_{detalle.periodo.nombre}.pdf", pdf, "application/pdf")
    try:
        email.send(fail_silently=False)
        if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            messages.info(
                request,
                "Voucher generado en modo desarrollo. Para enviarlo a correos reales falta configurar SMTP en el servidor.",
            )
        else:
            messages.success(request, "Voucher enviado por correo correctamente.")
    except Exception as exc:
        messages.error(request, f"No se pudo enviar el correo. Revisa la configuracion SMTP. Detalle: {exc}")
    return redirect("ver_planilla", empresa_slug=empresa.slug, periodo_id=detalle.periodo_id)

# Create your views here.
