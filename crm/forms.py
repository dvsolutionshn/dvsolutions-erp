import json
import unicodedata
from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from facturacion.models import Cliente, Producto
from clinica.models import Paciente, ProfesionalSalud, ServicioClinico, asegurar_profesionales_agenda_base

from .models import (
    CampaniaMarketing,
    CitaCliente,
    ConfiguracionCRM,
    OpcionServicioAgenda,
    PlantillaMensaje,
    ProgramaCamaraHiperbarica,
    ProgramaTerapiaPostQuirurgica,
    SesionCamaraHiperbarica,
    SesionTerapiaPostQuirurgica,
)


def _normalizar_texto(valor):
    return (
        unicodedata.normalize("NFKD", str(valor or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        return [super(MultipleFileField, self).clean(item, initial) for item in data if item]


class SeleccionUnicaCheckboxField(forms.MultipleChoiceField):
    """Presenta opciones como casillas, pero conserva un unico valor en el modelo."""

    widget = forms.CheckboxSelectMultiple

    def prepare_value(self, value):
        if isinstance(value, str):
            return [value] if value else []
        return value

    def clean(self, value):
        valores = super().clean(value)
        if len(valores) > 1:
            raise forms.ValidationError("Seleccione solamente una opción.")
        return valores[0] if valores else ""


class ProgramaCamaraHiperbaricaForm(forms.ModelForm):
    class Meta:
        model = ProgramaCamaraHiperbarica
        fields = [
            "cirugia",
            "fecha_cirugia",
            "indicacion",
            "programa",
            "programa_otro",
            "orden_medica",
        ]
        widgets = {
            "fecha_cirugia": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "indicacion": forms.Textarea(attrs={"rows": 2}),
            "orden_medica": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "cirugia": "Cirugía o procedimiento relacionado",
            "fecha_cirugia": "Fecha de cirugía",
            "indicacion": "Indicación médica",
            "programa": "Programa indicado",
            "programa_otro": "Otro programa",
            "orden_medica": "Orden médica",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_cirugia"].input_formats = ["%Y-%m-%d"]


class SesionCamaraHiperbaricaForm(forms.ModelForm):
    CAMPOS_SI_NO = [
        "estado_general_estable",
        "sin_fiebre",
        "sin_dificultad_respiratoria",
        "sin_dolor_toracico",
        "sin_sintomas_neurologicos",
        "sin_dolor_oido",
        "compensa_ambos_oidos",
        "area_quirurgica_revisada",
        "seguridad_camara_verificada",
        "apto_para_sesion",
    ]
    CAMPOS_REQUERIDOS_FINALIZAR = CAMPOS_SI_NO + [
        "observaciones_previas",
        "presion_arterial_antes",
        "saturacion_oxigeno_antes",
        "presion_camara",
        "tiempo_minutos",
        "compensacion_oidos",
        "tolerancia",
        "presion_arterial_despues",
        "saturacion_oxigeno_despues",
        "evolucion_evento_adverso",
        "firma_control_previo",
        "firma_parametros",
        "nota_enfermeria",
        "firma_enfermeria",
    ]

    estado_general_estable = SeleccionUnicaCheckboxField(
        label="Estado general estable", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    sin_fiebre = SeleccionUnicaCheckboxField(
        label="Sin fiebre", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    sin_dificultad_respiratoria = SeleccionUnicaCheckboxField(
        label="Sin dificultad respiratoria", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    sin_dolor_toracico = SeleccionUnicaCheckboxField(
        label="Sin dolor torácico", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    sin_sintomas_neurologicos = SeleccionUnicaCheckboxField(
        label="Sin síntomas neurológicos", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    sin_dolor_oido = SeleccionUnicaCheckboxField(
        label="Sin dolor de oído", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    compensa_ambos_oidos = SeleccionUnicaCheckboxField(
        label="Compensa ambos oídos", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    area_quirurgica_revisada = SeleccionUnicaCheckboxField(
        label="Área quirúrgica revisada", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    seguridad_camara_verificada = SeleccionUnicaCheckboxField(
        label="Seguridad de la cámara verificada", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    apto_para_sesion = SeleccionUnicaCheckboxField(
        label="Apto para la sesión", choices=SesionCamaraHiperbarica.RESPUESTA_CHOICES, required=False
    )
    tolerancia = SeleccionUnicaCheckboxField(
        label="Tolerancia", choices=SesionCamaraHiperbarica.TOLERANCIA_CHOICES, required=False
    )

    class Meta:
        model = SesionCamaraHiperbarica
        fields = [
            "numero_sesion",
            "numero_sesion_adicional",
            "estado_general_estable",
            "sin_fiebre",
            "sin_dificultad_respiratoria",
            "sin_dolor_toracico",
            "sin_sintomas_neurologicos",
            "sin_dolor_oido",
            "compensa_ambos_oidos",
            "area_quirurgica_revisada",
            "seguridad_camara_verificada",
            "apto_para_sesion",
            "observaciones_previas",
            "firma_control_previo",
            "presion_arterial_antes",
            "saturacion_oxigeno_antes",
            "presion_camara",
            "tiempo_minutos",
            "compensacion_oidos",
            "tolerancia",
            "presion_arterial_despues",
            "saturacion_oxigeno_despues",
            "evolucion_evento_adverso",
            "firma_parametros",
            "nota_enfermeria",
            "firma_enfermeria",
        ]
        widgets = {
            "numero_sesion": forms.NumberInput(attrs={"min": 1, "max": 22}),
            "numero_sesion_adicional": forms.NumberInput(attrs={"min": 1, "max": 22}),
            "observaciones_previas": forms.Textarea(attrs={"rows": 3}),
            "evolucion_evento_adverso": forms.Textarea(attrs={"rows": 3}),
            "nota_enfermeria": forms.Textarea(attrs={"rows": 4}),
            "tiempo_minutos": forms.NumberInput(attrs={"min": 1, "max": 300}),
        }
        labels = {
            "numero_sesion": "Número de sesión",
            "numero_sesion_adicional": "Segunda sesión (opcional)",
            "observaciones_previas": "Observaciones del control previo",
            "firma_control_previo": "Nombre y firma del personal que autoriza",
            "presion_arterial_antes": "Presión arterial antes",
            "saturacion_oxigeno_antes": "Saturación de oxígeno antes",
            "presion_camara": "Presión de cámara",
            "tiempo_minutos": "Tiempo en minutos",
            "compensacion_oidos": "Compensación de oídos",
            "presion_arterial_despues": "Presión arterial después",
            "saturacion_oxigeno_despues": "Saturación de oxígeno después",
            "evolucion_evento_adverso": "Evolución o evento adverso",
            "firma_parametros": "Nombre y firma del control de parámetros",
            "nota_enfermeria": "Observaciones y nota de enfermería",
            "firma_enfermeria": "Nombre y firma de enfermería",
        }

    def __init__(self, *args, finalizar=False, bloqueada=False, **kwargs):
        self.finalizar = finalizar
        self.bloqueada = bloqueada
        super().__init__(*args, **kwargs)
        if bloqueada:
            for campo in self.fields.values():
                campo.disabled = True

    def clean(self):
        datos = super().clean()
        if self.finalizar:
            for nombre in self.CAMPOS_REQUERIDOS_FINALIZAR:
                valor = datos.get(nombre)
                if valor in (None, "", []):
                    self.add_error(nombre, "Complete este campo antes de finalizar la sesión.")
        return datos


class ProgramaTerapiaPostQuirurgicaForm(forms.ModelForm):
    class Meta:
        model = ProgramaTerapiaPostQuirurgica
        fields = ["cirugia", "fecha_cirugia"]
        widgets = {"fecha_cirugia": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        labels = {"cirugia": "Cirugía realizada", "fecha_cirugia": "Fecha de cirugía"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_cirugia"].input_formats = ["%Y-%m-%d"]


class SesionTerapiaPostQuirurgicaForm(forms.ModelForm):
    estado_paciente = forms.MultipleChoiceField(
        label="Estado del paciente",
        choices=SesionTerapiaPostQuirurgica.ESTADO_PACIENTE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    equipos_utilizados = forms.MultipleChoiceField(
        label="Protocolo / equipos utilizados",
        choices=SesionTerapiaPostQuirurgica.EQUIPO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    cuidados_realizados = forms.MultipleChoiceField(
        label="Terapia manual y cuidados",
        choices=SesionTerapiaPostQuirurgica.CUIDADO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    CAMPOS_REQUERIDOS_FINALIZAR = [
        "hora_inicio", "hora_finalizacion", "presion_arterial", "frecuencia_cardiaca",
        "frecuencia_respiratoria", "saturacion_oxigeno", "temperatura", "escala_dolor",
        "estado_paciente", "equipos_utilizados", "minutos_area", "cuidados_realizados",
        "nota_enfermeria", "enfermera_nombre", "firma_enfermeria",
    ]

    class Meta:
        model = SesionTerapiaPostQuirurgica
        fields = [
            "numero_sesion", "numero_sesion_adicional", "hora_inicio", "hora_finalizacion", "presion_arterial",
            "frecuencia_cardiaca", "frecuencia_respiratoria", "saturacion_oxigeno",
            "temperatura", "escala_dolor", "estado_paciente", "equipos_utilizados",
            "minutos_area", "cuidados_realizados", "cuidado_otro", "nota_enfermeria",
            "enfermera_nombre", "firma_enfermeria",
        ]
        widgets = {
            "numero_sesion": forms.NumberInput(attrs={"min": 1, "max": 12}),
            "numero_sesion_adicional": forms.NumberInput(attrs={"min": 1, "max": 12}),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "hora_finalizacion": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "escala_dolor": forms.NumberInput(attrs={"min": 0, "max": 10}),
            "nota_enfermeria": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "numero_sesion": "Sesión", "numero_sesion_adicional": "Segunda sesión (opcional)", "hora_inicio": "Hora inicio", "hora_finalizacion": "Hora final",
            "presion_arterial": "PA", "frecuencia_cardiaca": "FC", "frecuencia_respiratoria": "FR",
            "saturacion_oxigeno": "SpO₂", "temperatura": "Temperatura", "escala_dolor": "Dolor /10",
            "minutos_area": "Minutos / área", "cuidado_otro": "Otro cuidado",
            "nota_enfermeria": "Nota de enfermería", "enfermera_nombre": "Nombre de enfermera",
            "firma_enfermeria": "Firma",
        }

    def __init__(self, *args, finalizar=False, bloqueada=False, **kwargs):
        self.finalizar = finalizar
        super().__init__(*args, **kwargs)
        self.fields["hora_inicio"].input_formats = ["%H:%M"]
        self.fields["hora_finalizacion"].input_formats = ["%H:%M"]
        if bloqueada:
            for campo in self.fields.values():
                campo.disabled = True

    def clean(self):
        datos = super().clean()
        if self.finalizar:
            for nombre in self.CAMPOS_REQUERIDOS_FINALIZAR:
                if datos.get(nombre) in (None, "", []):
                    self.add_error(nombre, "Complete este campo antes de finalizar la sesión.")
        if "otro" in (datos.get("cuidados_realizados") or []) and not (datos.get("cuidado_otro") or "").strip():
            self.add_error("cuidado_otro", "Especifique el otro cuidado realizado.")
        return datos


class ConfiguracionCRMForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionCRM
        fields = [
            "whatsapp_activo",
            "whatsapp_api_version",
            "whatsapp_phone_number_id",
            "whatsapp_business_account_id",
            "whatsapp_token",
            "whatsapp_numero_prueba",
            "whatsapp_plantilla_prueba",
            "whatsapp_idioma_plantilla",
            "whatsapp_plantilla_marketing",
            "whatsapp_idioma_marketing",
            "whatsapp_plantilla_cita",
            "whatsapp_idioma_cita",
            "whatsapp_cita_incluir_enlace",
            "mensaje_cita_confirmacion",
            "mensaje_cita_recordatorio_7_dias",
            "mensaje_cita_recordatorio_1_dia",
            "mensaje_cita_cancelada",
            "mensaje_cita_reagendada",
            "whatsapp_plantilla_preconsulta",
            "whatsapp_idioma_preconsulta",
            "remitente_correo",
            "recordatorio_cumpleanos_activo",
            "cumpleanos_recordatorio_1_dia",
            "cumpleanos_recordatorio_7_dias",
            "recordatorio_citas_activo",
            "dias_alerta_producto",
        ]
        widgets = {
            "whatsapp_token": forms.PasswordInput(render_value=True),
            "mensaje_cita_confirmacion": forms.Textarea(attrs={"rows": 2}),
            "mensaje_cita_recordatorio_7_dias": forms.Textarea(attrs={"rows": 2}),
            "mensaje_cita_recordatorio_1_dia": forms.Textarea(attrs={"rows": 2}),
            "mensaje_cita_cancelada": forms.Textarea(attrs={"rows": 2}),
            "mensaje_cita_reagendada": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "whatsapp_cita_incluir_enlace": "La plantilla de citas incluye enlace de confirmacion",
            "mensaje_cita_confirmacion": "Texto para confirmacion de cita",
            "mensaje_cita_recordatorio_7_dias": "Texto para recordatorio 7 dias antes",
            "mensaje_cita_recordatorio_1_dia": "Texto para recordatorio 1 dia antes",
            "mensaje_cita_cancelada": "Texto para cita cancelada",
            "mensaje_cita_reagendada": "Texto para cita reagendada",
            "whatsapp_plantilla_preconsulta": "Plantilla WhatsApp para preconsulta",
            "whatsapp_idioma_preconsulta": "Idioma plantilla preconsulta",
            "recordatorio_cumpleanos_activo": "Enviar cumpleaños automaticamente",
            "cumpleanos_recordatorio_1_dia": "Enviar 1 día antes",
            "cumpleanos_recordatorio_7_dias": "Enviar 7 días antes",
        }
        help_texts = {
            "whatsapp_token": "Token de Meta/WhatsApp Cloud API. Guardalo solo si el cliente ya tiene credenciales.",
            "whatsapp_numero_prueba": "Numero autorizado para probar, con codigo de pais. Ejemplo: 50499999999.",
            "whatsapp_plantilla_prueba": "Para pruebas de Meta normalmente se usa hello_world.",
            "whatsapp_idioma_plantilla": "Para hello_world normalmente es en_US.",
            "whatsapp_plantilla_marketing": "Nombre exacto de la plantilla comercial aprobada en Meta. Puede ser el mismo nombre para varias empresas si comparten WhatsApp Business. Ejemplo: promo_general_imagen.",
            "whatsapp_idioma_marketing": "Idioma aprobado de la plantilla comercial. Para Spanish normalmente usa es.",
            "whatsapp_plantilla_cita": "Nombre exacto de la plantilla transaccional aprobada en Meta. Puede reutilizarse en varias empresas. Debe tener 6 variables: paciente, aviso, fecha, hora, tipo de consulta y profesional.",
            "whatsapp_idioma_cita": "Código de idioma aprobado para la plantilla de citas, normalmente es.",
            "whatsapp_cita_incluir_enlace": "Activalo solo cuando la plantilla aprobada en Meta tenga la variable del enlace para confirmar o cancelar la cita.",
            "mensaje_cita_confirmacion": "Texto que viaja como variable aviso. Ejemplo: confirmacion de cita.",
            "mensaje_cita_recordatorio_7_dias": "Texto que viaja como variable aviso. Ejemplo: recordatorio: falta una semana.",
            "mensaje_cita_recordatorio_1_dia": "Texto que viaja como variable aviso. Ejemplo: recordatorio: su cita es manana.",
            "mensaje_cita_cancelada": "Texto que viaja como variable aviso cuando se cancela desde el calendario.",
            "mensaje_cita_reagendada": "Texto que viaja como variable aviso cuando se cambia la fecha u hora.",
            "whatsapp_plantilla_preconsulta": "Nombre exacto aprobado en Meta. Puede reutilizarse en varias empresas. Debe tener 3 variables de cuerpo: paciente, tipo de preconsulta y enlace seguro.",
            "whatsapp_idioma_preconsulta": "Idioma exacto aprobado para esa plantilla. Si Meta la aprobo como Spanish, normalmente usa es.",
            "recordatorio_cumpleanos_activo": "El sistema revisa clientes activos con fecha de nacimiento y usa la plantilla activa de tipo Cumpleanos.",
            "cumpleanos_recordatorio_1_dia": "Programa el saludo a las 9:00 AM un dia antes del cumpleaños.",
            "cumpleanos_recordatorio_7_dias": "Programa el saludo a las 9:00 AM siete dias antes del cumpleaños.",
            "dias_alerta_producto": "Dias antes para alertar productos con fecha de seguimiento o vencimiento.",
        }


class PlantillaMensajeForm(forms.ModelForm):
    class Meta:
        model = PlantillaMensaje
        fields = ["nombre", "tipo", "canal", "asunto", "mensaje", "imagen_promocional", "activa"]
        widgets = {
            "mensaje": forms.Textarea(attrs={"rows": 6, "placeholder": "Ejemplo: Hola {{cliente}}, en {{empresa}} queremos celebrar contigo. Tenemos una atención especial por tu cumpleaños."}),
        }
        help_texts = {
            "mensaje": "Variables disponibles: {{cliente}}, {{empresa}}, {{fecha}}, {{producto}}.",
            "imagen_promocional": "Opcional. Se usa en campañas con imagen.",
        }


class CampaniaMarketingForm(forms.ModelForm):
    class Meta:
        model = CampaniaMarketing
        fields = ["nombre", "plantilla", "audiencia", "fecha_programada", "estado"]
        widgets = {
            "fecha_programada": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["plantilla"].queryset = PlantillaMensaje.objects.filter(empresa=empresa, activa=True)
        else:
            self.fields["plantilla"].queryset = PlantillaMensaje.objects.none()


class CitaClienteForm(forms.ModelForm):
    EMPRESAS_WHATSAPP_CITAS = {"hospital_mia", "medical_spa", "luque_aestetic"}
    EMPRESAS_CIRUGIA_EXTENDIDA = {"hospital_mia", "serviciosmedicos"}
    CAPACIDAD_RECURSOS_AGENDA = {
        "tratamientos": {"nombre": "Tratamientos", "capacidad": 4},
        "camara_hiperbarica": {"nombre": "Camaras hiperbaricas", "capacidad": 3},
        "terapias": {"nombre": "Terapias", "capacidad": 3},
        "spa": {"nombre": "Spa", "capacidad": 6},
    }
    SERVICIOS_AGENDA_BASE = {
        "hospital_mia": True,
        "medical_spa": True,
        "luque_aestetic": True,
        "serviciosmedicos": True,
    }
    SERVICIOS_AGENDA_PREDEFINIDOS = [
        ("Tratamientos", "tratamiento", 60),
        ("Camara hiperbarica", "tratamiento", 60),
        ("Terapias", "tratamiento", 60),
        ("Spa", "spa", 60),
    ]
    SERVICIOS_AGENDA_HOSPITAL_MIA = [
        ("Post Cirugía", "tratamiento", 60),
    ]

    HORAS_12 = [
        (f"{hora:02d}:{minuto:02d}", f"{hora:02d}:{minuto:02d}")
        for hora in range(1, 13)
        for minuto in (0, 15, 30, 45)
    ]
    fecha_cita = forms.DateField(
        label="Fecha y hora",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    hora_cita = forms.ChoiceField(label="Hora", required=False, choices=HORAS_12)
    periodo_cita = forms.ChoiceField(
        label="AM / PM",
        required=False,
        choices=(("AM", "AM"), ("PM", "PM")),
    )
    cirugia_hora_fin = forms.ChoiceField(label="Hora final estimada", required=False, choices=HORAS_12)
    cirugia_periodo_fin = forms.ChoiceField(
        label="AM / PM final",
        required=False,
        choices=(("AM", "AM"), ("PM", "PM")),
    )
    fotos_cirugia = MultipleFileField(
        label="Fotos o videos para la cirugia",
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*,video/*", "multiple": True}),
        help_text="Adjunta fotos o videos de referencia al momento de programar la cirugia.",
    )
    detalles_agenda = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = CitaCliente
        fields = ["cliente", "paciente", "producto", "servicio_clinico", "titulo", "fecha_hora", "duracion_minutos", "responsable", "profesional_salud", "estado", "pagada", "cirugia_detalle", "cirugia_fin_estimada", "observacion", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]
        widgets = {
            "fecha_hora": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "cirugia_detalle": forms.Textarea(attrs={"rows": 3, "placeholder": "Ejemplo: Abdominoplastia con liposuccion, zona a operar, preparacion especial o detalle clinico."}),
            "cirugia_fin_estimada": forms.HiddenInput(),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.es_clinica = bool(empresa and (empresa.tipo_solucion == "clinica" or empresa.tiene_modulo_activo("clinica_medica")))
        self.notificaciones_cita_activas = bool(empresa and empresa.slug in self.EMPRESAS_WHATSAPP_CITAS)
        self.cirugia_extendida_activa = bool(empresa and empresa.slug in self.EMPRESAS_CIRUGIA_EXTENDIDA)
        self.detalles_agenda_activos = bool(empresa and empresa.slug == "hospital_mia")
        if empresa:
            asegurar_profesionales_agenda_base(empresa)
            if empresa.slug in self.SERVICIOS_AGENDA_BASE:
                for servicio in ServicioClinico.objects.filter(empresa=empresa, activo=True):
                    nombre_normalizado = unicodedata.normalize("NFKD", servicio.nombre or "").encode("ascii", "ignore").decode("ascii").lower()
                    if "terapia" in nombre_normalizado and ("camara" in nombre_normalizado or "hiperbar" in nombre_normalizado):
                        ServicioClinico.objects.filter(pk=servicio.pk).update(activo=False)
                    if nombre_normalizado.strip() == "cita con nosotros":
                        ServicioClinico.objects.filter(pk=servicio.pk).update(activo=False)
                    if empresa.slug == "hospital_mia" and nombre_normalizado.strip() in {"hidrofacial", "hydrofacial"}:
                        ServicioClinico.objects.filter(pk=servicio.pk).update(activo=False)
                for nombre, categoria, duracion in self.SERVICIOS_AGENDA_PREDEFINIDOS:
                    servicio = (
                        ServicioClinico.objects.filter(empresa=empresa, nombre=nombre, activo=True).first()
                        or ServicioClinico.objects.filter(empresa=empresa, nombre=nombre).first()
                    )
                    if servicio:
                        cambios = []
                        if not servicio.activo:
                            servicio.activo = True
                            cambios.append("activo")
                        if servicio.categoria != categoria:
                            servicio.categoria = categoria
                            cambios.append("categoria")
                        if servicio.duracion_minutos != duracion:
                            servicio.duracion_minutos = duracion
                            cambios.append("duracion_minutos")
                        if cambios:
                            servicio.save(update_fields=cambios)
                    else:
                        ServicioClinico.objects.create(
                            empresa=empresa,
                            nombre=nombre,
                            categoria=categoria,
                            duracion_minutos=duracion,
                            activo=True,
                        )
                if empresa.slug == "hospital_mia":
                    for nombre, categoria, duracion in self.SERVICIOS_AGENDA_HOSPITAL_MIA:
                        servicio = (
                            ServicioClinico.objects.filter(empresa=empresa, nombre__iexact=nombre).first()
                        )
                        if servicio:
                            cambios = []
                            if not servicio.activo:
                                servicio.activo = True
                                cambios.append("activo")
                            if servicio.categoria != categoria:
                                servicio.categoria = categoria
                                cambios.append("categoria")
                            if servicio.duracion_minutos != duracion:
                                servicio.duracion_minutos = duracion
                                cambios.append("duracion_minutos")
                            if cambios:
                                servicio.save(update_fields=cambios)
                        else:
                            ServicioClinico.objects.create(
                                empresa=empresa,
                                nombre=nombre,
                                categoria=categoria,
                                duracion_minutos=duracion,
                                activo=True,
                            )
                    OpcionServicioAgenda.objects.get_or_create(
                        empresa=empresa,
                        categoria="tratamientos",
                        nombre="Hidrofacial",
                        defaults={"orden": 10, "activo": True},
                    )
            self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["producto"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["paciente"].queryset = Paciente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["servicio_clinico"].queryset = ServicioClinico.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["profesional_salud"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["cliente"].queryset = Cliente.objects.none()
            self.fields["producto"].queryset = Producto.objects.none()
            self.fields["paciente"].queryset = Paciente.objects.none()
            self.fields["servicio_clinico"].queryset = ServicioClinico.objects.none()
            self.fields["profesional_salud"].queryset = ProfesionalSalud.objects.none()
        self.fields["producto"].required = False
        self.fields["duracion_minutos"].label = "Duración (minutos)"
        self.fields.pop("fecha_hora")
        self.fields.pop("cirugia_fin_estimada")
        self.fields["cirugia_detalle"].label = "Tipo / detalle de cirugia"
        self.fields["cirugia_hora_fin"].label = "Hora final estimada"
        self.fields["cirugia_periodo_fin"].label = "AM / PM final"
        self.fields["cirugia_detalle"].required = False
        if self.instance and self.instance.pk and self.instance.fecha_hora:
            fecha_local = timezone.localtime(self.instance.fecha_hora)
            hora_12 = fecha_local.hour % 12 or 12
            valor_hora = f"{hora_12:02d}:{fecha_local.minute:02d}"
            if valor_hora not in dict(self.HORAS_12):
                self.fields["hora_cita"].choices = [
                    *self.HORAS_12,
                    (valor_hora, valor_hora),
                ]
            self.initial.update({
                "fecha_cita": fecha_local.date(),
                "hora_cita": valor_hora,
                "periodo_cita": "PM" if fecha_local.hour >= 12 else "AM",
            })
            if self.detalles_agenda_activos and (
                self.instance.opcion_servicio or self.instance.sesion_servicio
            ):
                recurso = self._recurso_capacidad_servicio(self.instance.servicio_clinico)
                opcion_id = ""
                if recurso == "tratamientos" and self.instance.opcion_servicio:
                    opcion = OpcionServicioAgenda.objects.filter(
                        empresa=empresa,
                        categoria="tratamientos",
                        nombre__iexact=self.instance.opcion_servicio,
                    ).first()
                    opcion_id = str(opcion.id) if opcion else ""
                elif recurso == "spa" and self.instance.opcion_servicio:
                    opciones_spa = {
                        "masaje": "masaje",
                        "hidroterapia": "hidroterapia",
                        "sauna": "sauna",
                        "vapor": "vapor",
                    }
                    opcion_id = opciones_spa.get(_normalizar_texto(self.instance.opcion_servicio).strip(), "")
                if recurso == "terapias":
                    clave = f"terapia-{self.instance.fase_servicio}-{self.instance.sesion_servicio}"
                elif recurso == "camara_hiperbarica":
                    clave = f"camara-{self.instance.sesion_servicio}"
                elif recurso == "tratamientos":
                    clave = f"tratamiento-{opcion_id or self.instance.opcion_servicio}"
                else:
                    clave = f"spa-{opcion_id or self.instance.opcion_servicio}"
                self.initial["detalles_agenda"] = json.dumps([{
                    "tipo": recurso,
                    "clave": clave,
                    "opcion_id": opcion_id,
                    "opcion_nombre": self.instance.opcion_servicio or "",
                    "fase": self.instance.fase_servicio,
                    "sesion": self.instance.sesion_servicio,
                    "hora": valor_hora,
                    "periodo": "PM" if fecha_local.hour >= 12 else "AM",
                }])
        if self.instance and self.instance.pk and self.instance.cirugia_fin_estimada:
            fin_local = timezone.localtime(self.instance.cirugia_fin_estimada)
            hora_fin_12 = fin_local.hour % 12 or 12
            valor_hora_fin = f"{hora_fin_12:02d}:{fin_local.minute:02d}"
            if valor_hora_fin not in dict(self.HORAS_12):
                self.fields["cirugia_hora_fin"].choices = [
                    *self.HORAS_12,
                    (valor_hora_fin, valor_hora_fin),
                ]
            self.initial.update({
                "cirugia_hora_fin": valor_hora_fin,
                "cirugia_periodo_fin": "PM" if fin_local.hour >= 12 else "AM",
            })
        if not self.cirugia_extendida_activa:
            for nombre in ["cirugia_detalle", "cirugia_hora_fin", "cirugia_periodo_fin", "fotos_cirugia"]:
                self.fields.pop(nombre, None)
        if not self.detalles_agenda_activos:
            self.fields.pop("detalles_agenda", None)
        if self.es_clinica:
            for nombre in ["cliente", "producto", "titulo", "responsable", "duracion_minutos"]:
                self.fields.pop(nombre)
            self.fields["paciente"].label = "Paciente"
            self.fields["paciente"].required = True
            self.fields["paciente"].error_messages["required"] = "Selecciona el paciente de la lista antes de guardar la cita."
            self.fields["servicio_clinico"].label = "Tipo de consulta"
            self.fields["servicio_clinico"].required = True
            self.fields["servicio_clinico"].error_messages["required"] = "Selecciona el tipo de consulta."
            self.fields["profesional_salud"].label = "Doctor / profesional"
            self.fields["profesional_salud"].required = True
            self.fields["profesional_salud"].error_messages["required"] = "Selecciona el doctor o profesional que atendera la cita."
            self.fields["observacion"].label = "Motivo o notas de la cita"
            self.fields["pagada"].label = "Cita pagada"
            self.fields["enviar_confirmacion_whatsapp"].label = "Enviar confirmación por WhatsApp al guardar"
            self.fields["recordatorio_semana_whatsapp"].label = "Recordar 7 días antes"
            self.fields["recordatorio_dia_whatsapp"].label = "Recordar 1 día antes"
            if self.notificaciones_cita_activas and not (self.instance and self.instance.pk):
                self.initial.setdefault("enviar_confirmacion_whatsapp", True)
                self.initial.setdefault("recordatorio_semana_whatsapp", True)
                self.initial.setdefault("recordatorio_dia_whatsapp", True)
            if not self.notificaciones_cita_activas:
                for nombre in ["enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]:
                    self.fields.pop(nombre)
            self.order_fields(["paciente", "servicio_clinico", "profesional_salud", "fecha_cita", "hora_cita", "periodo_cita", "detalles_agenda", "cirugia_hora_fin", "cirugia_periodo_fin", "cirugia_detalle", "fotos_cirugia", "estado", "pagada", "observacion", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"])
        else:
            for nombre in ["paciente", "servicio_clinico", "profesional_salud", "cirugia_detalle", "cirugia_hora_fin", "cirugia_periodo_fin", "fotos_cirugia", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]:
                self.fields.pop(nombre, None)
            self.fields["pagada"].label = "Cita pagada"
            self.order_fields(["cliente", "producto", "titulo", "fecha_cita", "hora_cita", "periodo_cita", "duracion_minutos", "responsable", "estado", "pagada", "observacion"])

    def _armar_fecha_hora(self, fecha, hora_texto, periodo):
        hora_12, minuto = (int(parte) for parte in hora_texto.split(":"))
        hora_24 = hora_12 % 12 + (12 if periodo == "PM" else 0)
        fecha_hora = datetime.combine(fecha, datetime.min.time()).replace(hour=hora_24, minute=minuto)
        return timezone.make_aware(fecha_hora)

    def _servicio_es_cirugia(self, servicio):
        if not servicio:
            return False
        categoria = (getattr(servicio, "categoria", "") or "").lower()
        nombre = _normalizar_texto(getattr(servicio, "nombre", "") or "")
        if "post cirugia" in nombre or "postquirurg" in nombre or "post quirurg" in nombre:
            return False
        return categoria == "cirugia" or "cirug" in nombre

    def _recurso_capacidad_servicio(self, servicio):
        if not servicio:
            return ""
        categoria = (getattr(servicio, "categoria", "") or "").lower()
        nombre = _normalizar_texto(getattr(servicio, "nombre", "") or "")
        if "post cirugia" in nombre or "postquirurg" in nombre or "post quirurg" in nombre:
            return ""
        texto = f"{categoria} {nombre}"
        if "camara" in texto or "cámara" in texto or "hiperbar" in texto:
            return "camara_hiperbarica"
        if "terapia" in texto:
            return "terapias"
        if categoria == "spa" or "spa" in texto:
            return "spa"
        if (
            categoria in {"tratamiento", "procedimiento"}
            or "tratamiento" in texto
            or "botox" in texto
            or "relleno" in texto
        ):
            return "tratamientos"
        return ""

    def _validar_capacidad_recurso(self, inicio, fin_bloque, servicio):
        recurso = self._recurso_capacidad_servicio(servicio)
        if not recurso or not self.empresa:
            return False

        config = self.CAPACIDAD_RECURSOS_AGENDA[recurso]
        citas = (
            CitaCliente.objects.filter(
                empresa=self.empresa,
                fecha_hora__date=timezone.localtime(inicio).date(),
            )
            .exclude(estado="cancelada")
            .select_related("servicio_clinico", "paciente", "cliente")
        )
        if self.instance and self.instance.pk:
            citas = citas.exclude(pk=self.instance.pk)

        ocupadas = 0
        ejemplos = []
        for cita in citas:
            if self._recurso_capacidad_servicio(cita.servicio_clinico) != recurso:
                continue
            cita_inicio, cita_fin = self._rango_bloqueado_cita(cita)
            if inicio < cita_fin and fin_bloque > cita_inicio:
                ocupadas += 1
                if len(ejemplos) < 3:
                    ejemplos.append(cita.display_cliente)

        if ocupadas >= config["capacidad"]:
            detalle = f" Pacientes en ese horario: {', '.join(ejemplos)}." if ejemplos else ""
            raise forms.ValidationError(
                f"No hay cubiculos disponibles para {config['nombre']} a esa hora. "
                f"Capacidad: {config['capacidad']}; ocupados: {ocupadas}.{detalle}"
            )
        return True

    def _rango_bloqueado_cita(self, cita):
        inicio = cita.fecha_hora
        if self.cirugia_extendida_activa and cita.cirugia_fin_estimada:
            return inicio, cita.cirugia_fin_estimada
        minutos = cita.duracion_minutos or getattr(cita.servicio_clinico, "duracion_minutos", None) or 30
        return inicio, inicio + timedelta(minutes=minutos)

    def _validar_traslapes_agenda_extendida(self, inicio, fin_bloque, profesional=None):
        if not self.cirugia_extendida_activa or not self.empresa:
            return
        citas = (
            CitaCliente.objects.filter(
                empresa=self.empresa,
                fecha_hora__date=timezone.localtime(inicio).date(),
            )
            .exclude(estado="cancelada")
            .select_related("servicio_clinico")
        )
        if profesional:
            citas = citas.filter(profesional_salud=profesional)
        if self.instance and self.instance.pk:
            citas = citas.exclude(pk=self.instance.pk)
        for cita in citas:
            cita_inicio, cita_fin = self._rango_bloqueado_cita(cita)
            if inicio < cita_fin and fin_bloque > cita_inicio:
                inicio_local = timezone.localtime(cita_inicio).strftime("%I:%M %p")
                fin_local = timezone.localtime(cita_fin).strftime("%I:%M %p")
                raise forms.ValidationError(
                    f"Ese horario se cruza con {cita.display_servicio} de {cita.display_cliente}, "
                    f"bloqueado de {inicio_local} a {fin_local}."
                )

    def _limpiar_detalles_agenda(self, servicio, fecha):
        if not self.detalles_agenda_activos or not servicio or not fecha:
            return []
        recurso = self._recurso_capacidad_servicio(servicio)
        if recurso not in {"terapias", "camara_hiperbarica", "tratamientos", "spa"}:
            return []
        bruto = (self.cleaned_data.get("detalles_agenda") or "").strip()
        if not bruto and "detalles_agenda" not in self.data:
            # Compatibilidad con integraciones anteriores que todavía envían una cita simple.
            return []
        if not bruto:
            self.add_error("detalles_agenda", "Selecciona al menos una sesión, tratamiento o servicio y asigna su hora.")
            return []
        try:
            filas = json.loads(bruto)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.add_error("detalles_agenda", "No se pudo leer el detalle de la atención. Selecciona nuevamente las opciones.")
            return []
        if not isinstance(filas, list) or not filas or len(filas) > 32:
            self.add_error("detalles_agenda", "Selecciona entre 1 y 32 opciones para esta atención.")
            return []

        opciones_tratamiento = {
            str(opcion.id): opcion.nombre
            for opcion in OpcionServicioAgenda.objects.filter(
                empresa=self.empresa, categoria="tratamientos", activo=True
            )
        }
        opciones_spa = {"masaje": "Masaje", "hidroterapia": "Hidroterapia", "sauna": "Sauna", "vapor": "Vapor"}
        limpias = []
        claves = set()
        for posicion, fila in enumerate(filas, start=1):
            if not isinstance(fila, dict):
                self.add_error("detalles_agenda", f"La opción {posicion} no es válida.")
                continue
            clave = str(fila.get("clave") or "").strip()
            if not clave or clave in claves:
                self.add_error("detalles_agenda", "No repitas la misma sesión o servicio.")
                continue
            claves.add(clave)
            hora = str(fila.get("hora") or "").strip()
            periodo = str(fila.get("periodo") or "").strip().upper()
            if hora not in dict(self.HORAS_12) or periodo not in {"AM", "PM"}:
                self.add_error("detalles_agenda", f"Selecciona una hora válida para la opción {posicion}.")
                continue
            inicio = self._armar_fecha_hora(fecha, hora, periodo)
            limpia = {"clave": clave, "hora": hora, "periodo": periodo, "inicio": inicio, "opcion": "", "fase": None, "sesion": None}
            if recurso == "terapias":
                try:
                    fase = int(fila.get("fase"))
                    sesion = int(fila.get("sesion"))
                except (TypeError, ValueError):
                    fase = sesion = 0
                maximo = 22 if fase == 1 else 10 if fase == 2 else 0
                if not maximo or not 1 <= sesion <= maximo:
                    self.add_error("detalles_agenda", "Selecciona una sesión válida de Terapias.")
                    continue
                limpia.update({"fase": fase, "sesion": sesion})
            elif recurso == "camara_hiperbarica":
                try:
                    sesion = int(fila.get("sesion"))
                except (TypeError, ValueError):
                    sesion = 0
                if not 1 <= sesion <= 22:
                    self.add_error("detalles_agenda", "Selecciona una sesión válida de Cámara hiperbárica.")
                    continue
                limpia["sesion"] = sesion
            elif recurso == "tratamientos":
                opcion_id = str(fila.get("opcion_id") or "")
                if not opcion_id and fila.get("opcion_nombre"):
                    nombre_buscado = str(fila.get("opcion_nombre")).strip().casefold()
                    opcion_id = next((clave for clave, nombre in opciones_tratamiento.items() if nombre.casefold() == nombre_buscado), "")
                if opcion_id not in opciones_tratamiento:
                    self.add_error("detalles_agenda", "Selecciona un tratamiento vigente de la lista.")
                    continue
                limpia["opcion"] = opciones_tratamiento[opcion_id]
            else:
                opcion_id = str(fila.get("opcion_id") or "").lower()
                if not opcion_id and fila.get("opcion_nombre"):
                    nombre_buscado = str(fila.get("opcion_nombre")).strip().casefold()
                    opcion_id = next(
                        (clave for clave, nombre in opciones_spa.items() if nombre.casefold() == nombre_buscado),
                        "",
                    )
                if opcion_id not in opciones_spa:
                    self.add_error("detalles_agenda", "Selecciona una opción válida de Spa.")
                    continue
                limpia["opcion"] = opciones_spa[opcion_id]
            limpias.append(limpia)
        return limpias

    def clean(self):
        cleaned_data = super().clean()
        fecha = cleaned_data.get("fecha_cita")
        hora_texto = cleaned_data.get("hora_cita")
        periodo = cleaned_data.get("periodo_cita")
        servicio = cleaned_data.get("servicio_clinico")
        detalles = self._limpiar_detalles_agenda(servicio, fecha)
        if detalles:
            hora_texto = detalles[0]["hora"]
            periodo = detalles[0]["periodo"]
            cleaned_data["detalles_agenda_limpios"] = detalles

        # Compatibilidad con integraciones y formularios anteriores al selector AM/PM.
        fecha_hora_anterior = (self.data.get("fecha_hora") or "").strip()
        inicio = None
        if not all((fecha, hora_texto, periodo)) and fecha_hora_anterior:
            try:
                fecha_hora = datetime.strptime(fecha_hora_anterior, "%Y-%m-%dT%H:%M")
                inicio = timezone.make_aware(fecha_hora)
                fecha = timezone.localtime(inicio).date()
                cleaned_data["fecha_hora_compuesta"] = inicio
            except ValueError:
                pass

        if inicio is None:
            if not fecha:
                self.add_error("fecha_cita", "Selecciona la fecha de la cita.")
            if not hora_texto:
                self.add_error("hora_cita", "Selecciona la hora de la cita.")
            if not periodo:
                self.add_error("periodo_cita", "Selecciona AM o PM.")
            if not all((fecha, hora_texto, periodo)):
                return cleaned_data

            inicio = self._armar_fecha_hora(fecha, hora_texto, periodo)
            cleaned_data["fecha_hora_compuesta"] = inicio
        profesional = cleaned_data.get("profesional_salud")
        fin_bloque = inicio + timedelta(minutes=(getattr(servicio, "duracion_minutos", None) or cleaned_data.get("duracion_minutos") or 30))
        hora_fin = cleaned_data.get("cirugia_hora_fin")
        periodo_fin = cleaned_data.get("cirugia_periodo_fin")
        fin_estimada = None

        if self.cirugia_extendida_activa and hora_fin:
            if not periodo_fin:
                self.add_error("cirugia_periodo_fin", "Selecciona AM o PM.")
            if periodo_fin:
                fin_estimada = self._armar_fecha_hora(fecha, hora_fin, periodo_fin)
                if fin_estimada <= inicio:
                    self.add_error(
                        "cirugia_hora_fin",
                        "La hora de finalizacion no puede ser igual ni menor que la hora de inicio. "
                        "Seleccione una hora final posterior para poder guardar la cita.",
                    )
                else:
                    cleaned_data["cirugia_fin_estimada_compuesta"] = fin_estimada
                    fin_bloque = fin_estimada

        if self.cirugia_extendida_activa and self._servicio_es_cirugia(servicio):
            if not (cleaned_data.get("cirugia_detalle") or "").strip():
                self.add_error("cirugia_detalle", "Describe el tipo de cirugia o el procedimiento.")
            if not hora_fin:
                self.add_error("cirugia_hora_fin", "Selecciona la hora final estimada.")
            if not periodo_fin:
                self.add_error("cirugia_periodo_fin", "Selecciona AM o PM.")
            if fin_estimada and fin_estimada > inicio:
                fin_bloque = fin_estimada
        else:
            cleaned_data["cirugia_detalle"] = ""
            cleaned_data["cirugia_fin_estimada_compuesta"] = fin_estimada

        if not self.errors and detalles:
            duracion = getattr(servicio, "duracion_minutos", None) or 60
            vistos = []
            for detalle in detalles:
                detalle_fin = detalle["inicio"] + timedelta(minutes=duracion)
                try:
                    usa_capacidad = self._validar_capacidad_recurso(detalle["inicio"], detalle_fin, servicio)
                    if not usa_capacidad:
                        self._validar_traslapes_agenda_extendida(detalle["inicio"], detalle_fin, profesional)
                except forms.ValidationError as exc:
                    self.add_error("detalles_agenda", exc)
                    break
                if any(detalle["inicio"] < fin and detalle_fin > otro_inicio for otro_inicio, fin in vistos):
                    self.add_error("detalles_agenda", "Las opciones de una misma atención no pueden cruzarse entre sí. Asigna horas consecutivas.")
                    break
                vistos.append((detalle["inicio"], detalle_fin))
        elif not self.errors:
            try:
                usa_capacidad = self._validar_capacidad_recurso(inicio, fin_bloque, servicio)
                if not usa_capacidad:
                    self._validar_traslapes_agenda_extendida(inicio, fin_bloque, profesional)
            except forms.ValidationError as exc:
                self.add_error("fecha_cita", exc)
        return cleaned_data

    def save(self, commit=True):
        cita = super().save(commit=False)
        cita.fecha_hora = self.cleaned_data["fecha_hora_compuesta"]
        cita.cirugia_fin_estimada = self.cleaned_data.get("cirugia_fin_estimada_compuesta")
        if self.es_clinica:
            cita.titulo = cita.servicio_clinico.nombre
            cita.responsable = cita.profesional_salud.nombre
            cita.cliente = cita.paciente.cliente
            cita.producto = None
            cita.duracion_minutos = cita.servicio_clinico.duracion_minutos
        if commit:
            cita.save()
        return cita


class PacienteRapidoCitaForm(forms.ModelForm):
    class Meta:
        model = Paciente
        fields = [
            "tipo_id",
            "identidad",
            "primer_nombre",
            "segundo_nombre",
            "primer_apellido",
            "segundo_apellido",
            "fecha_nacimiento",
            "sexo",
            "telefono",
            "whatsapp",
            "correo",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "tipo_id": "Tipo de documento",
            "identidad": "No. de documento",
            "primer_nombre": "Primer nombre",
            "segundo_nombre": "Segundo nombre",
            "primer_apellido": "Primer apellido",
            "segundo_apellido": "Segundo apellido",
            "fecha_nacimiento": "Fecha de nacimiento",
            "telefono": "Teléfono",
            "whatsapp": "WhatsApp",
            "correo": "Correo electrónico",
        }

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields["primer_nombre"].required = True
        self.fields["primer_apellido"].required = True
        self.fields["identidad"].required = bool(
            empresa and empresa.slug in CitaClienteForm.EMPRESAS_WHATSAPP_CITAS
        )
        if self.fields["identidad"].required:
            self.fields["identidad"].error_messages["required"] = "La identidad es obligatoria."
        self.fields["identidad"].widget.attrs.update({
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "autocomplete": "off",
            "placeholder": "Solo números, sin guiones",
        })
        self.fields["telefono"].widget.attrs.update({"inputmode": "tel"})
        self.fields["whatsapp"].widget.attrs.update({"inputmode": "tel"})

    def clean_identidad(self):
        identidad = (self.cleaned_data.get("identidad") or "").strip()
        if identidad and not identidad.isdigit():
            raise forms.ValidationError("El documento solo debe contener números, sin guiones ni espacios.")
        if identidad and self.empresa and Paciente.objects.filter(
            empresa=self.empresa,
            identidad=identidad,
            activo=True,
        ).exists():
            raise forms.ValidationError("Ya existe un paciente activo con este número de documento.")
        return identidad

    def clean(self):
        cleaned_data = super().clean()
        telefono = (cleaned_data.get("telefono") or "").strip()
        whatsapp = (cleaned_data.get("whatsapp") or "").strip()
        if not telefono and not whatsapp:
            self.add_error("whatsapp", "Ingresa al menos un teléfono o número de WhatsApp.")
        if whatsapp and not telefono:
            cleaned_data["telefono"] = whatsapp
        if telefono and not whatsapp:
            cleaned_data["whatsapp"] = telefono
        return cleaned_data
