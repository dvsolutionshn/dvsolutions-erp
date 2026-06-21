from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Empresa

from .forms import AsignacionOrdenForm, BahiaServicioForm, ConfiguracionTecnicentroForm, DiagnosticoForm, EvidenciaForm, RecepcionOrdenForm
from .models import BahiaServicio, ConfiguracionTecnicentro, DiagnosticoVehicular, HistorialEstadoOrden, OrdenServicio, Vehiculo


ESTADOS_ACTIVOS = ["espera", "recepcion", "diagnostico", "cotizacion", "aprobacion", "reparacion", "pruebas", "listo"]
TRANSICIONES = {
    "espera": {"recepcion", "diagnostico", "cancelado"},
    "recepcion": {"diagnostico", "cancelado"},
    "diagnostico": {"cotizacion", "reparacion", "cancelado"},
    "cotizacion": {"aprobacion", "reparacion", "cancelado"},
    "aprobacion": {"reparacion", "cancelado"},
    "reparacion": {"pruebas", "listo"},
    "pruebas": {"reparacion", "listo"},
    "listo": {"entregado"},
    "entregado": set(),
    "cancelado": set(),
}


def _empresa(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


@login_required
def dashboard(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    activas = OrdenServicio.objects.filter(empresa=empresa, estado__in=ESTADOS_ACTIVOS).select_related(
        "vehiculo", "cliente", "tecnico_asignado", "bahia"
    )
    ahora = timezone.now()
    cola = activas.filter(estado="espera").annotate(
        orden_prioridad=Case(
            When(prioridad="urgente", then=0),
            When(prioridad="alta", then=1),
            default=2,
            output_field=IntegerField(),
        )
    ).order_by("orden_prioridad", "fecha_recepcion")
    columnas = []
    for codigo, titulo in [
        ("espera", "En cola"), ("diagnostico", "Diagnostico"),
        ("reparacion", "En reparacion"), ("pruebas", "Control de calidad"), ("listo", "Listos"),
    ]:
        estados = [codigo]
        if codigo == "diagnostico":
            estados += ["recepcion", "cotizacion", "aprobacion"]
        ordenes_columna = cola if codigo == "espera" else activas.filter(estado__in=estados).order_by("fecha_recepcion")
        columnas.append({"codigo": codigo, "titulo": titulo, "ordenes": ordenes_columna})
    completadas_hoy = OrdenServicio.objects.filter(empresa=empresa, fecha_entrega__date=timezone.localdate()).count()
    promedio = 0
    entregadas = OrdenServicio.objects.filter(empresa=empresa, estado="entregado", fecha_entrega__isnull=False)[:50]
    tiempos = [orden.minutos_en_taller for orden in entregadas]
    if tiempos:
        promedio = sum(tiempos) // len(tiempos)
    return render(request, "tecnicentro/dashboard.html", {
        "empresa": empresa,
        "columnas": columnas,
        "cola": cola,
        "ahora": ahora,
        "metricas": {
            "cola": cola.count(),
            "taller": activas.exclude(estado__in=["espera", "listo"]).count(),
            "listos": activas.filter(estado="listo").count(),
            "entregados_hoy": completadas_hoy,
            "promedio_min": promedio,
        },
    })


@login_required
def recepcion(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    form = RecepcionOrdenForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        with transaction.atomic():
            vehiculo, creado = Vehiculo.objects.get_or_create(
                empresa=empresa,
                placa=datos["placa"],
                defaults={
                    "cliente": datos["cliente"], "marca": datos["marca"], "modelo": datos["modelo"],
                    "anio": datos["anio"], "color": datos["color"], "tipo": datos["tipo_vehiculo"],
                    "combustible": datos["combustible"], "kilometraje_actual": datos["kilometraje_entrada"],
                },
            )
            if not creado:
                vehiculo.cliente = datos["cliente"]
                vehiculo.marca = datos["marca"]
                vehiculo.modelo = datos["modelo"]
                vehiculo.anio = datos["anio"]
                vehiculo.color = datos["color"]
                vehiculo.tipo = datos["tipo_vehiculo"]
                vehiculo.combustible = datos["combustible"]
                vehiculo.kilometraje_actual = datos["kilometraje_entrada"]
                vehiculo.save()
            orden = OrdenServicio.objects.create(
                empresa=empresa, cliente=datos["cliente"], vehiculo=vehiculo,
                asesor_recepcion=request.user, prioridad=datos["prioridad"],
                motivo_ingreso=datos["motivo_ingreso"], observaciones_recepcion=datos["observaciones_recepcion"],
                kilometraje_entrada=datos["kilometraje_entrada"], nivel_combustible=datos["nivel_combustible"],
                deja_vehiculo=datos["deja_vehiculo"], autoriza_whatsapp=datos["autoriza_whatsapp"],
                tiempo_espera_estimado_min=datos["tiempo_espera_estimado_min"],
                tiempo_reparacion_estimado_min=datos["tiempo_reparacion_estimado_min"],
                fecha_estimada_finalizacion=timezone.now() + timedelta(
                    minutes=datos["tiempo_espera_estimado_min"] + datos["tiempo_reparacion_estimado_min"]
                ),
            )
            HistorialEstadoOrden.objects.create(orden=orden, estado="espera", usuario=request.user, nota="Vehiculo recibido en recepcion digital.")
        messages.success(request, f"{orden.numero} creada. {vehiculo.placa} ingreso a la cola del taller.")
        return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)
    return render(request, "tecnicentro/recepcion.html", {"empresa": empresa, "form": form})


@login_required
def ordenes(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    q = (request.GET.get("q") or "").strip()
    estado = (request.GET.get("estado") or "").strip()
    qs = OrdenServicio.objects.filter(empresa=empresa).select_related("vehiculo", "cliente", "tecnico_asignado", "bahia")
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(vehiculo__placa__icontains=q) | Q(cliente__nombre__icontains=q) | Q(vehiculo__marca__icontains=q) | Q(vehiculo__modelo__icontains=q))
    if estado:
        qs = qs.filter(estado=estado)
    return render(request, "tecnicentro/ordenes.html", {
        "empresa": empresa, "ordenes": qs[:250], "q": q, "estado": estado,
        "estados": OrdenServicio.ESTADO_CHOICES,
    })


@login_required
def detalle_orden(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(
        OrdenServicio.objects.select_related("vehiculo", "cliente", "asesor_recepcion", "tecnico_asignado", "bahia", "factura"),
        empresa=empresa, id=orden_id,
    )
    diagnostico_obj = DiagnosticoVehicular.objects.filter(orden=orden).first()
    return render(request, "tecnicentro/detalle_orden.html", {
        "empresa": empresa, "orden": orden, "diagnostico": diagnostico_obj,
        "asignacion_form": AsignacionOrdenForm(instance=orden, empresa=empresa),
        "evidencia_form": EvidenciaForm(),
        "transiciones": [(codigo, dict(OrdenServicio.ESTADO_CHOICES)[codigo]) for codigo in TRANSICIONES.get(orden.estado, set())],
    })


@login_required
@require_POST
def cambiar_estado(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(OrdenServicio, empresa=empresa, id=orden_id)
    nuevo = request.POST.get("estado")
    if nuevo not in TRANSICIONES.get(orden.estado, set()):
        messages.error(request, "La transicion solicitada no es valida para el estado actual.")
    else:
        ahora = timezone.now()
        orden.estado = nuevo
        campos = ["estado", "fecha_actualizacion"]
        if nuevo in {"diagnostico", "reparacion"} and not orden.fecha_ingreso_taller:
            orden.fecha_ingreso_taller = ahora
            campos.append("fecha_ingreso_taller")
        if nuevo == "listo":
            orden.fecha_listo = ahora
            campos.append("fecha_listo")
        if nuevo == "entregado":
            orden.fecha_entrega = ahora
            campos.append("fecha_entrega")
        orden.save(update_fields=campos)
        HistorialEstadoOrden.objects.create(
            orden=orden, estado=nuevo, usuario=request.user,
            nota=(request.POST.get("nota") or "Cambio operativo desde la orden.").strip(),
        )
        messages.success(request, f"{orden.numero} ahora esta en {orden.get_estado_display()}.")
    return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)


@login_required
@require_POST
def asignar_orden(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(OrdenServicio, empresa=empresa, id=orden_id)
    form = AsignacionOrdenForm(request.POST, instance=orden, empresa=empresa)
    if form.is_valid():
        orden = form.save()
        messages.success(request, "Tecnico, bahia y tiempo estimado actualizados.")
    else:
        messages.error(request, "Revisa la asignacion de tecnico y bahia.")
    return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)


@login_required
def diagnostico(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(OrdenServicio, empresa=empresa, id=orden_id)
    objeto = DiagnosticoVehicular.objects.filter(orden=orden).first()
    form = DiagnosticoForm(request.POST or None, instance=objeto)
    if request.method == "POST" and form.is_valid():
        objeto = form.save(commit=False)
        objeto.orden = orden
        objeto.tecnico = orden.tecnico_asignado or request.user
        if objeto.estado == "completado" and not objeto.fecha_finalizacion:
            objeto.fecha_finalizacion = timezone.now()
        objeto.save()
        if orden.estado in {"espera", "recepcion"}:
            orden.estado = "diagnostico"
            orden.fecha_ingreso_taller = orden.fecha_ingreso_taller or timezone.now()
            orden.save(update_fields=["estado", "fecha_ingreso_taller", "fecha_actualizacion"])
            HistorialEstadoOrden.objects.create(orden=orden, estado="diagnostico", usuario=request.user, nota="Diagnostico tecnico iniciado.")
        messages.success(request, "Diagnostico vehicular guardado.")
        return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)
    return render(request, "tecnicentro/diagnostico.html", {"empresa": empresa, "orden": orden, "form": form})


@login_required
@require_POST
def agregar_evidencia(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(OrdenServicio, empresa=empresa, id=orden_id)
    form = EvidenciaForm(request.POST, request.FILES)
    if form.is_valid():
        evidencia = form.save(commit=False)
        evidencia.orden = orden
        evidencia.subido_por = request.user
        evidencia.save()
        messages.success(request, "Evidencia fotografica agregada a la trazabilidad.")
    else:
        messages.error(request, "Selecciona una imagen valida y su etapa.")
    return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)


@login_required
def configuracion(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    objeto, _ = ConfiguracionTecnicentro.objects.get_or_create(empresa=empresa)
    config_form = ConfiguracionTecnicentroForm(instance=objeto)
    bahia_form = BahiaServicioForm()
    if request.method == "POST":
        if request.POST.get("accion") == "guardar_configuracion":
            config_form = ConfiguracionTecnicentroForm(request.POST, instance=objeto)
            if config_form.is_valid():
                config_form.save()
                messages.success(request, "Configuracion operativa del Tecnicentro actualizada.")
                return redirect("tecnicentro_configuracion", empresa_slug=empresa.slug)
        elif request.POST.get("accion") == "crear_bahia":
            bahia_form = BahiaServicioForm(request.POST)
            if bahia_form.is_valid():
                bahia = bahia_form.save(commit=False)
                bahia.empresa = empresa
                bahia.save()
                messages.success(request, f"Bahia {bahia.codigo} creada correctamente.")
                return redirect("tecnicentro_configuracion", empresa_slug=empresa.slug)
    return render(request, "tecnicentro/configuracion.html", {
        "empresa": empresa, "config_form": config_form, "bahia_form": bahia_form,
        "bahias": BahiaServicio.objects.filter(empresa=empresa).order_by("codigo"),
    })
