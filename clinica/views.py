from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import ExtractDay, ExtractMonth
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from core.models import Empresa
from contabilidad.services import asegurar_cuenta_contable_cliente
from facturacion.models import Cliente

from .forms import (
    ANTECEDENTES_FAMILIARES_CHOICES,
    ANTECEDENTES_PERSONALES_CHOICES,
    ALERGIAS_GENERALES_CHOICES,
    CONSUMO_RIESGO_CHOICES,
    DECISION_CIRUGIA_CHOICES,
    FRECUENCIA_CHOICES,
    MEDICAMENTOS_HABITUALES_CHOICES,
    MEDICAMENTOS_ACTUALES_CHOICES,
    PROCEDIMIENTOS_GENERALES_CHOICES,
    RIESGO_TROMBOEMBOLICO_CHOICES,
    PSICOLOGICA_CHOICES,
    SI_NO_CHOICES,
    CitaClinicaForm,
    ExpedienteEventoForm,
    HistoriaClinicaEspecialidadForm,
    PacienteForm,
    PreconsultaClinicaPublicaForm,
    ProfesionalSaludForm,
    ServicioClinicoForm,
    TratamientoPacienteForm,
)
from .models import (
    CitaClinica,
    ConsentimientoClinico,
    ConfiguracionClinica,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    InvitacionRegistroPaciente,
    MedicamentoPrescrito,
    Paciente,
    PacienteFotoEvolucion,
    PreconsultaClinica,
    ProfesionalSalud,
    SeguimientoPostOperatorio,
    ServicioClinico,
    TratamientoPaciente,
)
from .tokens import generar_token_preconsulta, hash_token_preconsulta
from crm.forms import PacienteRapidoCitaForm


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_clinica(empresa):
    return ConfiguracionClinica.objects.get_or_create(empresa=empresa)[0]


EMPRESAS_FORMULARIOS_CLINICOS = {"hospital_mia", "medical_spa", "luque_aestetic"}


def _requiere_hospital_mia(empresa):
    if empresa.slug not in EMPRESAS_FORMULARIOS_CLINICOS:
        raise Http404("Los formularios hospitalarios no estan habilitados para esta empresa.")


def _ip_cliente(request):
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def _etiquetas_seleccion(valores, choices):
    etiquetas = dict(choices)
    return [etiquetas.get(valor, valor) for valor in (valores or [])]


def _resumen_preconsulta(preconsulta):
    general = {}
    if isinstance(preconsulta.datos_generales, dict):
        general = preconsulta.datos_generales.get("formulario_general", {}) or {}
    return {
        "antecedentes_personales": _etiquetas_seleccion(
            preconsulta.antecedentes_personales,
            ANTECEDENTES_PERSONALES_CHOICES,
        ),
        "medicamentos": _etiquetas_seleccion(
            preconsulta.medicamentos_habituales,
            MEDICAMENTOS_HABITUALES_CHOICES,
        ),
        "antecedentes_familiares": _etiquetas_seleccion(
            preconsulta.antecedentes_familiares,
            ANTECEDENTES_FAMILIARES_CHOICES,
        ),
        "general": general,
        "procedimientos": _etiquetas_seleccion(general.get("procedimientos_interes"), PROCEDIMIENTOS_GENERALES_CHOICES),
        "alergias_generales": _etiquetas_seleccion(general.get("alergias_seleccion"), ALERGIAS_GENERALES_CHOICES),
        "medicamentos_actuales": _etiquetas_seleccion(general.get("medicamentos_actuales_seleccion"), MEDICAMENTOS_ACTUALES_CHOICES),
        "riesgo_tromboembolico": _etiquetas_seleccion(general.get("riesgo_tromboembolico"), RIESGO_TROMBOEMBOLICO_CHOICES),
        "decision_cirugia": _etiquetas_seleccion(general.get("decision_cirugia"), DECISION_CIRUGIA_CHOICES),
        "tabaco_frecuencia": _etiquetas_seleccion(general.get("tabaco_frecuencia"), FRECUENCIA_CHOICES),
        "alcohol_frecuencia": _etiquetas_seleccion(general.get("alcohol_frecuencia"), FRECUENCIA_CHOICES),
        "drogas_recreativas": _etiquetas_seleccion(general.get("drogas_recreativas"), SI_NO_CHOICES),
        "consumo_riesgo": _etiquetas_seleccion(general.get("consumo_riesgo"), CONSUMO_RIESGO_CHOICES),
        "gine_embarazada": _etiquetas_seleccion(general.get("gine_embarazada"), SI_NO_CHOICES),
        "gine_lactancia": _etiquetas_seleccion(general.get("gine_lactancia"), SI_NO_CHOICES),
        "gine_mamografia": _etiquetas_seleccion(general.get("gine_mamografia"), SI_NO_CHOICES),
        "quirurgicos_operado": _etiquetas_seleccion(general.get("quirurgicos_operado"), SI_NO_CHOICES),
        "expectativas_realistas": _etiquetas_seleccion(general.get("expectativas_realistas"), SI_NO_CHOICES),
        "busca_perfeccion": _etiquetas_seleccion(general.get("busca_perfeccion"), SI_NO_CHOICES),
        "multiples_cirugias_insatisfaccion": _etiquetas_seleccion(general.get("multiples_cirugias_insatisfaccion"), SI_NO_CHOICES),
        "evaluacion_psicologica": _etiquetas_seleccion(general.get("evaluacion_psicologica"), PSICOLOGICA_CHOICES),
    }


def _actualizar_paciente_desde_preconsulta(paciente, form):
    campos_directos = [
        "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
        "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "direccion",
        "lugar_nacimiento", "ocupacion", "contacto_emergencia", "telefono_emergencia",
    ]
    for campo in campos_directos:
        setattr(paciente, campo, form.cleaned_data.get(campo))
    paciente.telefono = form.cleaned_data.get("telefono")
    paciente.whatsapp = form.cleaned_data.get("telefono")
    paciente.prefijo_telefono = form.cleaned_data.get("telefono_codigo_area") or paciente.prefijo_telefono or "504"
    alergias = (form.cleaned_data.get("alergias") or "").strip()
    paciente.alergias = alergias
    paciente.es_alergico = bool(alergias)
    antecedentes = _etiquetas_seleccion(
        form.cleaned_data.get("antecedentes_personales"),
        ANTECEDENTES_PERSONALES_CHOICES,
    )
    detalle_antecedentes = (form.cleaned_data.get("antecedentes_personales_detalle") or "").strip()
    paciente.antecedentes_medicos = "; ".join(antecedentes + ([detalle_antecedentes] if detalle_antecedentes else []))
    medicamentos = _etiquetas_seleccion(
        form.cleaned_data.get("medicamentos_habituales"),
        MEDICAMENTOS_HABITUALES_CHOICES,
    )
    detalle_medicamentos = (form.cleaned_data.get("medicamentos_habituales_detalle") or "").strip()
    paciente.medicamentos_actuales = "; ".join(medicamentos + ([detalle_medicamentos] if detalle_medicamentos else []))
    paciente.save()
    _sincronizar_cliente_facturacion_paciente(paciente)


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
            "formularios_hospitalarios": empresa.slug in EMPRESAS_FORMULARIOS_CLINICOS,
        },
    )


@login_required
def historias_especialidad(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    historias = paciente.historias_especialidad.select_related("profesional", "actualizado_por")
    preconsultas = paciente.preconsultas.select_related("creada_por")[:30]
    tipos = [
        {
            "codigo": codigo,
            "nombre": nombre,
            "total": historias.filter(tipo=codigo).count(),
            "preconsultas": paciente.preconsultas.filter(tipo=codigo).count(),
        }
        for codigo, nombre in HistoriaClinicaEspecialidad.TIPO_CHOICES
    ]
    return render(
        request,
        "clinica/historias_especialidad.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "historias": historias,
            "tipos": tipos,
            "preconsultas": preconsultas,
        },
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
    preconsultas_tipo = paciente.preconsultas.filter(tipo=tipo).select_related("creada_por")[:10]
    ultima_preconsulta = (
        paciente.preconsultas.filter(estado="completada", tipo__in=[tipo, "general"])
        .order_by("-fecha_completada", "-fecha_creacion")
        .first()
    )
    if ultima_preconsulta:
        resumen = _resumen_preconsulta(ultima_preconsulta)
        bloques_antecedentes = []
        if resumen["antecedentes_personales"]:
            bloques_antecedentes.append("Personales: " + ", ".join(resumen["antecedentes_personales"]))
        if ultima_preconsulta.antecedentes_personales_detalle:
            bloques_antecedentes.append(ultima_preconsulta.antecedentes_personales_detalle)
        if ultima_preconsulta.antecedentes_hospitalarios_detalle:
            bloques_antecedentes.append("Hospitalarios/quirurgicos: " + ultima_preconsulta.antecedentes_hospitalarios_detalle)
        if resumen["medicamentos"]:
            bloques_antecedentes.append("Medicamentos: " + ", ".join(resumen["medicamentos"]))
        if ultima_preconsulta.alergias:
            bloques_antecedentes.append("ALERGIAS: " + ultima_preconsulta.alergias)
        initial.update({
            "motivo_consulta": ultima_preconsulta.motivo_consulta,
            "antecedentes": "\n".join(bloques_antecedentes),
        })
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
            "ultima_preconsulta": ultima_preconsulta,
            "resumen_preconsulta": _resumen_preconsulta(ultima_preconsulta) if ultima_preconsulta else None,
            "tipo": tipo,
            "preconsultas_tipo": preconsultas_tipo,
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
    bloqueada = historia.bloqueada
    if bloqueada and request.method == "POST":
        messages.error(request, "La nota de enfermeria ya fue finalizada y no puede editarse.")
        return redirect("clinica_historias_especialidad", empresa_slug=empresa.slug, paciente_id=paciente.id)
    form = HistoriaClinicaEspecialidadForm(
        request.POST or None,
        empresa=empresa,
        tipo=historia.tipo,
        instance=historia,
    )
    if bloqueada:
        for field in form.fields.values():
            field.disabled = True
    preconsultas_tipo = paciente.preconsultas.filter(tipo=historia.tipo).select_related("creada_por")[:10]
    ultima_preconsulta = (
        paciente.preconsultas.filter(estado="completada", tipo__in=[historia.tipo, "general"])
        .order_by("-fecha_completada", "-fecha_creacion")
        .first()
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
            "titulo": (
                f"Ver nota finalizada: {historia.get_tipo_display()}"
                if bloqueada
                else f"Editar historia: {historia.get_tipo_display()}"
            ),
            "ultima_preconsulta": ultima_preconsulta,
            "resumen_preconsulta": _resumen_preconsulta(ultima_preconsulta) if ultima_preconsulta else None,
            "tipo": historia.tipo,
            "preconsultas_tipo": preconsultas_tipo,
            "bloqueada": bloqueada,
        },
    )


@login_required
@require_POST
def generar_enlace_preconsulta(request, empresa_slug, paciente_id, tipo="general"):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    tipos_validos = dict(PreconsultaClinica.TIPO_CHOICES)
    if tipo not in tipos_validos:
        raise Http404("Tipo de preconsulta no valido.")
    paciente.preconsultas.filter(estado="pendiente", tipo=tipo).update(estado="revocada")
    token_raw, token_hash, token_preview = generar_token_preconsulta()
    preconsulta = PreconsultaClinica.objects.create(
        empresa=empresa,
        paciente=paciente,
        tipo=tipo,
        token_hash=token_hash,
        token_preview=token_preview,
        fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
        creada_por=request.user,
    )
    enlace_publico = request.build_absolute_uri(
        reverse("clinica_preconsulta_publica", args=[token_raw])
    )
    telefono = "".join(c for c in (paciente.whatsapp or paciente.telefono or "") if c.isdigit())
    if len(telefono) == 8:
        telefono = "504" + telefono
    mensaje = quote(
        f"Hola {paciente.primer_nombre or paciente.nombre}. {empresa.nombre} le comparte su formulario de preconsulta "
        f"de {tipos_validos[tipo]}. "
        f"Complete la informacion antes de su cita en este enlace seguro: {enlace_publico}"
    )
    whatsapp_url = f"https://wa.me/{telefono}?text={mensaje}" if telefono else ""
    return render(
        request,
        "clinica/preconsulta_enlace.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "preconsulta": preconsulta,
            "enlace_publico": enlace_publico,
            "whatsapp_url": whatsapp_url,
            "tipo_nombre": tipos_validos[tipo],
        },
    )


@login_required
@require_POST
def generar_enlace_registro_paciente(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    token_raw, token_hash, token_preview = generar_token_preconsulta()
    invitacion = InvitacionRegistroPaciente.objects.create(
        empresa=empresa,
        token_hash=token_hash,
        token_preview=token_preview,
        fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
        creada_por=request.user,
    )
    enlace_publico = request.build_absolute_uri(
        reverse("clinica_registro_paciente_publico", args=[token_raw])
    )
    mensaje = quote(
        f"Hola. {empresa.nombre} le comparte su formulario seguro para crear su expediente como paciente nuevo. "
        f"Complete la informacion y adjunte su fotografia en este enlace: {enlace_publico}"
    )
    return render(
        request,
        "clinica/registro_paciente_enlace.html",
        {
            "empresa": empresa,
            "invitacion": invitacion,
            "enlace_publico": enlace_publico,
            "whatsapp_url": f"https://api.whatsapp.com/send?text={mensaje}",
        },
    )


@login_required
def preconsulta_detalle(request, empresa_slug, paciente_id, preconsulta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    preconsulta = get_object_or_404(
        PreconsultaClinica,
        id=preconsulta_id,
        empresa=empresa,
        paciente=paciente,
    )
    return render(
        request,
        "clinica/preconsulta_detalle.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "preconsulta": preconsulta,
            "resumen": _resumen_preconsulta(preconsulta),
        },
    )


@never_cache
@sensitive_post_parameters()
def preconsulta_publica(request, token):
    preconsulta = get_object_or_404(
        PreconsultaClinica.objects.select_related("empresa", "paciente"),
        token_hash=hash_token_preconsulta(token),
        empresa__slug__in=EMPRESAS_FORMULARIOS_CLINICOS,
    )
    if preconsulta.estado == "completada":
        return render(request, "clinica/preconsulta_publica_finalizada.html", {"completada": True})
    if not preconsulta.vigente:
        return render(request, "clinica/preconsulta_publica_finalizada.html", {"completada": False}, status=410)

    form = PreconsultaClinicaPublicaForm(
        request.POST or None,
        request.FILES or None,
        instance=preconsulta,
        paciente=preconsulta.paciente,
        empresa=preconsulta.empresa,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            preconsulta = form.save(commit=False)
            preconsulta.datos_generales = form.datos_generales_limpios()
            preconsulta.estado = "completada"
            preconsulta.fecha_completada = timezone.now()
            preconsulta.ip_completada = _ip_cliente(request)
            preconsulta.save()
            _actualizar_paciente_desde_preconsulta(preconsulta.paciente, form)
        return render(request, "clinica/preconsulta_publica_finalizada.html", {"completada": True})

    return render(
        request,
        "clinica/preconsulta_publica.html",
        {"form": form, "preconsulta": preconsulta, "paciente": preconsulta.paciente},
    )


@never_cache
@sensitive_post_parameters()
def registro_paciente_publico(request, token):
    invitacion = get_object_or_404(
        InvitacionRegistroPaciente.objects.select_related("empresa", "paciente"),
        token_hash=hash_token_preconsulta(token),
        empresa__slug__in=EMPRESAS_FORMULARIOS_CLINICOS,
    )
    if invitacion.estado == "revocada" or invitacion.fecha_expiracion <= timezone.now():
        return render(
            request,
            "clinica/preconsulta_publica_finalizada.html",
            {"completada": False, "registro_nuevo": True},
            status=410,
        )

    preconsulta_base = PreconsultaClinica(
        empresa=invitacion.empresa,
        tipo="general",
        token_hash=invitacion.token_hash,
        token_preview=invitacion.token_preview,
        fecha_expiracion=invitacion.fecha_expiracion,
    )
    form = PreconsultaClinicaPublicaForm(
        request.POST or None,
        request.FILES or None,
        instance=preconsulta_base,
        empresa=invitacion.empresa,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            paciente = Paciente(
                empresa=invitacion.empresa,
                expediente_codigo=_proximo_codigo_expediente(invitacion.empresa),
                primer_nombre=form.cleaned_data.get("primer_nombre"),
                segundo_nombre=form.cleaned_data.get("segundo_nombre"),
                primer_apellido=form.cleaned_data.get("primer_apellido"),
                segundo_apellido=form.cleaned_data.get("segundo_apellido"),
                identidad=form.cleaned_data.get("identidad"),
                fecha_nacimiento=form.cleaned_data.get("fecha_nacimiento"),
                sexo=form.cleaned_data.get("sexo") or "no_indicado",
                estado_civil=form.cleaned_data.get("estado_civil") or "no_indicado",
                correo=form.cleaned_data.get("correo") or "",
                telefono=form.cleaned_data.get("telefono") or "",
                whatsapp=form.cleaned_data.get("telefono") or "",
                prefijo_telefono=form.cleaned_data.get("telefono_codigo_area") or "504",
                direccion=form.cleaned_data.get("direccion") or "",
                lugar_nacimiento=form.cleaned_data.get("lugar_nacimiento") or "",
                ocupacion=form.cleaned_data.get("ocupacion") or "",
                contacto_emergencia=form.cleaned_data.get("contacto_emergencia") or "",
                telefono_emergencia=form.cleaned_data.get("telefono_emergencia") or "",
                foto_perfil=form.cleaned_data.get("foto_perfil"),
                creado_por=invitacion.creada_por,
            )
            paciente.save()

            _preconsulta_token_raw, preconsulta_token_hash, preconsulta_token_preview = generar_token_preconsulta()
            preconsulta = form.save(commit=False)
            preconsulta.empresa = invitacion.empresa
            preconsulta.paciente = paciente
            preconsulta.tipo = "general"
            preconsulta.token_hash = preconsulta_token_hash
            preconsulta.token_preview = preconsulta_token_preview
            preconsulta.fecha_expiracion = invitacion.fecha_expiracion
            preconsulta.datos_generales = form.datos_generales_limpios()
            preconsulta.estado = "completada"
            preconsulta.fecha_completada = timezone.now()
            preconsulta.ip_completada = _ip_cliente(request)
            preconsulta.creada_por = invitacion.creada_por
            preconsulta.save()
            _actualizar_paciente_desde_preconsulta(paciente, form)

            if paciente.foto_perfil:
                PacienteFotoEvolucion.objects.create(
                    empresa=invitacion.empresa,
                    paciente=paciente,
                    imagen=paciente.foto_perfil,
                    tipo="ingreso",
                    titulo="Foto de ingreso",
                    descripcion="Fotografia adjuntada por el paciente durante su registro seguro.",
                    creado_por=invitacion.creada_por,
                )

            invitacion.fecha_completada = timezone.now()
            invitacion.ip_completada = _ip_cliente(request)
            invitacion.paciente = paciente
            invitacion.save(update_fields=[
                "fecha_completada",
                "ip_completada",
                "paciente",
            ])
        return render(
            request,
            "clinica/preconsulta_publica_finalizada.html",
            {"completada": True, "registro_nuevo": True},
        )

    return render(
        request,
        "clinica/preconsulta_publica.html",
        {
            "form": form,
            "preconsulta": preconsulta_base,
            "paciente": None,
            "registro_nuevo": True,
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
    return render(request, "clinica/form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": "Nueva cita clinica",
        "paciente_rapido_form": PacienteRapidoCitaForm(empresa=empresa),
    })


def crear_paciente_rapido_cita(request, empresa_slug):
    # La agenda y el módulo clínico comparten una sola validación y sincronización
    # para evitar expedientes o clientes de facturación con reglas diferentes.
    from crm.views import crear_paciente_rapido_cita as crear_paciente
    return crear_paciente(request, empresa_slug)


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
