from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Empresa
from facturacion.models import Cliente

from .forms import AsignacionOrdenForm, BahiaServicioForm, CitaTallerForm, ConfiguracionTecnicentroForm, DiagnosticoForm, EvidenciaForm, InspeccionRecepcionForm, RecepcionOrdenForm
from .models import BahiaServicio, CitaTaller, ConfiguracionTecnicentro, DiagnosticoVehicular, HistorialEstadoOrden, InspeccionRecepcion, OrdenServicio, Vehiculo
from .services import actualizar_estimaciones_cola, espera_para_nuevo_ingreso


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
    actualizar_estimaciones_cola(empresa)
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
        "proximas_citas": CitaTaller.objects.filter(
            empresa=empresa, estado__in=["programada", "confirmada"], fecha_hora__gte=ahora
        ).select_related("cliente", "vehiculo")[:5],
    })


@login_required
def recepcion(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    cita = None
    cita_id = request.GET.get("cita")
    if cita_id:
        cita = get_object_or_404(CitaTaller, empresa=empresa, id=cita_id, estado__in=["programada", "confirmada"])
    inicial = {}
    if cita:
        inicial.update({"cliente": cita.cliente, "motivo_ingreso": cita.servicio_solicitado, "tiempo_reparacion_estimado_min": cita.duracion_estimada_min})
        if cita.vehiculo:
            inicial.update({"placa": cita.vehiculo.placa, "marca": cita.vehiculo.marca, "modelo": cita.vehiculo.modelo, "anio": cita.vehiculo.anio, "color": cita.vehiculo.color, "tipo_vehiculo": cita.vehiculo.tipo, "combustible": cita.vehiculo.combustible, "kilometraje_entrada": cita.vehiculo.kilometraje_actual})
    form = RecepcionOrdenForm(request.POST or None, empresa=empresa, initial=inicial)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        with transaction.atomic():
            cliente = datos["cliente"]
            if not cliente:
                cliente = Cliente.objects.create(
                    empresa=empresa,
                    nombre=datos["nuevo_cliente_nombre"].strip(),
                    telefono=datos["nuevo_cliente_telefono"].strip() or None,
                    telefono_whatsapp=datos["nuevo_cliente_telefono"].strip() or None,
                    rtn=datos["nuevo_cliente_rtn"].strip() or None,
                    activo=True,
                )
            vehiculo, creado = Vehiculo.objects.get_or_create(
                empresa=empresa,
                placa=datos["placa"],
                defaults={
                    "cliente": cliente, "marca": datos["marca"], "modelo": datos["modelo"],
                    "anio": datos["anio"], "color": datos["color"], "tipo": datos["tipo_vehiculo"],
                    "combustible": datos["combustible"], "kilometraje_actual": datos["kilometraje_entrada"],
                },
            )
            if not creado:
                vehiculo.cliente = cliente
                vehiculo.marca = datos["marca"]
                vehiculo.modelo = datos["modelo"]
                vehiculo.anio = datos["anio"]
                vehiculo.color = datos["color"]
                vehiculo.tipo = datos["tipo_vehiculo"]
                vehiculo.combustible = datos["combustible"]
                vehiculo.kilometraje_actual = datos["kilometraje_entrada"]
                vehiculo.save()
            espera_calculada = espera_para_nuevo_ingreso(empresa)
            orden = OrdenServicio.objects.create(
                empresa=empresa, cliente=cliente, vehiculo=vehiculo,
                asesor_recepcion=request.user, prioridad=datos["prioridad"],
                motivo_ingreso=datos["motivo_ingreso"], observaciones_recepcion=datos["observaciones_recepcion"],
                kilometraje_entrada=datos["kilometraje_entrada"], nivel_combustible=datos["nivel_combustible"],
                deja_vehiculo=datos["deja_vehiculo"], autoriza_whatsapp=datos["autoriza_whatsapp"],
                tiempo_espera_estimado_min=espera_calculada,
                tiempo_reparacion_estimado_min=datos["tiempo_reparacion_estimado_min"],
                fecha_estimada_finalizacion=timezone.now() + timedelta(
                    minutes=espera_calculada + datos["tiempo_reparacion_estimado_min"]
                ),
            )
            if cita:
                cita.orden = orden
                cita.estado = "atendida"
                cita.save(update_fields=["orden", "estado"])
            HistorialEstadoOrden.objects.create(orden=orden, estado="espera", usuario=request.user, nota="Vehiculo recibido en recepcion digital.")
        messages.success(request, f"{orden.numero} creada. Espera calculada: {espera_calculada} minutos.")
        return redirect("tecnicentro_inspeccion", empresa_slug=empresa.slug, orden_id=orden.id)
    return render(request, "tecnicentro/recepcion.html", {"empresa": empresa, "form": form, "cita": cita})


@login_required
def agenda(request, empresa_slug):
    empresa = _empresa(empresa_slug)
    form = CitaTallerForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.creado_por = request.user
        cita.save()
        messages.success(request, "Cita registrada en la agenda del taller.")
        return redirect("tecnicentro_agenda", empresa_slug=empresa.slug)
    inicio = timezone.localdate()
    citas = CitaTaller.objects.filter(empresa=empresa, fecha_hora__date__gte=inicio).select_related("cliente", "vehiculo", "orden")[:100]
    return render(request, "tecnicentro/agenda.html", {"empresa": empresa, "form": form, "citas": citas})


@login_required
@require_POST
def cambiar_estado_cita(request, empresa_slug, cita_id):
    empresa = _empresa(empresa_slug)
    cita = get_object_or_404(CitaTaller, empresa=empresa, id=cita_id)
    nuevo = request.POST.get("estado")
    permitidos = {
        "programada": {"confirmada", "cancelada", "no_asistio"},
        "confirmada": {"cancelada", "no_asistio"},
    }
    if nuevo not in permitidos.get(cita.estado, set()):
        messages.error(request, "Ese cambio no es válido para el estado actual de la cita.")
    else:
        cita.estado = nuevo
        cita.save(update_fields=["estado"])
        messages.success(request, f"Cita actualizada: {cita.get_estado_display()}.")
    return redirect("tecnicentro_agenda", empresa_slug=empresa.slug)


@login_required
def inspeccion_recepcion(request, empresa_slug, orden_id):
    empresa = _empresa(empresa_slug)
    orden = get_object_or_404(OrdenServicio.objects.select_related("vehiculo", "cliente"), empresa=empresa, id=orden_id)
    objeto = InspeccionRecepcion.objects.filter(orden=orden).first()
    form = InspeccionRecepcionForm(request.POST or None, instance=objeto)
    if request.method == "POST" and form.is_valid():
        inspeccion = form.save(commit=False)
        inspeccion.orden = orden
        inspeccion.inspeccionado_por = request.user
        inspeccion.save()
        HistorialEstadoOrden.objects.create(orden=orden, estado=orden.estado, usuario=request.user, nota="Inspección de recepción documentada y aceptada." if inspeccion.aceptacion_cliente else "Inspección de recepción documentada.")
        messages.success(request, "Inspección de entrada guardada en la trazabilidad del vehículo.")
        return redirect("tecnicentro_detalle_orden", empresa_slug=empresa.slug, orden_id=orden.id)
    return render(request, "tecnicentro/inspeccion.html", {"empresa": empresa, "orden": orden, "form": form})


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
        "inspeccion": InspeccionRecepcion.objects.filter(orden=orden).first(),
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
