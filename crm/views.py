import calendar
from datetime import date, datetime, timedelta
import logging
import re
import unicodedata
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db.models import Count, Q
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST, require_http_methods

from core.access import EMPRESAS_INTERFAZ_CLINICA_GLOBAL
from core.models import Empresa
from contabilidad.models import CuentaFinanciera
from contabilidad.services import asegurar_cuentas_financieras_base_honduras
from facturacion.models import BodegaInventario, Cliente, PagoFactura, Producto, TipoImpuesto
from clinica.models import CitaClinica, Paciente, PacienteFotoEvolucion, PreconsultaClinica, ProfesionalSalud, ServicioClinico

from .forms import (
    CampaniaMarketingForm,
    CitaClienteForm,
    ConfiguracionCRMForm,
    PacienteRapidoCitaForm,
    PlantillaMensajeForm,
    ProgramaCamaraHiperbaricaForm,
    SesionCamaraHiperbaricaForm,
)
from .models import (
    CampaniaMarketing,
    CitaCirugiaFoto,
    CitaCliente,
    ConfiguracionCRM,
    EnvioCampania,
    OpcionServicioAgenda,
    PlantillaMensaje,
    ProgramaCamaraHiperbarica,
    SesionCamaraHiperbarica,
)
from .appointment_notifications import procesar_notificacion, programar_notificaciones_cita
from .models import NotificacionCitaWhatsApp
from .services import (
    WhatsAppAPIError,
    enviar_imagen_whatsapp,
    enviar_mensaje_whatsapp_texto,
    enviar_plantilla_cita_whatsapp,
    enviar_plantilla_marketing_whatsapp,
    enviar_plantilla_whatsapp,
    subir_media_whatsapp,
)
from .tokens import leer_token_respuesta_cita


logger = logging.getLogger(__name__)

AGENDA_ESPEJO_SERVICIOSMEDICOS = {
    "origen": "hospital_mia",
    "profesional_tokens": ("luis", "gonz"),
}
EMPRESAS_AGENDA_CENTRAL_HOSPITAL_MIA = frozenset({
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
})


def _normalizar_texto_agenda(valor):
    texto = unicodedata.normalize("NFKD", valor or "")
    return "".join(caracter for caracter in texto if not unicodedata.combining(caracter)).lower()


def _empresa_origen_agenda(empresa):
    if empresa.slug == "serviciosmedicos":
        origen = Empresa.objects.filter(slug=AGENDA_ESPEJO_SERVICIOSMEDICOS["origen"]).first()
        if origen:
            return origen
    return empresa


def _empresa_origen_agenda_mobile(empresa):
    if empresa.slug in EMPRESAS_AGENDA_CENTRAL_HOSPITAL_MIA:
        origen = Empresa.objects.filter(slug=AGENDA_ESPEJO_SERVICIOSMEDICOS["origen"]).first()
        if origen:
            return origen
    return empresa


def _cita_pertenece_agenda_espejo(cita, empresa):
    if empresa.slug != "serviciosmedicos":
        return True
    nombre = _normalizar_texto_agenda(getattr(cita.profesional_salud, "nombre", "") or cita.responsable)
    return all(token in nombre for token in AGENDA_ESPEJO_SERVICIOSMEDICOS["profesional_tokens"])


def _profesional_pertenece_agenda_espejo(profesional, empresa):
    if empresa.slug != "serviciosmedicos":
        return True
    nombre = _normalizar_texto_agenda(getattr(profesional, "nombre", ""))
    return all(token in nombre for token in AGENDA_ESPEJO_SERVICIOSMEDICOS["profesional_tokens"])


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _proteger_agenda_mobile(request, empresa):
    if not request.user.is_authenticated:
        login_url = reverse("empresa_login", args=[empresa.slug])
        return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")
    if not request.user.puede_acceder_empresa(empresa):
        return HttpResponse("Acceso no autorizado.", status=403)
    tiene_acceso_app = (
        request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_alguna_permision_facturacion_empresa(empresa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa)
    )
    if not tiene_acceso_app:
        return HttpResponse("Tu usuario no tiene permiso para usar la app movil de esta empresa.", status=403)
    return None


def _configuracion_crm(empresa):
    return ConfiguracionCRM.objects.get_or_create(empresa=empresa)[0]


def _asegurar_pacientes_empresas_clinicas(empresa):
    if empresa.slug not in EMPRESAS_INTERFAZ_CLINICA_GLOBAL:
        return
    from clinica.services_pacientes import asegurar_paciente_desde_cliente

    clientes_sin_paciente = (
        Cliente.objects.filter(empresa=empresa, activo=True)
        .exclude(nombre__iexact="Consumidor final")
        .filter(pacientes_clinicos__isnull=True)
        .distinct()
    )
    for cliente in clientes_sin_paciente.iterator():
        asegurar_paciente_desde_cliente(cliente)


def _fecha_agenda(valor):
    try:
        return date.fromisoformat(valor or "")
    except ValueError:
        return timezone.localdate()


def _es_cita_camara_hiperbarica(cita):
    texto = _normalizar_texto_agenda(cita.display_servicio)
    return "camara" in texto and "hiperbar" in texto


def _contexto_control_camara_hyperbarica(
    empresa,
    request,
    fecha_seleccionada,
    *,
    cita_control_id=None,
):
    if empresa.slug != "hospital_mia":
        return {}

    citas_del_dia = (
        CitaCliente.objects.filter(
            empresa=empresa,
            fecha_hora__date=fecha_seleccionada,
            paciente__isnull=False,
        )
        .select_related("paciente", "servicio_clinico", "profesional_salud")
        .order_by("fecha_hora")
    )
    citas_camara = [cita for cita in citas_del_dia if _es_cita_camara_hiperbarica(cita)]

    cita_control = None
    control_id = str(cita_control_id or request.GET.get("control_camara") or "").strip()
    if control_id:
        try:
            cita_control = next((cita for cita in citas_camara if cita.id == int(control_id)), None)
        except (TypeError, ValueError):
            cita_control = None

    programa = None
    sesion = None
    historial = []
    programa_form = None
    sesion_form = None
    if cita_control:
        sesion = (
            SesionCamaraHiperbarica.objects.filter(cita=cita_control)
            .select_related("programa", "creado_por", "actualizado_por")
            .first()
        )
        programa = sesion.programa if sesion else (
            ProgramaCamaraHiperbarica.objects.filter(
                empresa=empresa,
                paciente=cita_control.paciente,
                activo=True,
            ).first()
        )
        if programa:
            historial = list(
                programa.sesiones.select_related("cita", "creado_por", "actualizado_por").order_by("numero_sesion")
            )
        numeros_usados = {registro.numero_sesion for registro in historial}
        sugerido = cita_control.sesion_servicio if 1 <= (cita_control.sesion_servicio or 0) <= 22 else None
        if sugerido in numeros_usados and (not sesion or sesion.numero_sesion != sugerido):
            sugerido = None
        if not sugerido:
            sugerido = next((numero for numero in range(1, 23) if numero not in numeros_usados), 22)
        numero_desde_cita = (
            cita_control.sesion_servicio
            if 1 <= (cita_control.sesion_servicio or 0) <= 22
            else None
        )
        programa_form = ProgramaCamaraHiperbaricaForm(instance=programa)
        sesion_form = SesionCamaraHiperbaricaForm(
            instance=sesion,
            initial={"numero_sesion": sesion.numero_sesion if sesion else sugerido},
            bloqueada=bool(sesion and sesion.bloqueada),
        )
    else:
        numero_desde_cita = None

    numero_actual = None
    if cita_control and not (sesion and sesion.estado == "finalizada"):
        numero_actual = sesion.numero_sesion if sesion else (numero_desde_cita or sugerido)

    contexto = {
        "citas_camara_hiperbarica": citas_camara,
        "cita_control_camara": cita_control,
        "programa_camara": programa,
        "programa_camara_form": programa_form,
        "sesion_camara": sesion,
        "sesion_camara_form": sesion_form,
        "numero_sesion_desde_cita": numero_desde_cita,
        "historial_camara": historial,
        "tablero_sesiones_camara": [
            {
                "numero": numero,
                "registro": next((item for item in historial if item.numero_sesion == numero), None),
                "actual": numero == numero_actual,
            }
            for numero in range(1, 23)
        ],
        "sesiones_camara_completadas": sum(1 for item in historial if item.estado == "finalizada"),
    }
    return contexto


def _contexto_calendario(
    empresa,
    request,
    form,
    *,
    modo_agenda=False,
    vista_predeterminada="mes",
    empresa_agenda=None,
):
    empresa_agenda = empresa_agenda or _empresa_origen_agenda(empresa)
    es_clinica = bool(
        empresa_agenda.tipo_solucion == "clinica"
        or empresa_agenda.tiene_modulo_activo("clinica_medica")
    )
    agenda_espejo = empresa_agenda.id != empresa.id
    vista = request.GET.get("vista", vista_predeterminada)
    if vista not in {"mes", "semana", "dia", "anio", "agenda", "proximas"}:
        vista = "mes"
    seleccionada = _fecha_agenda(request.GET.get("fecha"))
    filtro_servicio = (request.GET.get("servicio") or "").strip()
    filtro_profesional = (request.GET.get("profesional") or "").strip()
    filtro_estado = (request.GET.get("estado") or "").strip()
    paciente_historial_id = (request.GET.get("paciente_historial") or "").strip()
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    if vista == "proximas":
        inicio = max(seleccionada, timezone.localdate())
        fin = inicio + timedelta(days=60)
        anterior, siguiente = inicio - timedelta(days=30), inicio + timedelta(days=30)
        titulo_periodo = "Próximas citas"
    elif vista == "agenda":
        inicio = seleccionada
        fin = inicio + timedelta(days=30)
        anterior, siguiente = inicio - timedelta(days=30), inicio + timedelta(days=30)
        titulo_periodo = f"Agenda desde {inicio:%d/%m/%Y}"
    elif vista == "anio":
        inicio = date(seleccionada.year, 1, 1)
        fin = date(seleccionada.year, 12, 31)
        anterior = date(seleccionada.year - 1, seleccionada.month, min(seleccionada.day, 28))
        siguiente = date(seleccionada.year + 1, seleccionada.month, min(seleccionada.day, 28))
        titulo_periodo = str(seleccionada.year)
    elif vista == "mes":
        inicio = seleccionada.replace(day=1)
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        anterior = (inicio - timedelta(days=1)).replace(day=1)
        siguiente = fin + timedelta(days=1)
        titulo_periodo = f"{meses[seleccionada.month]} {seleccionada.year}"
    elif vista == "semana":
        inicio = seleccionada - timedelta(days=seleccionada.weekday())
        fin = inicio + timedelta(days=6)
        anterior, siguiente = inicio - timedelta(days=7), inicio + timedelta(days=7)
        titulo_periodo = f"{inicio:%d/%m/%Y} — {fin:%d/%m/%Y}"
    else:
        inicio = fin = seleccionada
        anterior, siguiente = seleccionada - timedelta(days=1), seleccionada + timedelta(days=1)
        titulo_periodo = seleccionada.strftime("%d/%m/%Y")

    citas_qs = (
        CitaCliente.objects.filter(
            empresa=empresa_agenda, fecha_hora__date__gte=inicio, fecha_hora__date__lte=fin
        )
        .select_related("cliente", "producto", "paciente", "servicio_clinico", "profesional_salud")
        .prefetch_related("fotos_cirugia")
        .order_by("fecha_hora")
    )
    if agenda_espejo and empresa.slug == "serviciosmedicos":
        citas_qs = citas_qs.filter(Q(profesional_salud__nombre__icontains="Luis") | Q(responsable__icontains="Luis"))
    if filtro_servicio:
        try:
            citas_qs = citas_qs.filter(servicio_clinico_id=int(filtro_servicio))
        except (TypeError, ValueError):
            filtro_servicio = ""
    if filtro_profesional:
        try:
            citas_qs = citas_qs.filter(profesional_salud_id=int(filtro_profesional))
        except (TypeError, ValueError):
            filtro_profesional = ""
    if filtro_estado:
        if filtro_estado in dict(CitaCliente.ESTADO_CHOICES):
            citas_qs = citas_qs.filter(estado=filtro_estado)
        else:
            filtro_estado = ""
    if paciente_historial_id:
        try:
            citas_qs = citas_qs.filter(paciente_id=int(paciente_historial_id))
        except (TypeError, ValueError):
            paciente_historial_id = ""
    citas = [cita for cita in citas_qs if _cita_pertenece_agenda_espejo(cita, empresa)]

    filtros_query = urlencode({
        clave: valor
        for clave, valor in {
            "servicio": filtro_servicio,
            "profesional": filtro_profesional,
            "estado": filtro_estado,
            "paciente_historial": paciente_historial_id,
        }.items()
        if valor
    })
    filtros_query = f"&{filtros_query}" if filtros_query else ""
    filtros_activos = bool(filtro_servicio or filtro_profesional or filtro_estado)
    por_fecha = {}
    for cita in citas:
        clave = timezone.localtime(cita.fecha_hora).date()
        por_fecha.setdefault(clave, []).append(cita)

    semanas = []
    if vista == "mes":
        calendario = calendar.Calendar(firstweekday=0)
        for semana in calendario.monthdatescalendar(seleccionada.year, seleccionada.month):
            semanas.append([
                {"fecha": dia, "es_mes": dia.month == seleccionada.month, "es_hoy": dia == timezone.localdate(), "citas": por_fecha.get(dia, [])}
                for dia in semana
            ])
    meses_agenda = []
    if vista == "anio":
        for numero_mes in range(1, 13):
            primer_dia = date(seleccionada.year, numero_mes, 1)
            citas_mes = [
                cita for cita in citas
                if timezone.localtime(cita.fecha_hora).date().month == numero_mes
            ]
            meses_agenda.append({
                "fecha": primer_dia,
                "nombre": meses[numero_mes],
                "total": len(citas_mes),
                "citas": citas_mes[:4],
            })
    dias = [
        {"fecha": dia, "es_hoy": dia == timezone.localdate(), "citas": por_fecha.get(dia, [])}
        for dia in (inicio + timedelta(days=i) for i in range((fin - inicio).days + 1))
    ]
    paciente_busqueda_inicial = None
    pacientes_busqueda = []
    cliente_busqueda_inicial = None
    clientes_busqueda = []
    paciente_id_inicial = form["paciente"].value() if es_clinica and "paciente" in form.fields else None
    cliente_id_inicial = form["cliente"].value() if not es_clinica and "cliente" in form.fields else None
    if es_clinica and "paciente" in form.fields:
        pacientes_busqueda = [
            {
                "id": paciente.id,
                "nombre": paciente.nombre,
                "documento": paciente.identidad or "",
                "expediente": paciente.expediente_codigo,
                "telefono": paciente.whatsapp or paciente.telefono or "",
                "correo": paciente.correo or "",
            }
            for paciente in form.fields["paciente"].queryset
        ]
    if not es_clinica and "cliente" in form.fields:
        clientes_busqueda = [
            {
                "id": cliente.id,
                "nombre": cliente.nombre,
                "documento": cliente.rtn or "",
                "expediente": "",
                "telefono": cliente.telefono_whatsapp or cliente.telefono or "",
                "correo": cliente.correo or "",
            }
            for cliente in form.fields["cliente"].queryset
        ]

    paciente_historial = None
    citas_historial_futuras = []
    citas_historial_pasadas = []
    if es_clinica and paciente_historial_id:
        try:
            paciente_historial = Paciente.objects.filter(
                empresa=empresa_agenda,
                activo=True,
                id=int(paciente_historial_id),
            ).first()
        except (TypeError, ValueError):
            paciente_historial_id = ""
            paciente_historial = None
    if paciente_historial:
        ahora_historial = timezone.now()
        citas_historial_qs = (
            CitaCliente.objects.filter(empresa=empresa_agenda, paciente=paciente_historial)
            .select_related("paciente", "cliente", "servicio_clinico", "producto", "profesional_salud")
            .prefetch_related("fotos_cirugia")
            .order_by("fecha_hora")
        )
        citas_historial = [
            cita for cita in citas_historial_qs
            if _cita_pertenece_agenda_espejo(cita, empresa)
        ]
        citas_historial_futuras = [cita for cita in citas_historial if cita.fecha_hora >= ahora_historial][:25]
        citas_historial_pasadas = [cita for cita in reversed(citas_historial) if cita.fecha_hora < ahora_historial][:25]
    if paciente_id_inicial:
        try:
            paciente_busqueda_inicial = Paciente.objects.filter(
                empresa=empresa_agenda,
                id=paciente_id_inicial,
            ).first()
        except (TypeError, ValueError):
            paciente_busqueda_inicial = None
    if cliente_id_inicial:
        try:
            cliente_busqueda_inicial = Cliente.objects.filter(
                empresa=empresa,
                id=cliente_id_inicial,
            ).first()
        except (TypeError, ValueError):
            cliente_busqueda_inicial = None
    servicios_clinicos_meta = []
    servicios_filtro = []
    profesionales_filtro = []
    if es_clinica:
        servicios_filtro = list(ServicioClinico.objects.filter(empresa=empresa_agenda, activo=True).order_by("nombre"))
        servicios_clinicos_meta = [
            {"id": servicio.id, "nombre": servicio.nombre, "categoria": servicio.categoria, "color_calendario": servicio.color_calendario}
            for servicio in servicios_filtro
        ]
        profesionales_filtro = list(ProfesionalSalud.objects.filter(empresa=empresa_agenda, activo=True).order_by("nombre"))
        if agenda_espejo:
            profesionales_filtro = [
                profesional for profesional in profesionales_filtro
                if _profesional_pertenece_agenda_espejo(profesional, empresa)
            ]

    contexto = {
        "empresa": empresa, "form": form, "citas": citas, "modo_agenda": modo_agenda,
        "agenda_empresa": empresa_agenda,
        "agenda_espejo": agenda_espejo,
        "vista": vista, "fecha_seleccionada": seleccionada, "titulo_periodo": titulo_periodo,
        "fecha_anterior": anterior, "fecha_siguiente": siguiente, "semanas": semanas, "dias": dias,
        "meses_agenda": meses_agenda,
        "cita_editando": getattr(form, "instance", None) if getattr(form, "instance", None) and form.instance.pk else None,
        "estados_cita": CitaCliente.ESTADO_CHOICES,
        "es_clinica": es_clinica,
        "es_hospital_mia": empresa.slug in CitaClienteForm.EMPRESAS_WHATSAPP_CITAS,
        "permite_crear_tipo_consulta": empresa.slug == "hospital_mia",
        "detalles_agenda_hospital_mia": empresa.slug == "hospital_mia",
        "opciones_tratamiento_agenda": list(
            OpcionServicioAgenda.objects.filter(
                empresa=empresa, categoria="tratamientos", activo=True
            ).order_by("orden", "nombre")
        ) if empresa.slug == "hospital_mia" else [],
        "paciente_rapido_form": PacienteRapidoCitaForm(empresa=empresa_agenda) if es_clinica else None,
        "paciente_busqueda_inicial": paciente_busqueda_inicial,
        "pacientes_busqueda": pacientes_busqueda,
        "cliente_busqueda_inicial": cliente_busqueda_inicial,
        "clientes_busqueda": clientes_busqueda,
        "agenda_contactos_busqueda": pacientes_busqueda if es_clinica else clientes_busqueda,
        "agenda_cirugia_extendida": empresa.slug in CitaClienteForm.EMPRESAS_CIRUGIA_EXTENDIDA,
        "servicios_clinicos_meta": servicios_clinicos_meta,
        "servicios_filtro": servicios_filtro,
        "profesionales_filtro": profesionales_filtro,
        "filtro_servicio": filtro_servicio,
        "filtro_profesional": filtro_profesional,
        "filtro_estado": filtro_estado,
        "filtros_query": filtros_query,
        "filtros_activos": filtros_activos,
        "paciente_historial": paciente_historial,
        "paciente_historial_id": paciente_historial_id,
        "citas_historial_futuras": citas_historial_futuras,
        "citas_historial_pasadas": citas_historial_pasadas,
    }
    return contexto


def _guardar_cita_formulario(request, empresa, form, objeto=None):
    detalles = form.cleaned_data.get("detalles_agenda_limpios") or []
    if objeto and len(detalles) > 1:
        raise ValueError("Al editar una cita solo puede conservarse una opción por registro.")
    with transaction.atomic():
        cita_base = form.save(commit=False)
        cita_base.empresa = empresa
        if empresa.slug in CitaClienteForm.EMPRESAS_WHATSAPP_CITAS:
            cita_base.enviar_confirmacion_whatsapp = True
            cita_base.recordatorio_semana_whatsapp = True
            cita_base.recordatorio_dia_whatsapp = True
        grupo = cita_base.grupo_atencion or (uuid.uuid4() if detalles else None)
        creadas = []
        filas = detalles or [None]
        for indice, detalle in enumerate(filas):
            if indice == 0:
                cita = cita_base
            else:
                cita = CitaCliente(
                    empresa=empresa,
                    cliente=cita_base.cliente,
                    paciente=cita_base.paciente,
                    producto=cita_base.producto,
                    servicio_clinico=cita_base.servicio_clinico,
                    profesional_salud=cita_base.profesional_salud,
                    responsable=cita_base.responsable,
                    estado=cita_base.estado,
                    pagada=cita_base.pagada,
                    observacion=cita_base.observacion,
                    duracion_minutos=cita_base.duracion_minutos,
                    enviar_confirmacion_whatsapp=False,
                    recordatorio_semana_whatsapp=cita_base.recordatorio_semana_whatsapp,
                    recordatorio_dia_whatsapp=cita_base.recordatorio_dia_whatsapp,
                )
            if detalle:
                cita.fecha_hora = detalle["inicio"]
                cita.grupo_atencion = grupo
                cita.opcion_servicio = detalle["opcion"] or None
                cita.fase_servicio = detalle["fase"]
                cita.sesion_servicio = detalle["sesion"]
                cita.titulo = cita.display_servicio
            cita.save()
            _sincronizar_cita_clinica(cita)
            creadas.append(cita)
        _guardar_fotos_cirugia_cita(cita_base, form.cleaned_data.get("fotos_cirugia"), request.user)
    _programar_whatsapp_cita(request, cita_base)
    return cita_base, creadas


def _sincronizar_cita_clinica(cita):
    if not cita.paciente_id:
        return
    estados = {
        "pendiente": "solicitada", "confirmada": "confirmada",
        "realizada": "completada", "cancelada": "cancelada",
    }
    detalle_cirugia = ""
    if cita.cirugia_detalle:
        detalle_cirugia = f"Detalle de cirugia: {cita.cirugia_detalle}"
        if cita.cirugia_fin_estimada:
            detalle_cirugia = (
                f"{detalle_cirugia}\nFin estimado: "
                f"{timezone.localtime(cita.cirugia_fin_estimada):%d/%m/%Y %I:%M %p} "
                "(incluye bloqueo de recuperacion de 1 hora)."
            )
    observaciones = "\n".join(parte for parte in [detalle_cirugia, cita.observacion or ""] if parte).strip()
    valores = {
        "empresa": cita.empresa,
        "paciente": cita.paciente,
        "profesional": cita.profesional_salud,
        "servicio": cita.servicio_clinico,
        "fecha_hora": cita.fecha_hora,
        "estado": estados.get(cita.estado, "solicitada"),
        "pagada": cita.pagada,
        "canal": "recepcion",
        "motivo": cita.cirugia_detalle or cita.observacion or cita.titulo,
        "observaciones": observaciones,
    }
    if cita.cita_clinica_id:
        for campo, valor in valores.items():
            setattr(cita.cita_clinica, campo, valor)
        cita.cita_clinica.save()
    else:
        cita.cita_clinica = CitaClinica.objects.create(**valores)
        cita.save(update_fields=["cita_clinica"])


def _guardar_fotos_cirugia_cita(cita, archivos, usuario):
    if cita.empresa.slug not in CitaClienteForm.EMPRESAS_CIRUGIA_EXTENDIDA or not archivos:
        return
    for archivo in archivos:
        content_type = (getattr(archivo, "content_type", "") or "").lower()
        nombre_archivo = (getattr(archivo, "name", "") or "").lower()
        es_video = content_type.startswith("video/") or nombre_archivo.endswith((".mp4", ".mov", ".webm", ".m4v"))
        es_imagen = content_type.startswith("image/") or nombre_archivo.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic"))
        if not (es_imagen or es_video):
            continue
        adjunto = CitaCirugiaFoto.objects.create(
            cita=cita,
            empresa=cita.empresa,
            imagen=archivo,
            creado_por=usuario,
        )
        if cita.paciente_id:
            descripcion = (
                f"Archivo cargado al programar cirugia el "
                f"{timezone.localtime(cita.fecha_hora):%d/%m/%Y %I:%M %p}."
            )
            if cita.cirugia_detalle:
                descripcion = f"{descripcion}\nDetalle: {cita.cirugia_detalle}"
            datos_evolucion = {
                "empresa": cita.empresa,
                "paciente": cita.paciente,
                "tipo": "preoperatorio",
                "titulo": f"{'Video' if es_video else 'Foto'} antes de operacion - {cita.display_servicio}",
                "descripcion": descripcion,
                "fecha": timezone.now(),
                "creado_por": usuario,
            }
            if es_video:
                datos_evolucion["video"] = adjunto.imagen.name
            else:
                datos_evolucion["imagen"] = adjunto.imagen.name
            PacienteFotoEvolucion.objects.create(**datos_evolucion)


def _programar_whatsapp_cita(request, cita):
    try:
        notificaciones = programar_notificaciones_cita(cita)
        confirmacion = next(
            (item for item in notificaciones if item.tipo == NotificacionCitaWhatsApp.TIPO_CONFIRMACION),
            None,
        )
        if not confirmacion or confirmacion.estado == "enviado":
            return
        resultado = procesar_notificacion(confirmacion.id)
        if resultado.estado == "enviado":
            messages.success(request, "Confirmación de la cita enviada por WhatsApp.")
        elif resultado.estado == "error":
            messages.warning(request, f"La cita se guardó, pero WhatsApp respondió con error: {resultado.ultimo_error}")
    except Exception:
        # Una falla externa de Meta, red o configuración nunca debe impedir que
        # recepción registre la cita. El detalle completo queda en el log.
        logger.exception("No se pudo procesar WhatsApp para la cita %s", cita.pk)
        messages.warning(
            request,
            "La cita se guardó correctamente, pero WhatsApp no pudo procesarse ahora. "
            "El recordatorio podrá reintentarse automáticamente.",
        )


def cita_respuesta_publica(request, token):
    try:
        datos = leer_token_respuesta_cita(token)
    except (signing.BadSignature, signing.SignatureExpired):
        return render(
            request,
            "crm/cita_respuesta_publica.html",
            {"estado_pagina": "invalido"},
            status=410,
        )

    cita = get_object_or_404(
        CitaCliente.objects.select_related(
            "empresa", "paciente", "cliente", "servicio_clinico", "profesional_salud", "cita_clinica"
        ),
        id=datos.get("cita_id"),
        empresa__slug=datos.get("empresa"),
    )
    local = timezone.localtime(cita.fecha_hora)
    contexto = {
        "estado_pagina": "formulario",
        "empresa": cita.empresa,
        "cita": cita,
        "fecha_local": local,
    }
    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "confirmar":
            cita.estado = "confirmada"
            nota = f"Paciente confirmó asistencia desde enlace público el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}."
            cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
            cita.save(update_fields=["estado", "observacion"])
            _sincronizar_cita_clinica(cita)
            contexto["estado_pagina"] = "confirmada"
        elif accion == "cancelar":
            motivo = (request.POST.get("motivo") or "").strip()
            cita.estado = "cancelada"
            nota = f"Paciente canceló desde enlace público el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}."
            if motivo:
                nota = f"{nota} Motivo: {motivo}"
            cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
            cita.save(update_fields=["estado", "observacion"])
            cita.notificaciones_whatsapp.filter(estado__in=["pendiente", "error"]).update(estado="omitido")
            _sincronizar_cita_clinica(cita)
            contexto["estado_pagina"] = "cancelada"
        else:
            contexto["error"] = "Selecciona si confirmas o cancelas la cita."
    return render(request, "crm/cita_respuesta_publica.html", contexto)


@login_required
def crm_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    hoy = timezone.localdate()
    manana = hoy + timezone.timedelta(days=1)
    config = _configuracion_crm(empresa)
    clientes = Cliente.objects.filter(empresa=empresa, activo=True)
    total_clientes = clientes.count()
    aceptan_promos = clientes.filter(acepta_promociones=True).count()
    tasa_promos = round((aceptan_promos / total_clientes) * 100) if total_clientes else 0
    cumpleanos_manana = clientes.filter(
        fecha_nacimiento__month=manana.month,
        fecha_nacimiento__day=manana.day,
    ).order_by("nombre")
    proximos_cumpleanos = []
    clientes_con_fecha = clientes.exclude(fecha_nacimiento__isnull=True).only(
        "nombre", "telefono", "telefono_whatsapp", "fecha_nacimiento"
    )
    for cliente in clientes_con_fecha:
        try:
            fecha_cumple = cliente.fecha_nacimiento.replace(year=hoy.year)
        except ValueError:
            fecha_cumple = cliente.fecha_nacimiento.replace(year=hoy.year, day=28)
        if fecha_cumple < hoy:
            try:
                fecha_cumple = fecha_cumple.replace(year=hoy.year + 1)
            except ValueError:
                fecha_cumple = fecha_cumple.replace(year=hoy.year + 1, day=28)
        dias_faltantes = (fecha_cumple - hoy).days
        if 0 <= dias_faltantes <= 30:
            proximos_cumpleanos.append({
                "cliente": cliente,
                "fecha": fecha_cumple,
                "dias": dias_faltantes,
            })
    proximos_cumpleanos = sorted(proximos_cumpleanos, key=lambda item: (item["dias"], item["cliente"].nombre))[:8]
    fecha_alerta = hoy + timezone.timedelta(days=config.dias_alerta_producto)
    productos_alerta = Producto.objects.filter(
        empresa=empresa,
        activo=True,
        fecha_alerta__isnull=False,
        fecha_alerta__lte=fecha_alerta,
    ).order_by("fecha_alerta")[:8]
    etiquetas_fuente = {
        "facebook": "Facebook",
        "instagram": "Instagram",
        "x": "X",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "google": "Google",
        "whatsapp": "WhatsApp",
        "referencia": "Referencia",
        "otro": "Otro",
    }
    fuentes_preconsulta = [
        {
            "fuente": etiquetas_fuente.get(item["datos_generales__referido_por"], item["datos_generales__referido_por"] or "No indicado"),
            "total": item["total"],
        }
        for item in PreconsultaClinica.objects.filter(
            empresa=empresa,
            datos_generales__referido_por__isnull=False,
        )
        .exclude(datos_generales__referido_por="")
        .values("datos_generales__referido_por")
        .annotate(total=Count("id"))
        .order_by("-total", "datos_generales__referido_por")[:8]
    ]
    return render(
        request,
        "crm/dashboard.html",
        {
            "empresa": empresa,
            "config": config,
            "resumen": {
                "clientes": total_clientes,
                "aceptan_promos": aceptan_promos,
                "tasa_promos": tasa_promos,
                "campanias": CampaniaMarketing.objects.filter(empresa=empresa).count(),
                "campanias_enviadas": CampaniaMarketing.objects.filter(empresa=empresa, estado="enviada").count(),
                "plantillas_activas": PlantillaMensaje.objects.filter(empresa=empresa, activa=True).count(),
            },
            "cumpleanos_manana": cumpleanos_manana,
            "proximos_cumpleanos": proximos_cumpleanos,
            "productos_alerta": productos_alerta,
            "fuentes_preconsulta": fuentes_preconsulta,
        },
    )


@login_required
def configuracion_crm(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    form = ConfiguracionCRMForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuracion CRM actualizada correctamente.")
        return redirect("crm_dashboard", empresa_slug=empresa.slug)
    return render(request, "crm/form.html", {"empresa": empresa, "form": form, "titulo": "Configuracion CRM"})


@login_required
@require_POST
def enviar_prueba_whatsapp(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp en la configuracion CRM antes de enviar pruebas.")
        return redirect("crm_configuracion", empresa_slug=empresa.slug)
    try:
        respuesta = enviar_plantilla_whatsapp(config, config.whatsapp_numero_prueba)
        messages.success(request, f"Prueba enviada correctamente. Respuesta Meta: {respuesta}")
    except WhatsAppAPIError as exc:
        messages.error(request, f"No se pudo enviar la prueba WhatsApp. {exc}")
    return redirect("crm_configuracion", empresa_slug=empresa.slug)


@login_required
def plantillas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    plantilla_id = request.POST.get("plantilla_id") or request.GET.get("editar")
    plantilla_obj = get_object_or_404(PlantillaMensaje, id=plantilla_id, empresa=empresa) if plantilla_id else None
    form = PlantillaMensajeForm(request.POST or None, request.FILES or None, instance=plantilla_obj)
    if request.method == "POST" and form.is_valid():
        plantilla = form.save(commit=False)
        plantilla.empresa = empresa
        plantilla.save()
        messages.success(request, "Plantilla actualizada correctamente." if plantilla_obj else "Plantilla guardada correctamente.")
        return redirect("crm_plantillas", empresa_slug=empresa.slug)
    plantillas_qs = PlantillaMensaje.objects.filter(empresa=empresa)
    resumen_tipos = {
        "total": plantillas_qs.count(),
        "activas": plantillas_qs.filter(activa=True).count(),
        "cumpleanos": plantillas_qs.filter(tipo="cumpleanos", activa=True).count(),
        "promocion": plantillas_qs.filter(tipo="promocion", activa=True).count(),
    }
    return render(
        request,
        "crm/plantillas.html",
        {
            "empresa": empresa,
            "form": form,
            "plantillas": plantillas_qs,
            "plantilla_obj": plantilla_obj,
            "resumen_tipos": resumen_tipos,
        },
    )


@login_required
def campanias(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    campanias_qs = CampaniaMarketing.objects.filter(empresa=empresa).select_related("plantilla")
    return render(request, "crm/campanias.html", {"empresa": empresa, "campanias": campanias_qs})


@login_required
def crear_campania(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CampaniaMarketingForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        campania = form.save(commit=False)
        campania.empresa = empresa
        campania.creado_por = request.user
        campania.save()
        messages.success(request, "Campania creada correctamente.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)
    return render(request, "crm/form.html", {"empresa": empresa, "form": form, "titulo": "Nueva Campania"})


@login_required
def ver_campania(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    envios = campania.envios.select_related("cliente")
    return render(request, "crm/ver_campania.html", {"empresa": empresa, "campania": campania, "envios": envios})


@login_required
@require_POST
def preparar_envios_campania(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not campania.plantilla:
        messages.error(request, "La campania necesita una plantilla para preparar envios.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)
    creados = 0
    for cliente in campania.clientes_objetivo():
        envio, creado = EnvioCampania.objects.get_or_create(
            campania=campania,
            cliente=cliente,
            canal=campania.plantilla.canal if campania.plantilla.canal != "ambos" else "whatsapp",
            defaults={"mensaje": campania.plantilla.render(cliente=cliente), "estado": "preparado"},
        )
        if creado:
            creados += 1
        elif envio.estado == "pendiente":
            envio.mensaje = campania.plantilla.render(cliente=cliente)
            envio.estado = "preparado"
            envio.save(update_fields=["mensaje", "estado"])
    messages.success(request, f"Envios preparados para {creados} cliente(s) nuevos.")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


def _resumen_promocion(campania, envio):
    mensaje = (envio.mensaje or "").replace("\r", " ").replace("\n", " ").strip()
    if mensaje:
        return mensaje[:900]
    if campania.plantilla and campania.plantilla.mensaje:
        return campania.plantilla.mensaje[:900]
    return campania.nombre


def _vigencia_promocion(campania):
    if campania.fecha_programada:
        return timezone.localtime(campania.fecha_programada).strftime("%d/%m/%Y")
    return "por tiempo limitado"


def _enlace_whatsapp_empresa(config, empresa):
    numero = "".join(ch for ch in (config.whatsapp_numero_prueba or "") if ch.isdigit())
    if numero:
        return f"https://wa.me/{numero}"
    slug = getattr(empresa, "slug", "") or "empresa"
    return f"responde a este mensaje o visita el enlace de {slug}"


@login_required
@require_POST
def enviar_campania_plantilla_prueba(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp Cloud API en la configuracion CRM antes de enviar la prueba masiva.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    envios = campania.envios.select_related("cliente")
    if not envios.exists():
        messages.error(request, "No hay envios preparados. Primero prepara los mensajes para todos.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    enviados = 0
    errores = 0
    nombre_plantilla = config.whatsapp_plantilla_prueba or "hello_world"
    idioma = config.whatsapp_idioma_plantilla or "en_US"
    for envio in envios:
        numero = envio.cliente.telefono_whatsapp or envio.cliente.telefono
        try:
            respuesta = enviar_plantilla_whatsapp(config, numero, nombre_plantilla=nombre_plantilla, idioma=idioma)
            envio.estado = "enviado"
            envio.respuesta = f"Prueba plantilla {nombre_plantilla}: {respuesta}"
            envio.fecha_envio = timezone.now()
            envio.save(update_fields=["estado", "respuesta", "fecha_envio"])
            enviados += 1
        except WhatsAppAPIError as exc:
            envio.estado = "error"
            envio.respuesta = f"Prueba plantilla {nombre_plantilla}: {exc}"
            envio.save(update_fields=["estado", "respuesta"])
            errores += 1

    if errores:
        messages.warning(request, f"Prueba masiva procesada: {enviados} enviado(s), {errores} con error.")
    else:
        messages.success(request, f"Prueba masiva enviada correctamente a {enviados} cliente(s) con {nombre_plantilla}.")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


@login_required
@require_POST
def enviar_campania_whatsapp_api(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp Cloud API en la configuracion CRM antes de enviar campanias.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    envios = campania.envios.select_related("cliente").exclude(estado="enviado")
    if not envios.exists():
        messages.error(request, "No hay envios pendientes. Primero prepara los mensajes para todos.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    media_id = None
    if campania.plantilla and campania.plantilla.imagen_promocional:
        try:
            media_id = subir_media_whatsapp(config, campania.plantilla.imagen_promocional)
        except WhatsAppAPIError as exc:
            messages.error(request, f"No se pudo subir la imagen promocional a WhatsApp. {exc}")
            return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    enviados = 0
    errores = 0
    usar_plantilla_marketing = bool(config.whatsapp_plantilla_marketing)
    for envio in envios:
        numero = envio.cliente.telefono_whatsapp or envio.cliente.telefono
        try:
            if usar_plantilla_marketing:
                respuesta = enviar_plantilla_marketing_whatsapp(
                    config,
                    numero,
                    nombre_cliente=envio.cliente.nombre,
                    promocion=_resumen_promocion(campania, envio),
                    vigencia=_vigencia_promocion(campania),
                    enlace=_enlace_whatsapp_empresa(config, empresa),
                    media_id=media_id,
                )
            elif media_id:
                respuesta = enviar_imagen_whatsapp(config, numero, media_id, envio.mensaje)
            else:
                respuesta = enviar_mensaje_whatsapp_texto(config, numero, envio.mensaje)
            envio.estado = "enviado"
            envio.respuesta = str(respuesta)
            envio.fecha_envio = timezone.now()
            envio.save(update_fields=["estado", "respuesta", "fecha_envio"])
            enviados += 1
        except WhatsAppAPIError as exc:
            envio.estado = "error"
            envio.respuesta = str(exc)
            envio.save(update_fields=["estado", "respuesta"])
            errores += 1

    if errores:
        messages.warning(request, f"Campania procesada: {enviados} enviado(s), {errores} con error.")
    else:
        campania.estado = "enviada"
        campania.save(update_fields=["estado"])
        messages.success(request, f"Campania enviada correctamente a {enviados} cliente(s).")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


@login_required
def citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, request.FILES or None, empresa=empresa, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita, creadas = _guardar_cita_formulario(request, empresa, form, objeto)
        messages.success(request, f"{len(creadas)} cita(s) guardada(s) correctamente.")
        return redirect("crm_citas", empresa_slug=empresa.slug)
    return render(request, "crm/citas.html", _contexto_calendario(empresa, request, form))


@login_required
def agenda_citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _asegurar_pacientes_empresas_clinicas(empresa)
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, request.FILES or None, empresa=empresa, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita, creadas = _guardar_cita_formulario(request, empresa, form, objeto)
        messages.success(request, "Cita actualizada correctamente." if objeto else f"{len(creadas)} cita(s) guardada(s) correctamente.")
        return redirect("agenda_citas", empresa_slug=empresa.slug)
    return render(request, "crm/citas.html", _contexto_calendario(empresa, request, form, modo_agenda=True))


@login_required
def camara_hiperbarica(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if empresa.slug != "hospital_mia":
        return HttpResponse("Este módulo clínico solo está habilitado para Hospital Mia.", status=404)
    if not request.user.puede_acceder_empresa(empresa):
        return HttpResponse("Acceso no autorizado.", status=403)

    puede_consultar = (
        request.user.is_superuser
        or request.user.es_administrador_empresa
        or request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa)
    )
    if not puede_consultar:
        return HttpResponse("Tu usuario no tiene permiso para consultar controles clínicos.", status=403)

    fecha_seleccionada = _fecha_agenda(request.GET.get("fecha"))
    contexto = {
        "empresa": empresa,
        "fecha_seleccionada": fecha_seleccionada,
        "fecha_anterior": fecha_seleccionada - timedelta(days=1),
        "fecha_siguiente": fecha_seleccionada + timedelta(days=1),
    }
    contexto.update(_contexto_control_camara_hyperbarica(empresa, request, fecha_seleccionada))
    return render(request, "crm/camara_hiperbarica.html", contexto)


@login_required
@require_POST
def guardar_control_camara_hiperbarica(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    if empresa.slug != "hospital_mia":
        return HttpResponse("Este control clínico solo está habilitado para Hospital Mia.", status=404)
    if not request.user.puede_acceder_empresa(empresa):
        return HttpResponse("Acceso no autorizado.", status=403)
    puede_trabajar = (
        request.user.is_superuser
        or request.user.es_administrador_empresa
        or request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa)
    )
    if not puede_trabajar:
        return HttpResponse("Tu usuario no tiene permiso para registrar controles clínicos.", status=403)

    cita = get_object_or_404(
        CitaCliente.objects.select_related("paciente", "servicio_clinico"),
        empresa=empresa,
        id=cita_id,
        paciente__isnull=False,
    )
    if not _es_cita_camara_hiperbarica(cita):
        return HttpResponse("La cita seleccionada no corresponde a cámara hiperbárica.", status=400)

    sesion = SesionCamaraHiperbarica.objects.filter(cita=cita).select_related("programa").first()
    if sesion and sesion.bloqueada:
        messages.warning(request, "La sesión ya fue finalizada y permanece bloqueada para proteger el historial clínico.")
        return redirect(
            f"{reverse('agenda_camara_hiperbarica', args=[empresa.slug])}?fecha={cita.fecha_hora:%Y-%m-%d}"
            f"&control_camara={cita.id}#documento-camara"
        )

    programa = sesion.programa if sesion else None
    programa_id = (request.POST.get("programa_id") or "").strip()
    if programa_id and not programa:
        try:
            programa = ProgramaCamaraHiperbarica.objects.filter(
                id=int(programa_id),
                empresa=empresa,
                paciente=cita.paciente,
                activo=True,
            ).first()
        except (TypeError, ValueError):
            programa = None

    finalizar = request.POST.get("accion") == "finalizar"
    programa_form = ProgramaCamaraHiperbaricaForm(request.POST, instance=programa)
    datos_sesion = request.POST.copy()
    numero_sesion_desde_cita = (
        cita.sesion_servicio if 1 <= (cita.sesion_servicio or 0) <= 22 else None
    )
    if numero_sesion_desde_cita:
        datos_sesion["numero_sesion"] = str(numero_sesion_desde_cita)
    sesion_form = SesionCamaraHiperbaricaForm(
        datos_sesion,
        instance=sesion,
        finalizar=finalizar,
    )
    # Valide ambos formularios siempre. El operador necesita ver todos los
    # campos pendientes en una sola respuesta, no solo el primer error.
    programa_valido = programa_form.is_valid()
    sesion_valida = sesion_form.is_valid()
    formularios_validos = programa_valido and sesion_valida
    if formularios_validos:
        numero_sesion = sesion_form.cleaned_data["numero_sesion"]
        programa_para_duplicado = programa or ProgramaCamaraHiperbarica.objects.filter(
            empresa=empresa,
            paciente=cita.paciente,
            activo=True,
        ).first()
        if programa_para_duplicado:
            duplicada = SesionCamaraHiperbarica.objects.filter(
                programa=programa_para_duplicado,
                numero_sesion=numero_sesion,
            )
            if sesion:
                duplicada = duplicada.exclude(pk=sesion.pk)
            if duplicada.exists():
                sesion_form.add_error(
                    "numero_sesion",
                    f"La sesión {numero_sesion} ya está registrada en este programa.",
                )
                formularios_validos = False

    if formularios_validos:
        with transaction.atomic():
            programa_guardado = programa_form.save(commit=False)
            programa_guardado.empresa = empresa
            programa_guardado.paciente = cita.paciente
            if not programa_guardado.pk:
                programa_guardado.creado_por = request.user
            programa_guardado.actualizado_por = request.user
            programa_guardado.save()

            sesion_guardada = sesion_form.save(commit=False)
            sesion_guardada.programa = programa_guardado
            sesion_guardada.empresa = empresa
            sesion_guardada.paciente = cita.paciente
            sesion_guardada.cita = cita
            sesion_guardada.estado = "finalizada" if finalizar else "borrador"
            if not sesion_guardada.pk:
                sesion_guardada.creado_por = request.user
            sesion_guardada.actualizado_por = request.user
            sesion_guardada.save()
            if finalizar:
                messages.success(
                    request,
                    f"Sesión {sesion_guardada.numero_sesion} finalizada y bloqueada correctamente.",
                )
            else:
                messages.success(request, f"Borrador de la sesión {sesion_guardada.numero_sesion} guardado.")
    else:
        borrador_guardado_automaticamente = False
        # Si se intentó finalizar una sesión incompleta, preserve de inmediato
        # todo lo capturado en la base de datos. Se vuelve a validar como
        # borrador porque los campos clínicos solo son obligatorios al cerrar.
        if finalizar and programa_valido:
            borrador_form = SesionCamaraHiperbaricaForm(
                datos_sesion,
                instance=sesion,
                finalizar=False,
            )
            if borrador_form.is_valid():
                numero_borrador = borrador_form.cleaned_data["numero_sesion"]
                programa_para_duplicado = programa or ProgramaCamaraHiperbarica.objects.filter(
                    empresa=empresa,
                    paciente=cita.paciente,
                    activo=True,
                ).first()
                duplicada = SesionCamaraHiperbarica.objects.none()
                if programa_para_duplicado:
                    duplicada = SesionCamaraHiperbarica.objects.filter(
                        programa=programa_para_duplicado,
                        numero_sesion=numero_borrador,
                    )
                    if sesion:
                        duplicada = duplicada.exclude(pk=sesion.pk)

                if not duplicada.exists():
                    with transaction.atomic():
                        programa_guardado = programa_form.save(commit=False)
                        programa_guardado.empresa = empresa
                        programa_guardado.paciente = cita.paciente
                        if not programa_guardado.pk:
                            programa_guardado.creado_por = request.user
                        programa_guardado.actualizado_por = request.user
                        programa_guardado.save()

                        sesion_guardada = borrador_form.save(commit=False)
                        sesion_guardada.programa = programa_guardado
                        sesion_guardada.empresa = empresa
                        sesion_guardada.paciente = cita.paciente
                        sesion_guardada.cita = cita
                        sesion_guardada.estado = "borrador"
                        if not sesion_guardada.pk:
                            sesion_guardada.creado_por = request.user
                        sesion_guardada.actualizado_por = request.user
                        sesion_guardada.save()
                    programa = programa_guardado
                    sesion = sesion_guardada
                    borrador_guardado_automaticamente = True

        errores = []
        for formulario in (programa_form, sesion_form):
            for campo, mensajes_campo in formulario.errors.items():
                etiqueta = formulario.fields[campo].label if campo in formulario.fields else "Formulario"
                errores.extend(f"{etiqueta}: {mensaje}" for mensaje in mensajes_campo)
        if borrador_guardado_automaticamente:
            messages.error(
                request,
                "La sesión no se finalizó porque faltan datos. Todo lo escrito quedó guardado "
                "automáticamente como borrador; complete los campos marcados en rojo.",
            )
        else:
            messages.error(
                request,
                "Revise los campos marcados en rojo. La información escrita se conserva en pantalla.",
            )

        fecha = timezone.localtime(cita.fecha_hora).date()
        contexto = {
            "empresa": empresa,
            "fecha_seleccionada": fecha,
            "fecha_anterior": fecha - timedelta(days=1),
            "fecha_siguiente": fecha + timedelta(days=1),
            "errores_control_camara": errores,
        }
        contexto.update(
            _contexto_control_camara_hyperbarica(
                empresa,
                request,
                fecha,
                cita_control_id=cita.id,
            )
        )
        contexto.update({
            "programa_camara": programa,
            "programa_camara_form": programa_form,
            "sesion_camara": sesion,
            "sesion_camara_form": sesion_form,
            "numero_sesion_desde_cita": numero_sesion_desde_cita,
        })
        return render(request, "crm/camara_hiperbarica.html", contexto)

    fecha = timezone.localtime(cita.fecha_hora).date().isoformat()
    return redirect(
        f"{reverse('agenda_camara_hiperbarica', args=[empresa.slug])}?fecha={fecha}"
        f"&control_camara={cita.id}#documento-camara"
    )


def agenda_mobile(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    empresa_agenda = _empresa_origen_agenda_mobile(empresa)
    _asegurar_pacientes_empresas_clinicas(empresa)
    if empresa_agenda.id != empresa.id:
        _asegurar_pacientes_empresas_clinicas(empresa_agenda)
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa_agenda, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, request.FILES or None, empresa=empresa_agenda, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita, creadas = _guardar_cita_formulario(request, empresa_agenda, form, objeto)
        messages.success(request, "Cita actualizada correctamente." if objeto else f"{len(creadas)} cita(s) creada(s) correctamente.")
        fecha = timezone.localtime(cita.fecha_hora).date().isoformat()
        vista_regreso = request.GET.get("vista") or request.POST.get("vista") or "dia"
        if vista_regreso not in {"dia", "semana", "mes", "anio", "agenda", "proximas"}:
            vista_regreso = "dia"
        return redirect(f"{reverse('agenda_mobile', args=[empresa.slug])}?vista={vista_regreso}&fecha={fecha}")

    contexto = _contexto_calendario(
        empresa,
        request,
        form,
        modo_agenda=True,
        vista_predeterminada="dia",
        empresa_agenda=empresa_agenda,
    )
    contexto["empresas_app_switch"] = [
        {
            "id": empresa_disponible.id,
            "nombre": empresa_disponible.nombre,
            "slug": empresa_disponible.slug,
            "actual": empresa_disponible.id == empresa.id,
            "url": reverse("agenda_mobile", args=[empresa_disponible.slug]),
            "puede_facturar": request.user.tiene_alguna_permision_facturacion_empresa(empresa_disponible),
        }
        for empresa_disponible in request.user.empresas_operativas()
    ]
    seleccionada = contexto["fecha_seleccionada"]
    dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    dias_semana_largos = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    for dia_contexto in contexto.get("dias", []):
        fecha_dia = dia_contexto["fecha"]
        dia_contexto["dia_corto"] = dias_semana[fecha_dia.weekday()]
        dia_contexto["titulo_mobile"] = (
            f"{dias_semana_largos[fecha_dia.weekday()]} "
            f"{fecha_dia.day} de {meses[fecha_dia.month - 1]}"
        )
    contexto["titulo_fecha_mobile"] = (
        f"{dias_semana_largos[seleccionada.weekday()]}, "
        f"{seleccionada.day} de {meses[seleccionada.month - 1]}"
    )
    inicio_tira = seleccionada - timedelta(days=3)
    fin_tira = seleccionada + timedelta(days=3)
    empresa_agenda = contexto["agenda_empresa"]
    agenda_espejo = contexto["agenda_espejo"]
    citas_tira = CitaCliente.objects.filter(
        empresa=empresa_agenda,
        fecha_hora__date__gte=inicio_tira,
        fecha_hora__date__lte=fin_tira,
    ).select_related("profesional_salud")
    if agenda_espejo and empresa.slug == "serviciosmedicos":
        citas_tira = citas_tira.filter(Q(profesional_salud__nombre__icontains="Luis") | Q(responsable__icontains="Luis"))
    if contexto.get("filtro_servicio"):
        citas_tira = citas_tira.filter(servicio_clinico_id=contexto["filtro_servicio"])
    if contexto.get("filtro_profesional"):
        citas_tira = citas_tira.filter(profesional_salud_id=contexto["filtro_profesional"])
    if agenda_espejo:
        conteos = {}
        for cita in citas_tira:
            if not _cita_pertenece_agenda_espejo(cita, empresa):
                continue
            clave = timezone.localtime(cita.fecha_hora).date()
            conteos[clave] = conteos.get(clave, 0) + 1
    else:
        conteos = {
            fila["fecha_hora__date"]: fila["total"]
            for fila in citas_tira.values("fecha_hora__date").annotate(total=Count("id"))
        }
    contexto["dias_moviles"] = [
        {
            "fecha": inicio_tira + timedelta(days=indice),
            "dia_corto": dias_semana[(inicio_tira + timedelta(days=indice)).weekday()],
            "total": conteos.get(inicio_tira + timedelta(days=indice), 0),
        }
        for indice in range(7)
    ]
    ahora = timezone.now()
    proximas_qs = (
        CitaCliente.objects.filter(
            empresa=empresa_agenda,
            fecha_hora__gte=ahora,
            fecha_hora__lte=ahora + timedelta(hours=24),
        )
        .select_related("paciente", "cliente", "servicio_clinico", "producto", "profesional_salud")
        .order_by("fecha_hora")
    )
    if agenda_espejo and empresa.slug == "serviciosmedicos":
        proximas_qs = proximas_qs.filter(Q(profesional_salud__nombre__icontains="Luis") | Q(responsable__icontains="Luis"))
    proximas = [
        cita for cita in proximas_qs[:50]
        if _cita_pertenece_agenda_espejo(cita, empresa)
    ][:20]
    contexto["proximas_app"] = [
        {
            "id": cita.id,
            "title": f"Cita: {cita.display_cliente}",
            "body": f"{cita.display_servicio} · {timezone.localtime(cita.fecha_hora).strftime('%I:%M %p')}",
            "at": int(cita.fecha_hora.timestamp() * 1000),
            "url": f"{reverse('agenda_mobile', args=[empresa.slug])}?fecha={timezone.localtime(cita.fecha_hora).date().isoformat()}",
        }
        for cita in proximas
    ]
    citas_hoy_qs = CitaCliente.objects.filter(
        empresa=empresa_agenda,
        fecha_hora__date=timezone.localdate(),
    ).select_related("profesional_salud")
    if agenda_espejo and empresa.slug == "serviciosmedicos":
        citas_hoy_qs = citas_hoy_qs.filter(Q(profesional_salud__nombre__icontains="Luis") | Q(responsable__icontains="Luis"))
    citas_hoy = [
        cita for cita in citas_hoy_qs
        if _cita_pertenece_agenda_espejo(cita, empresa)
    ]
    contexto["citas_hoy_total"] = len(citas_hoy)
    contexto["pendientes_hoy"] = sum(1 for cita in citas_hoy if cita.estado in ["pendiente", "confirmada"])
    contexto["hospital_mia_app_premium"] = empresa.slug in EMPRESAS_INTERFAZ_CLINICA_GLOBAL
    contexto["pacientes_app_premium"] = contexto["hospital_mia_app_premium"]
    pacientes_activos_qs = Paciente.objects.filter(empresa=empresa, activo=True)
    contexto["pacientes_app_total"] = pacientes_activos_qs.count()
    contexto["pacientes_recientes_app"] = pacientes_activos_qs.filter(
        empresa=empresa,
        activo=True,
    ).order_by("-fecha_actualizacion", "-id")[:4]
    limite_pacientes_app = 320 if contexto["pacientes_app_premium"] else 120
    pacientes_app_qs = list(
        pacientes_activos_qs.select_related("cliente")
        .order_by("-fecha_actualizacion", "nombre")[:limite_pacientes_app]
    )
    pacientes_agenda_por_perfil = {}
    pacientes_agenda_por_identidad = {}
    pacientes_agenda_por_nombre = {}
    if empresa_agenda.id != empresa.id:
        for paciente_agenda in Paciente.objects.filter(
            empresa=empresa_agenda,
            activo=True,
        ).select_related("cliente"):
            perfil_id = getattr(paciente_agenda.cliente, "perfil_compartido_id", None)
            if perfil_id:
                pacientes_agenda_por_perfil[str(perfil_id)] = paciente_agenda
            if paciente_agenda.identidad:
                pacientes_agenda_por_identidad[paciente_agenda.identidad.strip().casefold()] = paciente_agenda
            if paciente_agenda.nombre:
                clave_nombre = paciente_agenda.nombre.strip().casefold()
                pacientes_agenda_por_nombre[clave_nombre] = (
                    None if clave_nombre in pacientes_agenda_por_nombre else paciente_agenda
                )

    def _paciente_en_agenda(paciente):
        if empresa_agenda.id == empresa.id:
            return paciente
        perfil_id = getattr(paciente.cliente, "perfil_compartido_id", None)
        if perfil_id and str(perfil_id) in pacientes_agenda_por_perfil:
            return pacientes_agenda_por_perfil[str(perfil_id)]
        identidad = (paciente.identidad or "").strip().casefold()
        if identidad and identidad in pacientes_agenda_por_identidad:
            return pacientes_agenda_por_identidad[identidad]
        return pacientes_agenda_por_nombre.get((paciente.nombre or "").strip().casefold())

    paciente_agenda_por_paciente = {
        paciente.id: _paciente_en_agenda(paciente)
        for paciente in pacientes_app_qs
    }
    paciente_local_por_agenda_id = {
        paciente_agenda.id: paciente_id
        for paciente_id, paciente_agenda in paciente_agenda_por_paciente.items()
        if paciente_agenda
    }
    pacientes_agenda_ids = list(paciente_local_por_agenda_id)
    proximas_por_paciente = {}
    frecuencia_por_paciente = {}
    if contexto["pacientes_app_premium"] and pacientes_agenda_ids:
        proximas_pacientes_qs = (
            CitaCliente.objects.filter(
                empresa=empresa_agenda,
                paciente_id__in=pacientes_agenda_ids,
                fecha_hora__gte=ahora,
                estado__in=["pendiente", "confirmada"],
            )
            .select_related("servicio_clinico", "producto", "profesional_salud")
            .order_by("fecha_hora")
        )
        for cita in proximas_pacientes_qs:
            paciente_local_id = paciente_local_por_agenda_id.get(cita.paciente_id)
            if paciente_local_id:
                proximas_por_paciente.setdefault(paciente_local_id, cita)
        frecuencia_por_paciente = {
            paciente_local_por_agenda_id[fila["paciente_id"]]: fila["total"]
            for fila in CitaCliente.objects.filter(
                empresa=empresa_agenda,
                paciente_id__in=pacientes_agenda_ids,
            )
            .values("paciente_id")
            .annotate(total=Count("id"))
            if fila["paciente_id"] in paciente_local_por_agenda_id
        }

    def _foto_paciente_app(paciente):
        if not paciente.foto_perfil:
            return ""
        try:
            return paciente.foto_perfil.url
        except ValueError:
            return ""

    def _payload_paciente_app(paciente):
        proxima = proximas_por_paciente.get(paciente.id)
        paciente_agenda = paciente_agenda_por_paciente.get(paciente.id)
        fecha_actualizacion = timezone.localtime(paciente.fecha_actualizacion)
        telefono = paciente.whatsapp or paciente.telefono or ""
        alergias = (paciente.alergias or "").strip()
        alergias_sin_alerta = {
            "no",
            "no aplica",
            "ninguna",
            "ninguno",
            "n/a",
            "na",
            "sin alergias",
            "no refiere",
        }
        alergico_app = bool(
            paciente.es_alergico
            and (not alergias or alergias.casefold() not in alergias_sin_alerta)
        )
        payload = {
            "id": paciente.id,
            "nombre": paciente.nombre,
            "documento": paciente.identidad or "",
            "expediente": paciente.expediente_codigo,
            "telefono": telefono,
            "correo": paciente.correo or "",
            "edad": paciente.edad,
            "sexo": paciente.get_sexo_display(),
            "rh": paciente.rh or "No indicado",
            "alergico": alergico_app,
            "alergias": alergias,
            "foto": _foto_paciente_app(paciente),
            "actualizado": fecha_actualizacion.strftime("%d/%m/%Y"),
            "actualizado_iso": fecha_actualizacion.isoformat(),
            "frecuencia": frecuencia_por_paciente.get(paciente.id, 0),
            "cliente_id": paciente.cliente_id,
            "agenda_paciente_id": paciente_agenda.id if paciente_agenda else None,
            "agenda_expediente": paciente_agenda.expediente_codigo if paciente_agenda else "",
            "url": reverse("clinica_paciente_detalle", args=[empresa.slug, paciente.id]),
        }
        if contexto["pacientes_app_premium"]:
            payload["links"] = {
                "resumen": f'{payload["url"]}#resumen-clinico',
                "historia": reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.id]),
                "visitas": f'{payload["url"]}#historial-clinico',
                "signos": f'{payload["url"]}#signos-vitales',
                "diagnosticos": f'{payload["url"]}#diagnosticos',
                "evolucion": reverse("clinica_evolucion_paciente", args=[empresa.slug, paciente.id]),
                "recetas": reverse("clinica_recetas_paciente", args=[empresa.slug, paciente.id]),
                "examenes": reverse("clinica_examenes_paciente", args=[empresa.slug, paciente.id]),
                "archivos": reverse("clinica_documentos_categoria_paciente", args=[empresa.slug, paciente.id, "documento"]),
                "consentimientos": reverse("clinica_consentimientos_paciente", args=[empresa.slug, paciente.id]),
                "seguimientos": reverse("clinica_seguimientos_paciente", args=[empresa.slug, paciente.id]),
                "citas": f'{payload["url"]}#citas',
                "facturacion": f'{payload["url"]}#facturacion',
                "pagos": f'{payload["url"]}#pagos',
                "notas": f'{payload["url"]}#historial-clinico',
            }
            payload["proxima_cita"] = (
                {
                    "fecha": timezone.localtime(proxima.fecha_hora).strftime("%d/%m/%Y"),
                    "hora": timezone.localtime(proxima.fecha_hora).strftime("%I:%M %p"),
                    "fecha_iso": timezone.localtime(proxima.fecha_hora).isoformat(),
                    "servicio": proxima.display_servicio,
                    "profesional": proxima.display_responsable,
                    "estado": proxima.get_estado_display(),
                }
                if proxima
                else None
            )
        return payload

    contexto["pacientes_app_payload"] = [
        _payload_paciente_app(paciente)
        for paciente in pacientes_app_qs
    ]
    impuestos_qs = TipoImpuesto.objects.filter(activo=True).order_by("nombre")
    impuesto_default = impuestos_qs.first()
    productos_app_qs = (
        Producto.objects.filter(empresa=empresa, activo=True, eliminado=False)
        .select_related("impuesto_predeterminado")
        .order_by("nombre")[:180]
    )
    contexto["productos_app_payload"] = [
        {
            "id": producto.id,
            "nombre": producto.nombre,
            "codigo": producto.codigo or "",
            "precio": float(producto.precio or 0),
            "impuesto": float((producto.impuesto_predeterminado or impuesto_default).porcentaje if (producto.impuesto_predeterminado or impuesto_default) else 0),
            "tipo_item": producto.tipo_item,
            "stock": float(producto.stock_actual),
        }
        for producto in productos_app_qs
    ]
    contexto["impuestos_app_payload"] = [
        {
            "id": impuesto.id,
            "nombre": impuesto.nombre,
            "porcentaje": float(impuesto.porcentaje or 0),
        }
        for impuesto in impuestos_qs
    ]
    contexto["bodegas_app_payload"] = [
        {"id": bodega.id, "nombre": bodega.nombre}
        for bodega in BodegaInventario.objects.filter(empresa=empresa, activa=True).order_by("tipo", "nombre")
    ]
    try:
        asegurar_cuentas_financieras_base_honduras(empresa)
    except Exception:
        logger.exception("No se pudieron asegurar cuentas financieras para app movil de %s", empresa.id)
    cuentas_app_qs = CuentaFinanciera.objects.filter(empresa=empresa, activa=True).order_by("tipo", "nombre")
    contexto["cuentas_app_payload"] = [
        {"id": cuenta.id, "nombre": cuenta.nombre, "tipo": cuenta.tipo}
        for cuenta in cuentas_app_qs
    ]
    contexto["metodos_pago_app"] = PagoFactura.METODOS
    contexto["precios_incluyen_impuesto_app"] = bool(empresa.slug in {"hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"})
    response = render(request, "crm/agenda_mobile.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def agenda_mobile_manifest(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    icono = empresa.logo.url if empresa.logo else "/static/crm/hospital-mia-app.svg"
    icon_type = "image/svg+xml"
    if icono.lower().endswith((".jpg", ".jpeg")):
        icon_type = "image/jpeg"
    elif icono.lower().endswith(".png"):
        icon_type = "image/png"
    return JsonResponse(
        {
            "name": f"Agenda · {empresa.nombre}",
            "short_name": empresa.nombre[:24],
            "description": "Calendario móvil de citas conectado a DV Solutions ERP.",
            "id": inicio,
            "start_url": inicio,
            "scope": inicio,
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#f4f8fb",
            "theme_color": "#12324a",
            "icons": [
                {"src": icono, "sizes": "192x192", "type": icon_type, "purpose": "any"},
                {"src": icono, "sizes": "512x512", "type": icon_type, "purpose": "any maskable"},
            ],
        },
        content_type="application/manifest+json",
    )


def agenda_mobile_service_worker(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    script = f"""
const APP_HOME = {inicio!r};
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("notificationclick", event => {{
  event.notification.close();
  const target = event.notification.data?.url || APP_HOME;
  event.waitUntil(self.clients.matchAll({{type:"window", includeUncontrolled:true}}).then(clients => {{
    const visible = clients.find(client => "focus" in client);
    if (visible) {{ visible.navigate(target); return visible.focus(); }}
    return self.clients.openWindow(target);
  }}));
}});
self.addEventListener("message", event => {{
  if (event.data?.type !== "SHOW_APPOINTMENT") return;
  const payload = event.data.payload || {{}};
  self.registration.showNotification(payload.title || "Próxima cita", {{
    body: payload.body || "",
    icon: payload.icon || "/static/crm/hospital-mia-app.svg",
    badge: "/static/crm/hospital-mia-app.svg",
    tag: `cita-${{payload.id || "agenda"}}`,
    data: {{url: payload.url || APP_HOME}},
  }});
}});
"""
    response = HttpResponse(script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = inicio
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@login_required
def buscar_pacientes_cita(request, empresa_slug):
    empresa_operativa = _empresa_desde_slug(empresa_slug)
    if not request.user.puede_acceder_empresa(empresa_operativa):
        return JsonResponse({"results": [], "error": "Acceso no autorizado."}, status=403)
    if not (
        request.user.tiene_permiso_erp("puede_citas", empresa_operativa)
        or request.user.tiene_alguna_permision_facturacion_empresa(empresa_operativa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa_operativa)
    ):
        return JsonResponse({"results": [], "error": "Sin permiso para gestionar citas."}, status=403)
    empresa = _empresa_origen_agenda_mobile(empresa_operativa)

    query = " ".join((request.GET.get("q") or "").split())
    pacientes = Paciente.objects.filter(empresa=empresa, activo=True)
    for termino in query.split():
        pacientes = pacientes.filter(
            Q(nombre__icontains=termino)
            | Q(primer_nombre__icontains=termino)
            | Q(segundo_nombre__icontains=termino)
            | Q(primer_apellido__icontains=termino)
            | Q(segundo_apellido__icontains=termino)
            | Q(identidad__icontains=termino)
            | Q(expediente_codigo__icontains=termino)
            | Q(telefono__icontains=termino)
            | Q(whatsapp__icontains=termino)
            | Q(correo__icontains=termino)
        )
    pacientes = pacientes.order_by("-fecha_creacion", "nombre")[:12]
    return JsonResponse({
        "results": [
            {
                "id": paciente.id,
                "nombre": paciente.nombre,
                "documento": paciente.identidad or "",
                "expediente": paciente.expediente_codigo,
                "telefono": paciente.whatsapp or paciente.telefono or "",
                "correo": paciente.correo or "",
            }
            for paciente in pacientes
        ]
    })


@login_required
def buscar_clientes_cita(request, empresa_slug):
    empresa_operativa = _empresa_desde_slug(empresa_slug)
    if not request.user.puede_acceder_empresa(empresa_operativa):
        return JsonResponse({"results": [], "error": "Acceso no autorizado."}, status=403)
    if not (
        request.user.tiene_permiso_erp("puede_citas", empresa_operativa)
        or request.user.tiene_alguna_permision_facturacion_empresa(empresa_operativa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa_operativa)
    ):
        return JsonResponse({"results": [], "error": "Sin permiso para gestionar citas."}, status=403)
    empresa = _empresa_origen_agenda_mobile(empresa_operativa)

    query = " ".join((request.GET.get("q") or "").split())
    clientes = Cliente.objects.filter(empresa=empresa, activo=True)
    for termino in query.split():
        clientes = clientes.filter(
            Q(nombre__icontains=termino)
            | Q(rtn__icontains=termino)
            | Q(telefono__icontains=termino)
            | Q(telefono_whatsapp__icontains=termino)
            | Q(correo__icontains=termino)
        )
    clientes = clientes.order_by("-id", "nombre")[:12]
    return JsonResponse({
        "results": [
            {
                "id": cliente.id,
                "nombre": cliente.nombre,
                "documento": cliente.rtn or "",
                "expediente": "",
                "telefono": cliente.telefono_whatsapp or cliente.telefono or "",
                "correo": cliente.correo or "",
            }
            for cliente in clientes
        ]
    })


@login_required
@require_POST
def crear_paciente_rapido_cita(request, empresa_slug):
    empresa_operativa = _empresa_desde_slug(empresa_slug)
    if not request.user.puede_acceder_empresa(empresa_operativa):
        return JsonResponse({"ok": False, "error": "Acceso no autorizado."}, status=403)
    if not (
        request.user.tiene_permiso_erp("puede_citas", empresa_operativa)
        or request.user.tiene_alguna_permision_facturacion_empresa(empresa_operativa)
        or request.user.tiene_alguna_permision_clinica_empresa(empresa_operativa)
    ):
        return JsonResponse({"ok": False, "error": "Sin permiso para crear pacientes desde citas."}, status=403)
    empresa = _empresa_origen_agenda_mobile(empresa_operativa)
    es_clinica = empresa.tipo_solucion == "clinica" or empresa.tiene_modulo_activo("clinica_medica")
    if not es_clinica:
        return JsonResponse(
            {"ok": False, "error": "La creación de pacientes solo está disponible para empresas clínicas."},
            status=403,
        )

    form = PacienteRapidoCitaForm(request.POST, empresa=empresa)
    if not form.is_valid():
        errores = {
            campo: [str(error) for error in lista]
            for campo, lista in form.errors.items()
        }
        return JsonResponse(
            {"ok": False, "error": "Revisa los datos indicados.", "errors": errores},
            status=400,
        )

    from clinica.views import _proximo_codigo_expediente, _sincronizar_cliente_facturacion_paciente

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        codigo = _proximo_codigo_expediente(empresa)
        prefijo, numero = codigo.rsplit("-", 1)
        while Paciente.objects.filter(empresa=empresa, expediente_codigo=codigo).exists():
            numero = str(int(numero) + 1).zfill(5)
            codigo = f"{prefijo}-{numero}"

        paciente = form.save(commit=False)
        paciente.empresa = empresa
        paciente.expediente_codigo = codigo
        paciente.creado_por = request.user
        paciente.activo = True
        paciente.save()
        _sincronizar_cliente_facturacion_paciente(paciente)

    return JsonResponse({
        "ok": True,
        "paciente": {
            "id": paciente.id,
            "nombre": paciente.nombre,
            "expediente": paciente.expediente_codigo,
            "documento": paciente.identidad or "",
            "telefono": paciente.whatsapp or paciente.telefono or "",
            "label": str(paciente),
        },
    })


@login_required
@require_POST
def crear_tipo_consulta_rapido(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if empresa.slug != "hospital_mia":
        return JsonResponse(
            {"ok": False, "error": "Esta función está disponible únicamente en Hospital Mía."},
            status=403,
        )
    if not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"ok": False, "error": "Acceso no autorizado."}, status=403)
    if not (
        request.user.is_superuser
        or request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_permiso_erp("puede_configuracion_clinica", empresa)
    ):
        return JsonResponse(
            {"ok": False, "error": "Tu usuario no tiene permiso para administrar tipos de consulta."},
            status=403,
        )

    nombre = " ".join((request.POST.get("nombre") or "").split())
    categoria = (request.POST.get("categoria") or "consulta").strip()
    duracion_texto = (request.POST.get("duracion_minutos") or "30").strip()
    color_calendario = (request.POST.get("color_calendario") or "").strip().upper()
    categorias = dict(ServicioClinico.CATEGORIA_CHOICES)
    errores = {}
    if len(nombre) < 3:
        errores["nombre"] = ["Escribe un nombre de al menos 3 caracteres."]
    elif len(nombre) > 180:
        errores["nombre"] = ["El nombre no puede superar 180 caracteres."]
    if categoria not in categorias:
        errores["categoria"] = ["Selecciona una categoría válida."]
    if color_calendario and not re.fullmatch(r"#[0-9A-F]{6}", color_calendario):
        errores["color_calendario"] = ["Selecciona un color válido para el calendario."]
    try:
        duracion_minutos = int(duracion_texto)
        if duracion_minutos < 5 or duracion_minutos > 720:
            raise ValueError
    except (TypeError, ValueError):
        errores["duracion_minutos"] = ["La duración debe estar entre 5 y 720 minutos."]
    if errores:
        return JsonResponse(
            {"ok": False, "error": "Revisa los datos indicados.", "errors": errores},
            status=400,
        )

    servicio = ServicioClinico.objects.filter(empresa=empresa, nombre__iexact=nombre).first()
    creado = servicio is None
    if servicio is None:
        servicio = ServicioClinico.objects.create(
            empresa=empresa,
            nombre=nombre,
            categoria=categoria,
            duracion_minutos=duracion_minutos,
            color_calendario=color_calendario,
            activo=True,
        )
    else:
        servicio.nombre = nombre
        servicio.categoria = categoria
        servicio.duracion_minutos = duracion_minutos
        servicio.color_calendario = color_calendario
        servicio.activo = True
        servicio.save(update_fields=["nombre", "categoria", "duracion_minutos", "color_calendario", "activo"])

    return JsonResponse({
        "ok": True,
        "creado": creado,
        "servicio": {
            "id": servicio.id,
            "nombre": servicio.nombre,
            "categoria": servicio.categoria,
            "categoria_label": servicio.get_categoria_display(),
            "duracion_minutos": servicio.duracion_minutos,
            "color_calendario": servicio.color_calendario,
        },
    })


@login_required
@require_POST
def crear_tratamiento_rapido(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if empresa.slug != "hospital_mia" or not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"ok": False, "error": "Acceso no autorizado."}, status=403)
    if not (
        request.user.is_superuser
        or request.user.tiene_permiso_erp("puede_citas", empresa)
        or request.user.tiene_permiso_erp("puede_configuracion_clinica", empresa)
    ):
        return JsonResponse({"ok": False, "error": "No tiene permiso para agregar tratamientos."}, status=403)
    nombre = " ".join((request.POST.get("nombre") or "").split())
    if len(nombre) < 3 or len(nombre) > 180:
        return JsonResponse(
            {"ok": False, "error": "Escriba un nombre de tratamiento entre 3 y 180 caracteres."},
            status=400,
        )
    opcion, creada = OpcionServicioAgenda.objects.get_or_create(
        empresa=empresa,
        categoria="tratamientos",
        nombre__iexact=nombre,
        defaults={"nombre": nombre, "creado_por": request.user, "activo": True},
    )
    if not creada and not opcion.activo:
        opcion.activo = True
        opcion.save(update_fields=["activo"])
    return JsonResponse({"ok": True, "creada": creada, "opcion": {"id": opcion.id, "nombre": opcion.nombre}})


@login_required
def progreso_servicios_paciente(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if empresa.slug != "hospital_mia" or not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"ok": False, "error": "Acceso no autorizado."}, status=403)
    try:
        paciente_id = int(request.GET.get("paciente") or 0)
    except (TypeError, ValueError):
        paciente_id = 0
    paciente = Paciente.objects.filter(empresa=empresa, activo=True, id=paciente_id).first()
    if not paciente:
        return JsonResponse({"ok": False, "error": "Seleccione un paciente válido."}, status=400)
    completadas = CitaCliente.objects.filter(
        empresa=empresa,
        paciente=paciente,
        estado="realizada",
        sesion_servicio__isnull=False,
    ).select_related("servicio_clinico")
    terapias = []
    camara = []
    formulario_recurso = CitaClienteForm(empresa=empresa)
    for cita in completadas:
        recurso = formulario_recurso._recurso_capacidad_servicio(cita.servicio_clinico)
        if recurso == "terapias" and cita.fase_servicio:
            terapias.append({"fase": cita.fase_servicio, "sesion": cita.sesion_servicio})
        elif recurso == "camara_hiperbarica":
            camara.append(cita.sesion_servicio)
    return JsonResponse({"ok": True, "terapias": terapias, "camara": camara})


def _numero_contacto_cita(cita):
    if cita.paciente_id:
        return cita.paciente.whatsapp or cita.paciente.telefono or ""
    if cita.cliente_id:
        return cita.cliente.telefono_whatsapp or cita.cliente.telefono or ""
    return ""


def _enviar_aviso_cita_whatsapp(cita, *, aviso):
    config = ConfiguracionCRM.objects.filter(empresa=cita.empresa).first()
    if not config or not config.whatsapp_activo:
        raise WhatsAppAPIError("WhatsApp API no esta activo en CRM.")
    local = timezone.localtime(cita.fecha_hora)
    return enviar_plantilla_cita_whatsapp(
        config,
        _numero_contacto_cita(cita),
        paciente=cita.display_cliente,
        aviso=aviso,
        fecha=local.strftime("%d/%m/%Y"),
        hora=local.strftime("%I:%M %p"),
        consulta=cita.display_servicio,
        profesional=cita.display_responsable,
    )


def _redirect_agenda_accion(request, empresa):
    vista = request.POST.get("vista", "mes")
    fecha = request.POST.get("fecha", timezone.localdate().isoformat())
    return redirect(f"{reverse('agenda_citas', args=[empresa.slug])}?vista={vista}&fecha={fecha}")


@login_required
@require_POST
def cancelar_cita_whatsapp(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente.objects.select_related("paciente", "cliente", "servicio_clinico", "profesional_salud"), empresa=empresa, id=cita_id)
    motivo = (request.POST.get("motivo") or "").strip()
    nota = f"Cita cancelada desde agenda el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}."
    if motivo:
        nota = f"{nota} Motivo: {motivo}"
    cita.estado = "cancelada"
    cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
    cita.save(update_fields=["estado", "observacion"])
    cita.notificaciones_whatsapp.filter(estado__in=["pendiente", "error"]).update(estado="omitido")
    _sincronizar_cita_clinica(cita)
    try:
        _enviar_aviso_cita_whatsapp(cita, aviso="cita cancelada")
        messages.success(request, "Cita cancelada y aviso enviado por WhatsApp.")
    except WhatsAppAPIError as exc:
        messages.warning(request, f"Cita cancelada, pero no se pudo enviar WhatsApp: {exc}")
    return _redirect_agenda_accion(request, empresa)


@login_required
@require_POST
def reagendar_cita_whatsapp(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente.objects.select_related("paciente", "cliente", "servicio_clinico", "profesional_salud"), empresa=empresa, id=cita_id)
    nueva_fecha_raw = request.POST.get("nueva_fecha_hora")
    if not nueva_fecha_raw:
        fecha_form = (request.POST.get("nueva_fecha") or "").strip()
        hora_form = (request.POST.get("nueva_hora") or "").strip()
        periodo_form = (request.POST.get("nueva_periodo") or "AM").strip().upper()
        try:
            hora_num, minuto_num = [int(parte) for parte in hora_form.split(":", 1)]
            if periodo_form == "PM" and hora_num != 12:
                hora_num += 12
            if periodo_form == "AM" and hora_num == 12:
                hora_num = 0
            nueva_fecha_raw = f"{fecha_form}T{hora_num:02d}:{minuto_num:02d}"
        except (TypeError, ValueError):
            nueva_fecha_raw = ""
    nueva_fecha = parse_datetime(nueva_fecha_raw or "")
    if not nueva_fecha:
        messages.error(request, "Selecciona una nueva fecha y hora valida para reagendar.")
        return _redirect_agenda_accion(request, empresa)
    if timezone.is_naive(nueva_fecha):
        nueva_fecha = timezone.make_aware(nueva_fecha, timezone.get_current_timezone())
    fecha_anterior = timezone.localtime(cita.fecha_hora)
    cita.fecha_hora = nueva_fecha
    cita.estado = "confirmada" if cita.estado == "cancelada" else cita.estado
    nota = (
        f"Cita reagendada desde agenda el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}. "
        f"Antes: {fecha_anterior:%d/%m/%Y %I:%M %p}. Nueva: {timezone.localtime(nueva_fecha):%d/%m/%Y %I:%M %p}."
    )
    cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
    cita.save(update_fields=["fecha_hora", "estado", "observacion"])
    _sincronizar_cita_clinica(cita)
    programar_notificaciones_cita(cita)
    try:
        _enviar_aviso_cita_whatsapp(cita, aviso="cita reagendada")
        messages.success(request, "Cita reagendada y aviso enviado por WhatsApp.")
    except WhatsAppAPIError as exc:
        messages.warning(request, f"Cita reagendada, pero no se pudo enviar WhatsApp: {exc}")
    local = timezone.localtime(cita.fecha_hora)
    return redirect(f"{reverse('agenda_citas', args=[empresa.slug])}?vista=dia&fecha={local.date().isoformat()}")


@login_required
@require_POST
def actualizar_estado_cita(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id)
    estado = request.POST.get("estado")
    estados_validos = {codigo for codigo, _ in CitaCliente.ESTADO_CHOICES}
    if estado not in estados_validos:
        messages.error(request, "El estado solicitado no es válido.")
    else:
        cita.estado = estado
        cita.save(update_fields=["estado"])
        _sincronizar_cita_clinica(cita)
        programar_notificaciones_cita(cita)
        messages.success(request, f"Cita marcada como {cita.get_estado_display()}.")
    regreso_movil = request.POST.get("return_to") == "mobile"
    vista = request.POST.get("vista", "dia" if regreso_movil else "mes")
    fecha = request.POST.get("fecha", timezone.localdate().isoformat())
    url = reverse("agenda_mobile" if regreso_movil else "agenda_citas", args=[empresa.slug])
    return redirect(f"{url}?vista={vista}&fecha={fecha}")


@login_required
@require_http_methods(["GET", "POST"])
def eliminar_cita(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id)
    datos_regreso = request.POST if request.method == "POST" else request.GET
    regreso_movil = datos_regreso.get("return_to") == "mobile"
    vista = datos_regreso.get("vista", "dia" if regreso_movil else "mes")
    fecha = datos_regreso.get("fecha", timezone.localtime(cita.fecha_hora).date().isoformat())
    url = reverse("agenda_mobile" if regreso_movil else "agenda_citas", args=[empresa.slug])

    if request.method == "GET":
        return render(request, "crm/confirmar_eliminar_cita.html", {
            "empresa": empresa,
            "cita": cita,
            "vista": vista,
            "fecha": fecha,
            "regreso_movil": regreso_movil,
            "cancel_url": f"{url}?vista={vista}&fecha={fecha}",
        })

    motivo = (request.POST.get("motivo_eliminacion") or "").strip()

    if len(motivo) < 5:
        messages.error(request, "Explica el motivo de la eliminación con al menos 5 caracteres.")
        return redirect(f"{url}?vista={vista}&fecha={fecha}")

    referencia = cita.display_servicio or cita.titulo
    paciente = cita.display_cliente
    cita_clinica = cita.cita_clinica
    with transaction.atomic():
        # Los recordatorios de WhatsApp se eliminan en cascada junto con la cita.
        cita.delete()
        # La agenda clínica es el registro operativo vinculado; no debe quedar huérfano.
        if cita_clinica:
            cita_clinica.delete()

    messages.success(request, f"Cita eliminada: {referencia} · {paciente}.")
    return redirect(f"{url}?vista={vista}&fecha={fecha}")
