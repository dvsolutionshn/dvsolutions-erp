from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import ExtractDay, ExtractMonth
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa
from contabilidad.services import asegurar_cuenta_contable_cliente
from facturacion.models import Cliente

from .forms import CitaClinicaForm, ExpedienteEventoForm, HistoriaClinicaEspecialidadForm, PacienteForm, ProfesionalSaludForm, ServicioClinicoForm, TratamientoPacienteForm
from .models import (
    CitaClinica,
    ConsentimientoClinico,
    ConfiguracionClinica,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    MedicamentoPrescrito,
    Paciente,
    PacienteFotoEvolucion,
    ProfesionalSalud,
    SeguimientoPostOperatorio,
    ServicioClinico,
    TratamientoPaciente,
)


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_clinica(empresa):
    return ConfiguracionClinica.objects.get_or_create(empresa=empresa)[0]


def _requiere_hospital_mia(empresa):
    if empresa.slug != "hospital_mia":
        raise Http404("Los formularios hospitalarios no estan habilitados para esta empresa.")


def _proximo_codigo_expediente(empresa):
    prefijo = "MIA" if "mia" in (empresa.slug or "").lower() or "mia" in (empresa.nombre or "").lower() else "EXP"
    total = Paciente.objects.filter(empresa=empresa).count() + 1
    return f"{prefijo}-{total:05d}"


def _sincronizar_cliente_facturacion_paciente(paciente):
    cliente = paciente.cliente if paciente.cliente_id and paciente.cliente.empresa_id == paciente.empresa_id else None
    identidad = (paciente.identidad or "").strip()
    if not cliente and identidad:
        cliente = Cliente.objects.filter(empresa=paciente.empresa, rtn__iexact=identidad).first()
    if not cliente and paciente.nombre:
        cliente = Cliente.objects.filter(empresa=paciente.empresa, nombre__iexact=paciente.nombre.strip()).first()

    datos = {
        "nombre": paciente.nombre or "Paciente sin nombre",
        "rtn": identidad,
        "telefono": paciente.telefono or paciente.whatsapp or paciente.celular_2 or "",
        "telefono_whatsapp": paciente.whatsapp or paciente.telefono or "",
        "correo": paciente.correo or "",
        "fecha_nacimiento": paciente.fecha_nacimiento,
        "acepta_promociones": paciente.acepta_promociones,
        "direccion": paciente.direccion or "",
        "ciudad": paciente.municipio or paciente.departamento or "",
        "canal_preferido": "correo" if paciente.recibir_email and paciente.correo else "whatsapp",
        "activo": paciente.activo,
    }
    if cliente:
        cambios = []
        for campo, valor in datos.items():
            if campo == "rtn" and valor:
                existe = Cliente.objects.filter(
                    empresa=paciente.empresa,
                    rtn__iexact=valor,
                ).exclude(pk=cliente.pk).exists()
                if existe:
                    continue
            if campo == "nombre" and valor:
                existe = Cliente.objects.filter(
                    empresa=paciente.empresa,
                    nombre__iexact=valor,
                ).exclude(pk=cliente.pk).exists()
                if existe:
                    continue
            if getattr(cliente, campo) != valor:
                setattr(cliente, campo, valor)
                cambios.append(campo)
        if cambios:
            cliente.save(update_fields=cambios)
    else:
        cliente = Cliente.objects.create(empresa=paciente.empresa, **datos)

    if paciente.cliente_id != cliente.id:
        paciente.cliente = cliente
        paciente.save(update_fields=["cliente"])
    asegurar_cuenta_contable_cliente(cliente)
    return cliente


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
    hoy = timezone.localdate()
    q = (request.GET.get("q") or "").strip()
    pacientes_qs = (
        Paciente.objects.filter(empresa=empresa)
        .annotate(
            cumple_mes=Case(
                When(fecha_nacimiento__month=hoy.month, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            mes_nacimiento=ExtractMonth("fecha_nacimiento"),
            dia_nacimiento=ExtractDay("fecha_nacimiento"),
        )
        .order_by("cumple_mes", "dia_nacimiento", "primer_nombre", "primer_apellido", "nombre")
    )
    if q:
        pacientes_qs = pacientes_qs.filter(
            Q(nombre__icontains=q) | Q(expediente_codigo__icontains=q) | Q(identidad__icontains=q) | Q(telefono__icontains=q)
        )
    cumpleaneros_mes = pacientes_qs.filter(fecha_nacimiento__month=hoy.month).count()
    return render(request, "clinica/pacientes.html", {
        "empresa": empresa,
        "pacientes": pacientes_qs,
        "q": q,
        "mes_actual": hoy,
        "cumpleaneros_mes": cumpleaneros_mes,
    })


@login_required
def pacientes_sugerencias(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    pacientes_qs = (
        Paciente.objects.filter(empresa=empresa)
        .filter(
            Q(identidad__icontains=q)
            | Q(nombre__icontains=q)
            | Q(expediente_codigo__icontains=q)
            | Q(telefono__icontains=q)
            | Q(whatsapp__icontains=q)
            | Q(correo__icontains=q)
        )
        .order_by("identidad", "nombre")[:8]
    )
    results = [
        {
            "id": paciente.id,
            "nombre": paciente.nombre,
            "documento": paciente.identidad or "",
            "expediente": paciente.expediente_codigo,
            "telefono": paciente.whatsapp or paciente.telefono or "",
            "url": request.build_absolute_uri(
                reverse("clinica_paciente_detalle", args=[empresa.slug, paciente.id])
            ),
            "alergico": paciente.es_alergico,
        }
        for paciente in pacientes_qs
    ]
    return JsonResponse({"results": results})


@login_required
def crear_paciente(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    initial = {"expediente_codigo": _proximo_codigo_expediente(empresa)}
    form = PacienteForm(request.POST or None, request.FILES or None, empresa=empresa, initial=initial)
    if request.method == "POST" and form.is_valid():
        paciente = form.save(commit=False)
        paciente.empresa = empresa
        paciente.creado_por = request.user
        paciente.save()
        _sincronizar_cliente_facturacion_paciente(paciente)
        if paciente.foto_perfil:
            PacienteFotoEvolucion.objects.create(
                empresa=empresa,
                paciente=paciente,
                imagen=paciente.foto_perfil,
                tipo="ingreso",
                titulo="Foto de ingreso",
                descripcion="Foto registrada al crear el expediente del paciente.",
                creado_por=request.user,
            )
        messages.success(request, "Paciente creado correctamente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/paciente_form.html", {"empresa": empresa, "form": form, "titulo": "Nuevo paciente"})


@login_required
def editar_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    foto_anterior = paciente.foto_perfil.name if paciente.foto_perfil else ""
    form = PacienteForm(request.POST or None, request.FILES or None, empresa=empresa, instance=paciente)
    if request.method == "POST" and form.is_valid():
        paciente = form.save()
        _sincronizar_cliente_facturacion_paciente(paciente)
        foto_nueva = paciente.foto_perfil.name if paciente.foto_perfil else ""
        if foto_nueva and foto_nueva != foto_anterior:
            PacienteFotoEvolucion.objects.create(
                empresa=empresa,
                paciente=paciente,
                imagen=paciente.foto_perfil,
                tipo="evolucion",
                titulo="Actualizacion de foto de perfil",
                descripcion="Foto registrada desde la edicion del paciente.",
                creado_por=request.user,
            )
        messages.success(request, "Paciente actualizado correctamente.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/paciente_form.html", {"empresa": empresa, "form": form, "titulo": f"Editar paciente: {paciente.nombre}"})


@login_required
def paciente_detalle(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    eventos = paciente.eventos_expediente.select_related("profesional", "tratamiento")[:20]
    citas = paciente.citas.select_related("profesional", "servicio")[:10]
    tratamientos = paciente.tratamientos.select_related("profesional", "servicio")[:10]
    fotos_evolucion = paciente.fotos_evolucion.select_related("creado_por")[:12]
    medicamentos = MedicamentoPrescrito.objects.filter(empresa=empresa, paciente=paciente)[:10]
    consentimientos = ConsentimientoClinico.objects.filter(empresa=empresa, paciente=paciente)[:10]
    historias_especialidad = paciente.historias_especialidad.select_related("profesional", "actualizado_por")[:20]
    return render(
        request,
        "clinica/paciente_detalle.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "eventos": eventos,
            "citas": citas,
            "tratamientos": tratamientos,
            "fotos_evolucion": fotos_evolucion,
            "medicamentos": medicamentos,
            "consentimientos": consentimientos,
            "historias_especialidad": historias_especialidad,
            "formularios_hospitalarios": empresa.slug == "hospital_mia",
        },
    )


@login_required
def historias_especialidad(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    historias = paciente.historias_especialidad.select_related("profesional", "actualizado_por")
    tipos = [
        {"codigo": codigo, "nombre": nombre, "total": historias.filter(tipo=codigo).count()}
        for codigo, nombre in HistoriaClinicaEspecialidad.TIPO_CHOICES
    ]
    return render(
        request,
        "clinica/historias_especialidad.html",
        {"empresa": empresa, "paciente": paciente, "historias": historias, "tipos": tipos},
    )


@login_required
def crear_historia_especialidad(request, empresa_slug, paciente_id, tipo):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    tipos_validos = dict(HistoriaClinicaEspecialidad.TIPO_CHOICES)
    if tipo not in tipos_validos:
        raise Http404("Formulario clinico no valido.")
    initial = {"fecha_atencion": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
    form = HistoriaClinicaEspecialidadForm(
        request.POST or None,
        empresa=empresa,
        tipo=tipo,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        historia = form.save(commit=False)
        historia.empresa = empresa
        historia.paciente = paciente
        historia.tipo = tipo
        historia.creado_por = request.user
        historia.actualizado_por = request.user
        historia.save()
        messages.success(request, f"Historia de {historia.get_tipo_display()} guardada correctamente.")
        return redirect("clinica_historias_especialidad", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(
        request,
        "clinica/historia_especialidad_form.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "form": form,
            "tipo_nombre": tipos_validos[tipo],
            "titulo": f"Nueva historia: {tipos_validos[tipo]}",
        },
    )


@login_required
def editar_historia_especialidad(request, empresa_slug, paciente_id, historia_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    historia = get_object_or_404(
        HistoriaClinicaEspecialidad,
        id=historia_id,
        empresa=empresa,
        paciente=paciente,
    )
    form = HistoriaClinicaEspecialidadForm(
        request.POST or None,
        empresa=empresa,
        tipo=historia.tipo,
        instance=historia,
    )
    if request.method == "POST" and form.is_valid():
        historia = form.save(commit=False)
        historia.actualizado_por = request.user
        historia.save()
        messages.success(request, "Historia clinica actualizada correctamente.")
        return redirect("clinica_historias_especialidad", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(
        request,
        "clinica/historia_especialidad_form.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "historia": historia,
            "form": form,
            "tipo_nombre": historia.get_tipo_display(),
            "titulo": f"Editar historia: {historia.get_tipo_display()}",
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
