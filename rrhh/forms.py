from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado


class EditoresPlanillaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionRRHHEmpresa
        fields = ["editores_planilla"]
        widgets = {"editores_planilla": forms.CheckboxSelectMultiple}
        labels = {"editores_planilla": "Personas autorizadas para editar planillas"}
        help_texts = {
            "editores_planilla": (
                "Daniel Varela siempre conserva el permiso. Marca aquí únicamente a las personas adicionales."
            )
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        Usuario = get_user_model()
        if empresa:
            self.fields["editores_planilla"].queryset = (
                Usuario.objects.filter(is_active=True)
                .filter(Q(empresa=empresa) | Q(empresas_acceso=empresa))
                .exclude(Q(email__iexact="dannyvarela25@gmail.com") | Q(username__iexact="dannyvarela25"))
                .distinct()
                .order_by("first_name", "last_name", "email", "username")
            )
        else:
            self.fields["editores_planilla"].queryset = Usuario.objects.none()


class ConfiguracionRRHHEmpresaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionRRHHEmpresa
        fields = [
            "ihss_trabajador_porcentaje",
            "ihss_techo_mensual",
            "rap_trabajador_porcentaje",
            "aplicar_rap",
            "isr_porcentaje_base",
            "hora_extra_diurna_factor",
            "hora_extra_nocturna_factor",
            "hora_extra_feriado_factor",
            "dias_base_mes",
            "activa",
        ]


class EmpleadoForm(forms.ModelForm):
    class Meta:
        model = Empleado
        fields = [
            "codigo",
            "nombres",
            "apellidos",
            "identidad",
            "rtn",
            "foto",
            "fecha_nacimiento",
            "fecha_ingreso",
            "fecha_salida",
            "puesto",
            "departamento",
            "correo",
            "telefono",
            "direccion",
            "salario_mensual",
            "tipo_salario",
            "banco",
            "cuenta_bancaria",
            "aplica_ihss",
            "aplica_rap",
            "aplica_isr",
            "estado",
            "observacion",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
            "fecha_ingreso": forms.DateInput(attrs={"type": "date"}),
            "fecha_salida": forms.DateInput(attrs={"type": "date"}),
            "direccion": forms.Textarea(attrs={"rows": 3}),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa


class PeriodoPlanillaForm(forms.ModelForm):
    class Meta:
        model = PeriodoPlanilla
        fields = ["nombre", "frecuencia", "fecha_inicio", "fecha_fin", "fecha_pago", "incluir_13avo", "incluir_14avo"]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"type": "date"}),
            "fecha_pago": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "nombre": "Nombre de la planilla",
            "frecuencia": "Frecuencia de pago",
            "fecha_inicio": "Fecha inicial",
            "fecha_fin": "Fecha final",
            "fecha_pago": "Fecha de pago",
            "incluir_13avo": "Incluir 13avo proporcional",
            "incluir_14avo": "Incluir 14avo proporcional",
        }


class MovimientoPlanillaForm(forms.ModelForm):
    class Meta:
        model = MovimientoPlanilla
        fields = ["empleado", "tipo", "descripcion", "monto", "fecha"]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["empleado"].queryset = Empleado.objects.filter(empresa=empresa, estado="activo").order_by("nombres", "apellidos")
        else:
            self.fields["empleado"].queryset = Empleado.objects.none()


class DetallePlanillaForm(forms.ModelForm):
    class Meta:
        model = DetallePlanilla
        fields = [
            "dias_pagados",
            "salario_base",
            "horas_extra_diurnas",
            "horas_extra_nocturnas",
            "horas_extra_feriado",
            "monto_horas_extra",
            "bonos",
            "comisiones",
            "decimo_tercero",
            "decimo_cuarto",
            "ihss",
            "rap",
            "isr",
            "prestamos",
            "otras_deducciones",
            "observacion",
        ]
        widgets = {
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "dias_pagados": "Dias pagados",
            "salario_base": "Salario base",
            "horas_extra_diurnas": "Horas extra diurnas",
            "horas_extra_nocturnas": "Horas extra nocturnas",
            "horas_extra_feriado": "Horas extra feriado",
            "monto_horas_extra": "Monto horas extra",
            "bonos": "Bonos",
            "comisiones": "Comisiones",
            "decimo_tercero": "13avo",
            "decimo_cuarto": "14avo",
            "ihss": "IHSS",
            "rap": "RAP",
            "isr": "ISR",
            "prestamos": "Préstamos",
            "otras_deducciones": "Otras deducciones",
            "observacion": "Observación",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != "observacion":
                field.widget.attrs.update({"step": "0.01", "min": "0"})
        self.fields["monto_horas_extra"].widget.attrs.update({"readonly": "readonly"})
        self.fields["monto_horas_extra"].help_text = "Se recalcula al guardar según las horas indicadas y los factores configurados."
        self.fields["salario_base"].help_text = (
            "Esta corrección aplica solo a esta planilla. Actualiza también el expediente del empleado si el nuevo sueldo debe usarse en períodos futuros."
        )


class VacacionEmpleadoForm(forms.ModelForm):
    class Meta:
        model = VacacionEmpleado
        fields = ["empleado", "fecha_inicio", "fecha_fin", "dias", "estado", "observacion"]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"type": "date"}),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["empleado"].queryset = Empleado.objects.filter(empresa=empresa).order_by("nombres", "apellidos")
        else:
            self.fields["empleado"].queryset = Empleado.objects.none()
