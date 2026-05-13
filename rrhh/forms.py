from django import forms

from .models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado


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
        fields = ["nombre", "frecuencia", "fecha_inicio", "fecha_fin", "fecha_pago", "incluir_14avo", "estado"]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"type": "date"}),
            "fecha_pago": forms.DateInput(attrs={"type": "date"}),
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
            "decimo_cuarto": "14avo",
            "ihss": "IHSS",
            "rap": "RAP",
            "isr": "ISR",
            "prestamos": "Prestamos",
            "otras_deducciones": "Otras deducciones",
            "observacion": "Observacion",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != "observacion":
                field.widget.attrs.update({"step": "0.01", "min": "0"})
        self.fields["monto_horas_extra"].widget.attrs.update({"readonly": "readonly"})
        self.fields["monto_horas_extra"].help_text = "Se recalcula al guardar segun las horas indicadas y los factores configurados."


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
