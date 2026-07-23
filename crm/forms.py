from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from facturacion.models import Cliente, Producto
from clinica.models import Paciente, ProfesionalSalud, ServicioClinico, asegurar_profesionales_agenda_base

from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, PlantillaMensaje


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
        if empresa:
            asegurar_profesionales_agenda_base(empresa)
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
        if self.es_clinica:
            for nombre in ["cliente", "producto", "titulo", "responsable", "duracion_minutos"]:
                self.fields.pop(nombre)
            self.fields["paciente"].label = "Paciente"
            self.fields["paciente"].required = True
            self.fields["servicio_clinico"].label = "Tipo de consulta"
            self.fields["servicio_clinico"].required = True
            self.fields["profesional_salud"].label = "Doctor / profesional"
            self.fields["profesional_salud"].required = True
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
            self.order_fields(["paciente", "servicio_clinico", "profesional_salud", "fecha_cita", "hora_cita", "periodo_cita", "cirugia_hora_fin", "cirugia_periodo_fin", "cirugia_detalle", "fotos_cirugia", "estado", "pagada", "observacion", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"])
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
        nombre = (getattr(servicio, "nombre", "") or "").lower()
        return categoria == "cirugia" or "cirug" in nombre

    def _rango_bloqueado_cita(self, cita):
        inicio = cita.fecha_hora
        if self.cirugia_extendida_activa and self._servicio_es_cirugia(cita.servicio_clinico) and cita.cirugia_fin_estimada:
            return inicio, cita.cirugia_fin_estimada + timedelta(hours=1)
        minutos = cita.duracion_minutos or getattr(cita.servicio_clinico, "duracion_minutos", None) or 30
        return inicio, inicio + timedelta(minutes=minutos)

    def _validar_traslapes_serviciosmedicos(self, inicio, fin_bloque, profesional=None):
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

    def clean(self):
        cleaned_data = super().clean()
        fecha = cleaned_data.get("fecha_cita")
        hora_texto = cleaned_data.get("hora_cita")
        periodo = cleaned_data.get("periodo_cita")

        # Compatibilidad con integraciones y formularios anteriores al selector AM/PM.
        fecha_hora_anterior = (self.data.get("fecha_hora") or "").strip()
        if not all((fecha, hora_texto, periodo)) and fecha_hora_anterior:
            try:
                fecha_hora = datetime.strptime(fecha_hora_anterior, "%Y-%m-%dT%H:%M")
                inicio = timezone.make_aware(fecha_hora)
                cleaned_data["fecha_hora_compuesta"] = inicio
                return cleaned_data
            except ValueError:
                pass

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
        servicio = cleaned_data.get("servicio_clinico")
        profesional = cleaned_data.get("profesional_salud")
        fin_bloque = inicio + timedelta(minutes=(getattr(servicio, "duracion_minutos", None) or cleaned_data.get("duracion_minutos") or 30))

        if self.cirugia_extendida_activa and self._servicio_es_cirugia(servicio):
            if not (cleaned_data.get("cirugia_detalle") or "").strip():
                self.add_error("cirugia_detalle", "Describe el tipo de cirugia o el procedimiento.")
            hora_fin = cleaned_data.get("cirugia_hora_fin")
            periodo_fin = cleaned_data.get("cirugia_periodo_fin")
            if not hora_fin:
                self.add_error("cirugia_hora_fin", "Selecciona la hora final estimada.")
            if not periodo_fin:
                self.add_error("cirugia_periodo_fin", "Selecciona AM o PM.")
            if hora_fin and periodo_fin:
                fin_estimada = self._armar_fecha_hora(fecha, hora_fin, periodo_fin)
                if fin_estimada <= inicio:
                    self.add_error("cirugia_hora_fin", "La hora final debe ser posterior a la hora de inicio.")
                else:
                    cleaned_data["cirugia_fin_estimada_compuesta"] = fin_estimada
                    fin_bloque = fin_estimada + timedelta(hours=1)
        else:
            cleaned_data["cirugia_detalle"] = ""
            cleaned_data["cirugia_fin_estimada_compuesta"] = None

        if not self.errors:
            self._validar_traslapes_serviciosmedicos(inicio, fin_bloque, profesional)
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
