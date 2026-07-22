import logging
import re
from datetime import datetime
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
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
    DIETA_CHOICES,
    DROGAS_RECREATIVAS_CHOICES,
    EJERCICIO_CHOICES,
    FRECUENCIA_CHOICES,
    MEDICAMENTOS_HABITUALES_CHOICES,
    MEDICAMENTOS_ACTUALES_CHOICES,
    MOTIVO_CATEGORIA_CHOICES,
    PROCEDIMIENTOS_GENERALES_CHOICES,
    RIESGO_TROMBOEMBOLICO_CHOICES,
    PSICOLOGICA_CHOICES,
    SI_NO_CHOICES,
    CitaClinicaForm,
    DocumentoClinicoPacienteForm,
    ExamenPacienteForm,
    ExpedienteEventoForm,
    HistoriaClinicaEspecialidadForm,
    IncapacidadClinicaForm,
    PacienteForm,
    PacienteFotoEvolucionForm,
    PlanConsentimientoPDFForm,
    PreconsultaClinicaPublicaForm,
    ProfesionalSaludForm,
    RecetaMedicaForm,
    ServicioClinicoForm,
    TratamientoPacienteForm,
)
from .models import (
    CitaClinica,
    ConsentimientoClinico,
    DocumentoClinicoPaciente,
    ExamenPaciente,
    ConfiguracionClinica,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    InvitacionRegistroPaciente,
    MedicamentoPrescrito,
    Paciente,
    PacienteFotoEvolucion,
    PreconsultaClinica,
    ProfesionalSalud,
    RecetaMedica,
    SeguimientoPostOperatorio,
    ServicioClinico,
    TratamientoPaciente,
    asegurar_profesionales_agenda_base,
)
from .tokens import generar_token_preconsulta, hash_token_preconsulta
from crm.forms import PacienteRapidoCitaForm
from crm.models import ConfiguracionCRM
from crm.services import WhatsAppAPIError, enviar_plantilla_preconsulta_whatsapp

logger = logging.getLogger(__name__)

DOCUMENTOS_CLINICOS_CONFIG = {
    "laboratorio": {
        "titulo": "Trabajos de laboratorio",
        "descripcion": "Resultados, solicitudes y archivos de laboratorio organizados por fecha.",
        "accion": "Subir trabajo de laboratorio",
        "estado": "Biblioteca de laboratorio",
    },
    "radiologico": {
        "titulo": "Estudios radiologicos",
        "descripcion": "Ordenes, ultrasonidos, imagenologia y estudios recibidos del paciente.",
        "accion": "Subir estudio radiologico",
        "estado": "Imagenologia y estudios",
    },
    "documento": {
        "titulo": "Documentos del paciente",
        "descripcion": "Adjuntos generales, referencias, documentos personales y archivos clinicos de soporte.",
        "accion": "Subir documento",
        "estado": "Archivo documental",
    },
    "remision": {
        "titulo": "Remision y contraremision",
        "descripcion": "Control de referencias externas, remisiones recibidas y contrarremisiones emitidas.",
        "accion": "Subir remision",
        "estado": "Referencias externas",
    },
    "detalle_remision": {
        "titulo": "Detalle de remisiones",
        "descripcion": "Seguimiento puntual de remisiones, respuestas, hallazgos y continuidad medica.",
        "accion": "Subir detalle",
        "estado": "Seguimiento de remisiones",
    },
    "incapacidad": {
        "titulo": "Incapacidades",
        "descripcion": "Certificados e incapacidades emitidas con formato imprimible y control por fecha.",
        "accion": "Crear incapacidad",
        "estado": "Certificados medicos",
    },
}


def _sincronizar_agenda_desde_cita_clinica(cita):
    if not cita.paciente_id:
        return None
    from crm.appointment_notifications import procesar_notificacion, programar_notificaciones_cita
    from crm.models import CitaCliente, NotificacionCitaWhatsApp

    estados = {
        "solicitada": "pendiente",
        "confirmada": "confirmada",
        "en_atencion": "pendiente",
        "completada": "realizada",
        "cancelada": "cancelada",
        "no_asistio": "cancelada",
    }
    titulo = cita.servicio.nombre if cita.servicio_id else cita.motivo
    es_recordatorio = bool(getattr(cita, "es_recordatorio_tratamiento", False))
    agenda, _ = CitaCliente.objects.get_or_create(
        empresa=cita.empresa,
        cita_clinica=cita,
        defaults={
            "paciente": cita.paciente,
            "servicio_clinico": cita.servicio,
            "profesional_salud": cita.profesional,
            "titulo": titulo,
            "responsable": cita.profesional.nombre if cita.profesional_id else "",
            "fecha_hora": cita.fecha_hora,
            "duracion_minutos": cita.servicio.duracion_minutos if cita.servicio_id else 60,
            "estado": estados.get(cita.estado, "pendiente"),
            "pagada": cita.pagada,
            "observacion": cita.observaciones or cita.motivo,
            "enviar_confirmacion_whatsapp": not es_recordatorio,
            "recordatorio_semana_whatsapp": True,
            "recordatorio_dia_whatsapp": True,
        },
    )
    agenda.paciente = cita.paciente
    agenda.servicio_clinico = cita.servicio
    agenda.profesional_salud = cita.profesional
    agenda.titulo = titulo
    agenda.responsable = cita.profesional.nombre if cita.profesional_id else ""
    agenda.fecha_hora = cita.fecha_hora
    agenda.duracion_minutos = cita.servicio.duracion_minutos if cita.servicio_id else agenda.duracion_minutos or 60
    agenda.estado = estados.get(cita.estado, "pendiente")
    agenda.pagada = cita.pagada
    agenda.observacion = cita.observaciones or cita.motivo
    agenda.enviar_confirmacion_whatsapp = not es_recordatorio
    agenda.recordatorio_semana_whatsapp = True
    agenda.recordatorio_dia_whatsapp = True
    agenda.save()

    notificaciones = programar_notificaciones_cita(agenda)
    if not es_recordatorio:
        confirmacion = next(
            (item for item in notificaciones if item.tipo == NotificacionCitaWhatsApp.TIPO_CONFIRMACION),
            None,
        )
        if confirmacion and confirmacion.estado != "enviado":
            procesar_notificacion(confirmacion.id)
    return agenda


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_clinica(empresa):
    return ConfiguracionClinica.objects.get_or_create(empresa=empresa)[0]


EMPRESAS_FORMULARIOS_CLINICOS = {"hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"}


def _requiere_hospital_mia(empresa):
    if empresa.slug not in EMPRESAS_FORMULARIOS_CLINICOS:
        raise Http404("Los formularios hospitalarios no estan habilitados para esta empresa.")


def _ip_cliente(request):
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def _etiquetas_seleccion(valores, choices):
    etiquetas = dict(choices)
    if isinstance(valores, str):
        valores = [valores]
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


def _valor_preconsulta(general, campo, choices=None):
    valor = general.get(campo)
    if valor in (None, "", []):
        return ""
    if choices:
        if not isinstance(valor, (list, tuple)):
            valor = [valor]
        return _etiquetas_seleccion(valor, choices)
    return valor


def _campo_lectura_preconsulta(general, etiqueta, campo, choices=None, tipo="texto"):
    valor = _valor_preconsulta(general, campo, choices)
    if valor in (None, "", []):
        return None
    return {"label": etiqueta, "value": valor, "type": tipo}


def _secciones_preconsulta(preconsulta):
    general = {}
    if isinstance(preconsulta.datos_generales, dict):
        general = preconsulta.datos_generales.get("formulario_general", {}) or {}
    secciones = [
        {
            "titulo": "1. Datos generales",
            "descripcion": "Identificación y contacto reportado por el paciente.",
            "campos": [
                _campo_lectura_preconsulta(general, "Nombre completo", "nombres"),
                _campo_lectura_preconsulta(general, "Apellidos", "apellidos"),
                _campo_lectura_preconsulta(general, "Identidad", "identidad"),
                _campo_lectura_preconsulta(general, "Fecha de nacimiento", "fecha_nacimiento"),
                _campo_lectura_preconsulta(general, "Sexo", "sexo"),
                _campo_lectura_preconsulta(general, "Estado civil", "estado_civil"),
                _campo_lectura_preconsulta(general, "Teléfono / WhatsApp", "telefono"),
                _campo_lectura_preconsulta(general, "Correo", "correo"),
                _campo_lectura_preconsulta(general, "Residencia", "direccion"),
                _campo_lectura_preconsulta(general, "Ocupación", "ocupacion"),
                _campo_lectura_preconsulta(general, "Lugar de trabajo", "lugar_trabajo"),
                _campo_lectura_preconsulta(general, "Contacto de emergencia", "contacto_emergencia_completo"),
                _campo_lectura_preconsulta(general, "Persona que proporciona la información", "informante"),
                _campo_lectura_preconsulta(general, "Nombre del informante", "informante_detalle"),
                _campo_lectura_preconsulta(general, "Cómo conoció la clínica", "referido_por"),
                _campo_lectura_preconsulta(general, "Referencia", "referido_por_detalle"),
            ],
        },
        {
            "titulo": "2. Motivo y procedimientos de interés",
            "descripcion": "Áreas seleccionadas y procedimientos que motivan la consulta.",
            "campos": [
                _campo_lectura_preconsulta(general, "Motivo principal", "motivo_categoria", MOTIVO_CATEGORIA_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Procedimientos seleccionados", "procedimientos_interes", PROCEDIMIENTOS_GENERALES_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Otros procedimientos", "procedimientos_interes_otros"),
                _campo_lectura_preconsulta(general, "Qué le gustaría mejorar", "historia_mejorar"),
                _campo_lectura_preconsulta(general, "Tiempo con esta preocupación", "historia_tiempo_preocupacion"),
                _campo_lectura_preconsulta(general, "Tratamientos previos", "historia_tratamientos_previos"),
                _campo_lectura_preconsulta(general, "Expectativas", "historia_expectativas"),
            ],
        },
        {
            "titulo": "3. Antecedentes, alergias y medicamentos",
            "descripcion": "Condiciones, alergias y tratamientos reportados.",
            "campos": [
                {"label": "Antecedentes personales", "value": _etiquetas_seleccion(preconsulta.antecedentes_personales, ANTECEDENTES_PERSONALES_CHOICES), "type": "chips"},
                _campo_lectura_preconsulta(general, "Detalle de antecedentes", "antecedentes_personales_detalle"),
                {"label": "Alergias marcadas", "value": _valor_preconsulta(general, "alergias_seleccion", ALERGIAS_GENERALES_CHOICES), "type": "chips"},
                _campo_lectura_preconsulta(general, "Detalle de alergias", "alergias_otras"),
                _campo_lectura_preconsulta(general, "Alergias declaradas", "alergias"),
                {"label": "Medicamentos habituales", "value": _etiquetas_seleccion(preconsulta.medicamentos_habituales, MEDICAMENTOS_HABITUALES_CHOICES), "type": "chips"},
                _campo_lectura_preconsulta(general, "Detalle medicamentos habituales", "medicamentos_habituales_detalle"),
                {"label": "Medicamentos actuales", "value": _valor_preconsulta(general, "medicamentos_actuales_seleccion", MEDICAMENTOS_ACTUALES_CHOICES), "type": "chips"},
                _campo_lectura_preconsulta(general, "Detalle medicamentos actuales", "medicamentos_actuales_otros"),
                _campo_lectura_preconsulta(general, "Antecedentes infecciosos", "antecedentes_infecciosos"),
                _campo_lectura_preconsulta(general, "Hospitalizaciones o cirugías previas", "antecedentes_hospitalarios_detalle"),
            ],
        },
        {
            "titulo": "4. Hábitos y estilo de vida",
            "descripcion": "Datos no patológicos y riesgos de consumo.",
            "campos": [
                _campo_lectura_preconsulta(general, "Operado anteriormente", "quirurgicos_operado", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Detalle quirúrgico", "quirurgicos_detalle"),
                _campo_lectura_preconsulta(general, "Tabaco", "tabaco_frecuencia", FRECUENCIA_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Alcohol", "alcohol_frecuencia", FRECUENCIA_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Drogas recreativas", "drogas_recreativas", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Tipo de drogas", "drogas_recreativas_tipos", DROGAS_RECREATIVAS_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Detalle consumo", "drogas_recreativas_detalle"),
                _campo_lectura_preconsulta(general, "Hábitos de riesgo", "consumo_riesgo", CONSUMO_RIESGO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Detalle hábitos", "consumo_riesgo_detalle"),
                _campo_lectura_preconsulta(general, "Dieta", "dieta"),
                _campo_lectura_preconsulta(general, "Ejercicio", "ejercicio"),
            ],
        },
        {
            "titulo": "5. Familiares, ginecología y riesgos",
            "descripcion": "Antecedentes familiares y datos ginecológicos cuando aplican.",
            "campos": [
                {"label": "Antecedentes familiares", "value": _etiquetas_seleccion(preconsulta.antecedentes_familiares, ANTECEDENTES_FAMILIARES_CHOICES), "type": "chips"},
                _campo_lectura_preconsulta(general, "Detalle familiares", "antecedentes_familiares_detalle"),
                _campo_lectura_preconsulta(general, "Riesgo tromboembólico", "riesgo_tromboembolico", RIESGO_TROMBOEMBOLICO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Detalle riesgo", "riesgo_tromboembolico_otros"),
                _campo_lectura_preconsulta(general, "Menarca", "gine_menarca"),
                _campo_lectura_preconsulta(general, "Gestas", "gine_gestas"),
                _campo_lectura_preconsulta(general, "Partos", "gine_partos"),
                _campo_lectura_preconsulta(general, "Cesáreas", "gine_cesareas"),
                _campo_lectura_preconsulta(general, "Abortos", "gine_abortos"),
                _campo_lectura_preconsulta(general, "Última menstruación", "gine_ultima_menstruacion"),
                _campo_lectura_preconsulta(general, "Embarazada", "gine_embarazada", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Lactancia", "gine_lactancia", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Mamografía / ultrasonido", "gine_mamografia", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Fecha mamografía / ultrasonido", "gine_mamografia_fecha"),
            ],
        },
        {
            "titulo": "6. Evaluación psicológica y consentimiento",
            "descripcion": "Expectativas y elementos emocionales declarados.",
            "campos": [
                _campo_lectura_preconsulta(general, "Evaluación psicológica", "evaluacion_psicologica", PSICOLOGICA_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Detalle emocional", "evaluacion_psicologica_detalle"),
                _campo_lectura_preconsulta(general, "Quién tomó la decisión de operarse", "decision_cirugia", DECISION_CIRUGIA_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Otra decisión", "decision_cirugia_otros"),
                _campo_lectura_preconsulta(general, "Expectativas realistas", "expectativas_realistas", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Busca perfección absoluta", "busca_perfeccion", SI_NO_CHOICES, "chips"),
                _campo_lectura_preconsulta(general, "Múltiples cirugías por insatisfacción", "multiples_cirugias_insatisfaccion", SI_NO_CHOICES, "chips"),
            ],
        },
    ]
    for seccion in secciones:
        seccion["campos"] = [
            campo for campo in seccion["campos"]
            if campo and campo.get("value") not in (None, "", [])
        ]
    return [seccion for seccion in secciones if seccion["campos"]]


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
    patron = re.compile(rf"^{re.escape(prefijo)}-(\d+)$", re.IGNORECASE)
    mayor = 0
    codigos = Paciente.objects.filter(
        empresa=empresa,
        expediente_codigo__istartswith=f"{prefijo}-",
    ).values_list("expediente_codigo", flat=True)
    for codigo in codigos:
        coincidencia = patron.match(codigo or "")
        if coincidencia:
            mayor = max(mayor, int(coincidencia.group(1)))
    siguiente = mayor + 1
    while Paciente.objects.filter(empresa=empresa, expediente_codigo=f"{prefijo}-{siguiente:05d}").exists():
        siguiente += 1
    return f"{prefijo}-{siguiente:05d}"


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
        "puede_eliminar_pacientes": _puede_eliminar_pacientes(request.user, empresa),
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
        try:
            with transaction.atomic():
                paciente = form.save(commit=False)
                paciente.empresa = empresa
                paciente.creado_por = request.user
                if (
                    not paciente.expediente_codigo
                    or Paciente.objects.filter(
                        empresa=empresa,
                        expediente_codigo=paciente.expediente_codigo,
                    ).exists()
                ):
                    paciente.expediente_codigo = _proximo_codigo_expediente(empresa)
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
        except ValidationError as exc:
            logger.warning(
                "No se pudo crear paciente en %s por validacion al sincronizar cliente: %s",
                empresa.slug,
                exc,
            )
            if hasattr(exc, "message_dict"):
                for campo, errores in exc.message_dict.items():
                    form.add_error(campo if campo in form.fields else None, errores)
            else:
                form.add_error(None, exc)
            messages.error(request, "Revisa los datos marcados. No se guardo el paciente para evitar un registro incompleto.")
        except Exception:
            logger.exception("Error inesperado al crear paciente en %s", empresa.slug)
            form.add_error(
                None,
                "No se pudo guardar el paciente por un error interno. Intenta nuevamente; si persiste, avisa a soporte.",
            )
            messages.error(request, "No se pudo crear el paciente. El formulario quedo listo para corregir o reintentar.")
        else:
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


def _puede_eliminar_pacientes(user, empresa):
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or getattr(user, "es_administrador_empresa", False))
        and user.puede_acceder_empresa(empresa)
    )


def _puede_administrar_catalogo_clinico(user, empresa):
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or getattr(user, "es_administrador_empresa", False))
        and user.puede_acceder_empresa(empresa)
    )


@login_required
@require_POST
def eliminar_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    if not _puede_eliminar_pacientes(request.user, empresa):
        messages.error(request, "Solo administradores de la empresa pueden eliminar pacientes.")
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)

    nombre = paciente.nombre
    paciente.delete()
    messages.success(request, f"Paciente {nombre} eliminado correctamente.")
    return redirect("clinica_pacientes", empresa_slug=empresa.slug)


@login_required
def paciente_detalle(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    asegurar_profesionales_agenda_base(empresa)
    eventos = paciente.eventos_expediente.select_related("profesional", "tratamiento")[:20]
    citas = paciente.citas.select_related("profesional", "servicio")[:10]
    recordatorios_tratamiento = (
        paciente.citas.filter(es_recordatorio_tratamiento=True)
        .select_related("profesional", "servicio")
        .order_by("fecha_hora")[:8]
    )
    tratamientos = paciente.tratamientos.select_related("profesional", "servicio")[:10]
    profesionales = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
    fotos_evolucion = paciente.fotos_evolucion.select_related("creado_por")[:12]
    medicamentos = MedicamentoPrescrito.objects.filter(empresa=empresa, paciente=paciente)[:10]
    consentimientos = ConsentimientoClinico.objects.filter(empresa=empresa, paciente=paciente)[:10]
    examenes = ExamenPaciente.objects.filter(empresa=empresa, paciente=paciente)[:10]
    recetas = RecetaMedica.objects.filter(empresa=empresa, paciente=paciente).select_related("profesional")[:10]
    documentos_clinicos_conteos = {
        item["categoria"]: item["total"]
        for item in DocumentoClinicoPaciente.objects.filter(empresa=empresa, paciente=paciente)
        .values("categoria")
        .annotate(total=Count("id"))
    }
    historias_especialidad = paciente.historias_especialidad.select_related("profesional", "actualizado_por")[:20]
    return render(
        request,
        "clinica/paciente_detalle.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "eventos": eventos,
            "citas": citas,
            "recordatorios_tratamiento": recordatorios_tratamiento,
            "tratamientos": tratamientos,
            "profesionales": profesionales,
            "fotos_evolucion": fotos_evolucion,
            "medicamentos": medicamentos,
            "consentimientos": consentimientos,
            "examenes": examenes,
            "recetas": recetas,
            "documentos_clinicos_conteos": documentos_clinicos_conteos,
            "historias_especialidad": historias_especialidad,
            "formularios_hospitalarios": empresa.slug in EMPRESAS_FORMULARIOS_CLINICOS,
            "puede_eliminar_pacientes": _puede_eliminar_pacientes(request.user, empresa),
        },
    )


@login_required
@require_POST
def crear_recordatorios_tratamiento(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    asegurar_profesionales_agenda_base(empresa)

    tratamientos = request.POST.getlist("tratamiento[]")
    fechas = request.POST.getlist("fecha[]")
    horas = request.POST.getlist("hora[]")
    periodos = request.POST.getlist("periodo[]")
    profesionales_ids = request.POST.getlist("profesional[]")
    notas = request.POST.getlist("nota[]")

    creadas = 0
    errores = []
    total_filas = max(len(tratamientos), len(fechas), len(horas), len(periodos), len(profesionales_ids), len(notas))
    zona = timezone.get_current_timezone()

    for index in range(total_filas):
        tratamiento = (tratamientos[index] if index < len(tratamientos) else "").strip()
        fecha_texto = (fechas[index] if index < len(fechas) else "").strip()
        hora_texto = (horas[index] if index < len(horas) else "").strip()
        periodo = (periodos[index] if index < len(periodos) else "").strip().upper()
        profesional_id = (profesionales_ids[index] if index < len(profesionales_ids) else "").strip()
        nota = (notas[index] if index < len(notas) else "").strip()

        if not any([tratamiento, fecha_texto, hora_texto, profesional_id, nota]):
            continue
        fila = index + 1
        if not tratamiento:
            errores.append(f"Fila {fila}: indique el tratamiento o seguimiento.")
            continue
        if not fecha_texto:
            errores.append(f"Fila {fila}: seleccione la fecha.")
            continue
        if not hora_texto or periodo not in {"AM", "PM"}:
            errores.append(f"Fila {fila}: seleccione hora y AM/PM.")
            continue

        profesional = None
        if profesional_id:
            profesional = ProfesionalSalud.objects.filter(id=profesional_id, empresa=empresa, activo=True).first()
            if not profesional:
                errores.append(f"Fila {fila}: el profesional seleccionado no esta disponible.")
                continue

        try:
            fecha = datetime.strptime(fecha_texto, "%Y-%m-%d").date()
            hora_12, minuto = (int(parte) for parte in hora_texto.split(":"))
            if hora_12 < 1 or hora_12 > 12 or minuto not in {0, 15, 30, 45}:
                raise ValueError
            hora_24 = hora_12 % 12 + (12 if periodo == "PM" else 0)
            fecha_hora = timezone.make_aware(datetime.combine(fecha, datetime.min.time()).replace(hour=hora_24, minute=minuto), zona)
        except ValueError:
            errores.append(f"Fila {fila}: fecha u hora invalida.")
            continue

        cita = CitaClinica.objects.create(
            empresa=empresa,
            paciente=paciente,
            profesional=profesional,
            fecha_hora=fecha_hora,
            estado="confirmada",
            canal="recepcion",
            motivo=f"Recordatorio: {tratamiento}",
            es_recordatorio_tratamiento=True,
            tratamiento_recordatorio=tratamiento,
            observaciones=nota or "Seguimiento programado desde expediente clinico.",
        )
        try:
            _sincronizar_agenda_desde_cita_clinica(cita)
        except Exception:
            logger.exception("No se pudo sincronizar recordatorio clinico %s", cita.id)
            messages.warning(request, "Uno de los recordatorios se guardo, pero no pudo sincronizarse con WhatsApp/agenda externa.")
        creadas += 1

    for error in errores:
        messages.error(request, error)
    if creadas:
        messages.success(request, f"{creadas} recordatorio(s) de tratamiento enviados al calendario.")
    elif not errores:
        messages.info(request, "No se agregaron recordatorios porque no habia filas completas.")

    return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.id)


@login_required
def examenes_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    examenes = list(
        ExamenPaciente.objects.filter(empresa=empresa, paciente=paciente)
        .select_related("subido_por")
        .order_by("-fecha_examen", "-fecha_creacion")
    )
    resumen = {
        "total": len(examenes),
        "pdf": sum(1 for item in examenes if not item.es_imagen),
        "imagenes": sum(1 for item in examenes if item.es_imagen),
    }
    return render(
        request,
        "clinica/examenes_paciente.html",
        {"empresa": empresa, "paciente": paciente, "examenes": examenes, "resumen": resumen},
    )


@login_required
def consentimientos_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    consentimientos = list(
        ConsentimientoClinico.objects.filter(empresa=empresa, paciente=paciente)
        .select_related("tratamiento", "cita")
        .order_by("-fecha_firma", "-fecha_creacion")
    )
    resumen = {
        "total": len(consentimientos),
        "firmados": sum(1 for item in consentimientos if item.estado == "firmado"),
        "pendientes": sum(1 for item in consentimientos if item.estado == "pendiente"),
        "revocados": sum(1 for item in consentimientos if item.estado == "revocado"),
        "con_pdf": sum(1 for item in consentimientos if item.archivo),
    }
    return render(
        request,
        "clinica/consentimientos_paciente.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "consentimientos": consentimientos,
            "resumen": resumen,
        },
    )


@login_required
def recetas_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    recetas = list(
        RecetaMedica.objects.filter(empresa=empresa, paciente=paciente)
        .select_related("profesional", "creada_por")
        .prefetch_related("productos")
        .order_by("-fecha", "-fecha_creacion")
    )
    return render(
        request,
        "clinica/recetas_paciente.html",
        {"empresa": empresa, "paciente": paciente, "recetas": recetas},
    )


@login_required
def imprimir_receta_paciente(request, empresa_slug, paciente_id, receta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    receta = get_object_or_404(
        RecetaMedica.objects.select_related("profesional", "creada_por").prefetch_related("productos"),
        id=receta_id,
        empresa=empresa,
        paciente=paciente,
    )
    return render(
        request,
        "clinica/receta_imprimir.html",
        {"empresa": empresa, "paciente": paciente, "receta": receta},
    )


def _config_documento_clinico(categoria):
    config = DOCUMENTOS_CLINICOS_CONFIG.get(categoria)
    if not config:
        raise Http404("Modulo documental no valido.")
    return config


@login_required
def documentos_clinicos_paciente(request, empresa_slug, paciente_id, categoria):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    config = _config_documento_clinico(categoria)
    documentos = list(
        DocumentoClinicoPaciente.objects.filter(empresa=empresa, paciente=paciente, categoria=categoria)
        .select_related("profesional", "creado_por")
        .order_by("-fecha_documento", "-fecha_creacion")
    )
    resumen = {
        "total": len(documentos),
        "pdf": sum(1 for item in documentos if item.archivo and item.es_pdf),
        "imagenes": sum(1 for item in documentos if item.archivo and item.es_imagen),
        "sin_adjunto": sum(1 for item in documentos if not item.archivo),
    }
    return render(
        request,
        "clinica/documentos_clinicos_paciente.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "categoria": categoria,
            "config": config,
            "documentos": documentos,
            "resumen": resumen,
        },
    )


@login_required
def subir_documento_clinico_paciente(request, empresa_slug, paciente_id, categoria):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    config = _config_documento_clinico(categoria)
    form_class = IncapacidadClinicaForm if categoria == "incapacidad" else DocumentoClinicoPacienteForm
    form_kwargs = {"empresa": empresa}
    if categoria != "incapacidad":
        form_kwargs["categoria"] = categoria
    form = form_class(request.POST or None, request.FILES or None, **form_kwargs)
    if request.method == "POST" and form.is_valid():
        documento = form.save(commit=False)
        documento.empresa = empresa
        documento.paciente = paciente
        documento.categoria = categoria
        documento.creado_por = request.user
        if categoria == "incapacidad" and not documento.titulo:
            documento.titulo = "Incapacidad medica"
        documento.save()
        messages.success(request, f"{config['titulo']} actualizado correctamente en el expediente.")
        return redirect(
            "clinica_documentos_categoria_paciente",
            empresa_slug=empresa.slug,
            paciente_id=paciente.id,
            categoria=categoria,
        )
    return render(
        request,
        "clinica/form.html",
        {
            "empresa": empresa,
            "form": form,
            "titulo": f"{config['accion']}: {paciente.nombre}",
            "cancel_url": reverse("clinica_documentos_categoria_paciente", args=[empresa.slug, paciente.id, categoria]),
        },
    )


@login_required
def imprimir_incapacidad_paciente(request, empresa_slug, paciente_id, documento_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    incapacidad = get_object_or_404(
        DocumentoClinicoPaciente.objects.select_related("profesional", "creado_por"),
        id=documento_id,
        empresa=empresa,
        paciente=paciente,
        categoria="incapacidad",
    )
    return render(
        request,
        "clinica/incapacidad_imprimir.html",
        {"empresa": empresa, "paciente": paciente, "incapacidad": incapacidad},
    )


@login_required
def evolucion_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    registros = list(
        PacienteFotoEvolucion.objects.filter(empresa=empresa, paciente=paciente)
        .select_related("creado_por")
        .order_by("fecha", "id")
    )
    fotos = [registro for registro in registros if registro.imagen]
    videos = [registro for registro in registros if registro.video]
    resumen = {
        "total": len(registros),
        "fotos": len(fotos),
        "videos": len(videos),
        "ultimo": registros[-1] if registros else None,
    }
    return render(
        request,
        "clinica/evolucion_paciente.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "registros": registros,
            "fotos": fotos,
            "videos": videos,
            "resumen": resumen,
        },
    )


@login_required
def historias_especialidad(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    historias = paciente.historias_especialidad.select_related("profesional", "actualizado_por")
    preconsultas = paciente.preconsultas.filter(estado="completada").select_related("creada_por")[:30]
    tipos = [
        {
            "codigo": codigo,
            "nombre": nombre,
            "total": historias.filter(tipo=codigo).count(),
            "preconsultas": paciente.preconsultas.filter(tipo=codigo, estado="completada").count(),
            "ultima_preconsulta": paciente.preconsultas.filter(tipo=codigo, estado="completada").order_by("-fecha_completada", "-fecha_creacion").first(),
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
def historial_clinico_consolidado(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    preconsultas = list(
        paciente.preconsultas.filter(estado="completada")
        .select_related("creada_por")
        .order_by("-fecha_completada", "-fecha_creacion")
    )
    historias = list(
        paciente.historias_especialidad.select_related("profesional", "actualizado_por")
        .order_by("-fecha_atencion", "-id")
    )
    bloques_preconsulta = [
        {
            "preconsulta": preconsulta,
            "secciones": _secciones_preconsulta(preconsulta),
        }
        for preconsulta in preconsultas
    ]
    tipos = [
        {
            "codigo": codigo,
            "nombre": nombre,
            "historias": [historia for historia in historias if historia.tipo == codigo],
            "preconsultas": [preconsulta for preconsulta in preconsultas if preconsulta.tipo == codigo],
        }
        for codigo, nombre in HistoriaClinicaEspecialidad.TIPO_CHOICES
    ]
    return render(
        request,
        "clinica/historial_clinico_consolidado.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "tipos": tipos,
            "historias": historias,
            "preconsultas": preconsultas,
            "bloques_preconsulta": bloques_preconsulta,
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
    return _render_preconsulta_enlace(
        request,
        empresa=empresa,
        paciente=paciente,
        preconsulta=preconsulta,
        enlace_publico=enlace_publico,
        whatsapp_url=whatsapp_url,
        tipo_nombre=tipos_validos[tipo],
    )


def _render_preconsulta_enlace(request, *, empresa, paciente, preconsulta, enlace_publico, whatsapp_url, tipo_nombre):
    return render(
        request,
        "clinica/preconsulta_enlace.html",
        {
            "empresa": empresa,
            "paciente": paciente,
            "preconsulta": preconsulta,
            "enlace_publico": enlace_publico,
            "whatsapp_url": whatsapp_url,
            "tipo_nombre": tipo_nombre,
        },
    )


@login_required
@require_POST
def enviar_preconsulta_whatsapp(request, empresa_slug, paciente_id, preconsulta_id):
    empresa = _empresa_desde_slug(empresa_slug)
    _requiere_hospital_mia(empresa)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    preconsulta = get_object_or_404(
        PreconsultaClinica,
        id=preconsulta_id,
        empresa=empresa,
        paciente=paciente,
    )
    tipos_validos = dict(PreconsultaClinica.TIPO_CHOICES)
    tipo_nombre = tipos_validos.get(preconsulta.tipo, preconsulta.get_tipo_display())
    enlace_publico = (request.POST.get("enlace_publico") or "").strip()
    telefono = paciente.whatsapp or paciente.telefono or ""
    mensaje = quote(
        f"Hola {paciente.primer_nombre or paciente.nombre}. {empresa.nombre} le comparte su formulario de preconsulta "
        f"de {tipo_nombre}. "
        f"Complete la informacion antes de su cita en este enlace seguro: {enlace_publico}"
    )
    telefono_limpio = "".join(c for c in telefono if c.isdigit())
    if len(telefono_limpio) == 8:
        telefono_limpio = "504" + telefono_limpio
    whatsapp_url = f"https://wa.me/{telefono_limpio}?text={mensaje}" if telefono_limpio else ""

    try:
        if not enlace_publico:
            raise WhatsAppAPIError("No se encontro el enlace seguro de preconsulta para enviar.")
        config = ConfiguracionCRM.objects.filter(empresa=empresa).first()
        if not config or not config.whatsapp_activo:
            raise WhatsAppAPIError("WhatsApp API no esta activo en CRM para esta empresa.")
        enviar_plantilla_preconsulta_whatsapp(
            config,
            telefono,
            paciente=paciente.nombre,
            tipo_preconsulta=tipo_nombre,
            enlace=enlace_publico,
        )
        messages.success(request, f"Preconsulta enviada por WhatsApp a {paciente.nombre}.")
    except WhatsAppAPIError as exc:
        messages.error(request, f"No se pudo enviar directo por WhatsApp: {exc}")

    return _render_preconsulta_enlace(
        request,
        empresa=empresa,
        paciente=paciente,
        preconsulta=preconsulta,
        enlace_publico=enlace_publico,
        whatsapp_url=whatsapp_url,
        tipo_nombre=tipo_nombre,
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
            "secciones_preconsulta": _secciones_preconsulta(preconsulta),
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
        try:
            _sincronizar_agenda_desde_cita_clinica(cita)
        except Exception:
            messages.warning(request, "La cita se guardo, pero no se pudo sincronizar WhatsApp/agenda en este momento.")
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
def registrar_foto_evolucion(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = PacienteFotoEvolucionForm(request.POST or None, request.FILES or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        foto = form.save(commit=False)
        foto.empresa = empresa
        foto.paciente = paciente
        foto.creado_por = request.user
        foto.save()
        tipo_archivo = "Video" if foto.video else "Foto"
        messages.success(request, f"{tipo_archivo} de evolucion registrada correctamente.")
        return redirect("clinica_evolucion_paciente", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Registrar evolucion: {paciente.nombre}",
        "cancel_url": reverse("clinica_evolucion_paciente", args=[empresa.slug, paciente.id]),
    })


@login_required
def subir_consentimiento_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = PlanConsentimientoPDFForm(request.POST or None, request.FILES or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        consentimiento = form.save(commit=False)
        consentimiento.empresa = empresa
        consentimiento.paciente = paciente
        consentimiento.contenido = consentimiento.contenido or "Plan de consentimiento firmado cargado en PDF."
        consentimiento.save()
        messages.success(request, "Plan de consentimiento PDF agregado correctamente.")
        return redirect("clinica_consentimientos_paciente", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Subir plan de consentimiento: {paciente.nombre}",
        "cancel_url": reverse("clinica_consentimientos_paciente", args=[empresa.slug, paciente.id]),
    })


@login_required
def subir_examen_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = ExamenPacienteForm(request.POST or None, request.FILES or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        examen = form.save(commit=False)
        examen.empresa = empresa
        examen.paciente = paciente
        examen.subido_por = request.user
        examen.save()
        messages.success(request, "Examen agregado correctamente al expediente.")
        return redirect("clinica_examenes_paciente", empresa_slug=empresa.slug, paciente_id=paciente.id)
    return render(request, "clinica/form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Subir examen: {paciente.nombre}",
        "cancel_url": reverse("clinica_examenes_paciente", args=[empresa.slug, paciente.id]),
    })


@login_required
def crear_receta_paciente(request, empresa_slug, paciente_id):
    empresa = _empresa_desde_slug(empresa_slug)
    paciente = get_object_or_404(Paciente, id=paciente_id, empresa=empresa)
    form = RecetaMedicaForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        receta = form.save(commit=False)
        receta.empresa = empresa
        receta.paciente = paciente
        receta.creada_por = request.user
        receta.save()
        form.save_m2m()
        messages.success(request, "Receta medica creada correctamente.")
        return redirect("clinica_receta_imprimir", empresa_slug=empresa.slug, paciente_id=paciente.id, receta_id=receta.id)
    return render(request, "clinica/form.html", {
        "empresa": empresa,
        "form": form,
        "titulo": f"Nueva receta: {paciente.nombre}",
        "cancel_url": reverse("clinica_recetas_paciente", args=[empresa.slug, paciente.id]),
    })


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
    return render(
        request,
        "clinica/catalogo.html",
        {
            "empresa": empresa,
            "form": form,
            "items": profesionales_qs,
            "titulo": "Profesionales",
            "form_titulo": "Agregar",
            "form_descripcion": "Configura este registro para agenda, tratamientos y operacion clinica.",
            "puede_editar_catalogo": _puede_administrar_catalogo_clinico(request.user, empresa),
            "editar_url_name": "clinica_profesional_editar",
        },
    )


@login_required
def editar_profesional(request, empresa_slug, profesional_id):
    empresa = _empresa_desde_slug(empresa_slug)
    profesional = get_object_or_404(ProfesionalSalud, id=profesional_id, empresa=empresa)
    if not _puede_administrar_catalogo_clinico(request.user, empresa):
        messages.error(request, "Solo administradores de la empresa pueden editar profesionales.")
        return redirect("clinica_profesionales", empresa_slug=empresa.slug)

    form = ProfesionalSaludForm(request.POST or None, empresa=empresa, instance=profesional)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profesional actualizado correctamente.")
        return redirect("clinica_profesionales", empresa_slug=empresa.slug)

    profesionales_qs = ProfesionalSalud.objects.filter(empresa=empresa)
    return render(
        request,
        "clinica/catalogo.html",
        {
            "empresa": empresa,
            "form": form,
            "items": profesionales_qs,
            "titulo": "Profesionales",
            "form_titulo": "Editar registro",
            "form_descripcion": "Actualiza el nombre, especialidad, telefono o estado del profesional seleccionado.",
            "puede_editar_catalogo": True,
            "editar_url_name": "clinica_profesional_editar",
            "objeto_editando": profesional,
        },
    )


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
