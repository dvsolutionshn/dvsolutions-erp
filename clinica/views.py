from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Empresa

from .forms import CitaClinicaForm, ExpedienteEventoForm, PacienteForm, ProfesionalSaludForm, ServicioClinicoForm, TratamientoPacienteForm
from .models import (
    CitaClinica,
    ConfiguracionClinica,
    ExpedienteEvento,
    Paciente,
    ProfesionalSalud,
    SeguimientoPostOperatorio,
    ServicioClinico,
    TratamientoPaciente,
)


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_clinica(empresa):
    return ConfiguracionClinica.objects.get_or_create(empresa=empresa)[0]


def _proximo_codigo_expediente(empresa):
    prefijo = "MIA" if "mia" in (empresa.slug or "").lower() or "mia" in (empresa.nombre or "").lower() else "EXP"
    total = Paciente.objects.filter(empresa=empresa).count() + 1
    return f"{prefijo}-{total:05d}"


@login_required
def clinica_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    citas_hoy = CitaClinica.objects.filter(empresa=empresa, fecha_hora__date=hoy)
    citas_mes = CitaClinica.objects.filter(empresa=empresa, fecha_hora__date__gte=inicio_mes)
    pacientes = Paciente.objects.filter(empresa=empresa)
    tratamientos_activos = TratamientoPaciente.objects.filter(empresa=empresa, estado__in=["planificado", "en_proceso"])
    seguimientos_pendientes = SeguimientoPostOperatorio.objects.filter(
        empresa=empresa,
        estado__in=["pendiente", "requiere_revision"],
        fecha_programada__gte=hoy,
    )
    ultimos_eventos = ExpedienteEvento.objects.filter(empresa=empresa).select_related("paciente", "profesional")[:8]
    proximas_citas = (
        CitaClinica.objects.filter(empresa=empresa, fecha_hora__date__gte=hoy)
        .select_related("paciente", "profesional", "servicio")
        .order_by("fecha_hora")[:8]
    )
    embudo_citas = (
        citas_mes
        .values("estado")
        .annotate(total=Count("id"))
        .order_by("estado")
    )
    agenda_estado = {
        "solicitadas": citas_mes.filter(estado="solicitada").count(),
        "confirmadas": citas_mes.filter(estado="confirmada").count(),
        "en_atencion": citas_hoy.filter(estado="en_atencion").count(),
        "completadas": citas_mes.filter(estado="completada").count(),
    }
    automatizaciones = [
        {
            "titulo": "Preconsulta inteligente",
            "estado": "Activa",
            "detalle": "Nuevo paciente, ficha base, cita y expediente en una sola secuencia.",
        },
        {
            "titulo": "Confirmacion de agenda",
            "estado": "Pendiente" if agenda_estado["solicitadas"] else "Lista",
            "detalle": f"{agenda_estado['solicitadas']} cita(s) del mes esperando confirmacion.",
        },
        {
            "titulo": "Seguimiento postoperatorio",
            "estado": "Pendiente" if seguimientos_pendientes.exists() else "Listo",
            "detalle": f"{seguimientos_pendientes.count()} control(es) proximos o con revision requerida.",
        },
    ]
    return render(
        request,
        "clinica/dashboard.html",
        {
            "empresa": empresa,
            "config": _configuracion_clinica(empresa),
            "resumen": {
                "pacientes": pacientes.filter(activo=True).count(),
                "citas_hoy": citas_hoy.count(),
                "tratamientos_activos": tratamientos_activos.count(),
                "eventos_mes": ExpedienteEvento.objects.filter(empresa=empresa, fecha__date__gte=inicio_mes).count(),
                "seguimientos_pendientes": seguimientos_pendientes.count(),
            },
            "proximas_citas": proximas_citas,
            "ultimos_eventos": ultimos_eventos,
            "embudo_citas": embudo_citas,
            "agenda_estado": agenda_estado,
            "automatizaciones": automatizaciones,
        },
    )


@login_required
def pacientes(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    q = (request.GET.get("q") or "").strip()
    pacientes_qs = Paciente.objects.filter(empresa=empresa).order_by("nombre")
    if q:
        pacientes_qs = pacientes_qs.filter(
            Q(nombre__icontains=q) | Q(expediente_codigo__icontains=q) | Q(identidad__icontains=q) | Q(telefono__icontains=q)
        )
    return render(request, "clinica/pacientes.html", {"empresa": empresa, "pacientes": pacientes_qs, "q": q})


@login_required
def crear_paciente(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    initial = {"expediente_codigo": _proximo_codigo_expediente(empresa)}
    form = PacienteForm(request.POST or None, empresa=empresa, initial=initial)
    if request.method == "POST" and form.is_valid():
        paciente = form.save(commit=False)
        paciente.empresa = empresa
        paciente.creado_por = request.user
        paciente.save()
        messages.success(request, "Paciente creado correctamente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/form.html", {"empresa": empresa, "form": form, "titulo": "Nuevo paciente"})


@login_required
def editar_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = PacienteForm(request.POST or None, empresa=empresa, instance=paciente)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Paciente actualizado correctamente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/form.html", {"empresa": empresa, "form": form, "titulo": f"Editar paciente: {paciente.nombre}"})


@login_required
def paciente_detalle(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    eventos = paciente.eventos_expediente.select_related("profesional", "tratamiento")[:20]
    citas = paciente.citas.select_related("profesional", "servicio")[:10]
    tratamientos = paciente.tratamientos.select_related("profesional", "servicio")[:10]
    return render(
        request,
        "clinica/paciente_detalle.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "eventos": eventos,
            "citas": citas,
            "tratamientos": tratamientos,
        },
    )


@login_required
def citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    citas_qs = CitaClinica.objects.filter(empresa=empresa).select_related("paciente", "profesional", "servicio")
    return render(request, "clinica/citas.html", {"empresa": empresa, "citas": citas_qs})


@login_required
def crear_cita(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CitaClinicaForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        messages.success(request, "Cita clinica guardada correctamente.")
        return redirect("clinica_citas", empresa_slug=empresa.slug)
    return render(request, "clinica/form.html", {"empresa": empresa, "form": form, "titulo": "Nueva cita clinica"})


@login_required
def crear_evento_expediente(request, empresa_slug, paciente_id=None):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = None
    if paciente_id:
        paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = ExpedienteEventoForm(request.POST or None, empresa=empresa, paciente=paciente, initial={"paciente": paciente})
    if request.method == "POST" and form.is_valid():
        evento = form.save(commit=False)
        evento.empresa = empresa
        evento.creado_por = request.user
        evento.save()
        messages.success(request, "Evento agregado al expediente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=evento.paciente_id)
    return render(request, "clinica/form.html", {"empresa": empresa, "form": form, "titulo": "Agregar evento al expediente"})


@login_required
def tratamientos(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    tratamientos_qs = TratamientoPaciente.objects.filter(empresa=empresa).select_related("paciente", "profesional", "servicio")
    return render(request, "clinica/tratamientos.html", {"empresa": empresa, "tratamientos": tratamientos_qs})


@login_required
def crear_tratamiento(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = TratamientoPacienteForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        tratamiento = form.save(commit=False)
        tratamiento.empresa = empresa
        tratamiento.save()
        messages.success(request, "Tratamiento creado correctamente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=tratamiento.paciente_id)
    return render(request, "clinica/form.html", {"empresa": empresa, "form": form, "titulo": "Nuevo tratamiento"})


@login_required
def profesionales(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = ProfesionalSaludForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        profesional = form.save(commit=False)
        profesional.empresa = empresa
        profesional.save()
        messages.success(request, "Profesional guardado correctamente.")
        return redirect("clinica_profesionales", empresa_slug=empresa.slug)
    profesionales_qs = ProfesionalSalud.objects.filter(empresa=empresa)
    return render(request, "clinica/catalogo.html", {"empresa": empresa, "form": form, "items": profesionales_qs, "titulo": "Profesionales"})


@login_required
def servicios(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = ServicioClinicoForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        servicio = form.save(commit=False)
        servicio.empresa = empresa
        servicio.save()
        messages.success(request, "Servicio clinico guardado correctamente.")
        return redirect("clinica_servicios", empresa_slug=empresa.slug)
    servicios_qs = ServicioClinico.objects.filter(empresa=empresa)
    return render(request, "clinica/catalogo.html", {"empresa": empresa, "form": form, "items": servicios_qs, "titulo": "Servicios clinicos"})
