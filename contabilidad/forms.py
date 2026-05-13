from django import forms
from django.forms import inlineformset_factory

from facturacion.models import CompraInventario, Factura

from .models import AsientoContable, ClasificacionCompraFiscal, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, PeriodoContable, ReglaClasificacionBanco


class CuentaContableForm(forms.ModelForm):
    class Meta:
        model = CuentaContable
        fields = ["codigo", "nombre", "tipo", "cuenta_padre", "descripcion", "acepta_movimientos", "activa"]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
        cuentas_padre = CuentaContable.objects.none()
        if empresa:
            cuentas_padre = CuentaContable.objects.filter(empresa=empresa, activa=True).order_by("codigo", "nombre")
            if self.instance and self.instance.pk:
                cuentas_padre = cuentas_padre.exclude(pk=self.instance.pk)
        self.fields["cuenta_padre"].queryset = cuentas_padre
        self.fields["cuenta_padre"].required = False
        self.fields["cuenta_padre"].empty_label = "Cuenta principal / sin padre"
        textos = {
            "codigo": ("Codigo contable", "Ejemplo: 1.1.01 o 1101 segun tu estructura."),
            "nombre": ("Nombre de la cuenta", ""),
            "tipo": ("Tipo de cuenta", ""),
            "cuenta_padre": ("Cuenta padre", "Usala para crear subcuentas y reportes agrupados."),
            "descripcion": ("Descripcion", ""),
            "acepta_movimientos": ("Acepta movimientos", "Desmarca si quieres dejarla solo como cuenta agrupadora."),
            "activa": ("Cuenta activa", ""),
        }
        for field_name, (label, help_text) in textos.items():
            self.fields[field_name].label = label
            if help_text:
                self.fields[field_name].help_text = help_text


class ImportarCatalogoCuentasForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo Excel",
        help_text="Formato esperado: codigo, nombre, tipo, codigo_padre, acepta_movimientos, activa, descripcion.",
    )
    actualizar_existentes = forms.BooleanField(
        required=False,
        initial=True,
        label="Actualizar cuentas existentes",
        help_text="Si el codigo ya existe para esta empresa, actualiza nombre, tipo, padre y estado.",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        nombre = archivo.name.lower()
        if not nombre.endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("Sube un archivo Excel valido en formato .xlsx o .xlsm.")
        return archivo


class PeriodoContableForm(forms.ModelForm):
    class Meta:
        model = PeriodoContable
        fields = ["anio", "mes", "estado", "observacion"]
        widgets = {
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
        self.fields["anio"].label = "Anio"
        self.fields["mes"].label = "Mes"
        self.fields["estado"].label = "Estado"
        self.fields["observacion"].label = "Observacion"


class ClasificacionCompraFiscalForm(forms.ModelForm):
    class Meta:
        model = ClasificacionCompraFiscal
        fields = ["nombre", "cuenta_contable", "descripcion", "activa"]
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.filter(
                empresa=empresa,
                activa=True,
                acepta_movimientos=True,
            ).order_by("codigo", "nombre")
        else:
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.none()
        self.fields["nombre"].help_text = "Ejemplo: Combustible, Alquiler, Servicios Publicos, Papeleria, Inventario."
        self.fields["cuenta_contable"].help_text = "Cuenta que recibira el gasto o activo cuando automaticemos la partida contable."


class CuentaFinancieraForm(forms.ModelForm):
    class Meta:
        model = CuentaFinanciera
        fields = ["nombre", "tipo", "institucion", "numero", "cuenta_contable", "activa"]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.filter(
                empresa=empresa,
                activa=True,
                acepta_movimientos=True,
            ).order_by("codigo", "nombre")
        else:
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.none()


class ClasificacionMovimientoBancoForm(forms.ModelForm):
    class Meta:
        model = ClasificacionMovimientoBanco
        fields = ["nombre", "tipo", "cuenta_contable", "activa"]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.filter(
                empresa=empresa,
                activa=True,
                acepta_movimientos=True,
            ).order_by("codigo", "nombre")
        else:
            self.fields["cuenta_contable"].queryset = CuentaContable.objects.none()


class ImportarMovimientosBancoForm(forms.Form):
    cuenta_financiera = forms.ModelChoiceField(queryset=CuentaFinanciera.objects.none(), label="Cuenta o tarjeta")
    archivo = forms.FileField(
        label="Estado de cuenta",
        help_text="Sube un archivo .xlsx, .xlsm o .pdf con texto seleccionable.",
    )

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["cuenta_financiera"].queryset = CuentaFinanciera.objects.filter(empresa=empresa, activa=True).order_by("nombre")

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        nombre = archivo.name.lower()
        if not nombre.endswith((".xlsx", ".xlsm", ".pdf")):
            raise forms.ValidationError("Sube el estado de cuenta en formato Excel .xlsx/.xlsm o PDF .pdf.")
        return archivo


class MovimientoBancarioClasificacionForm(forms.ModelForm):
    class Meta:
        model = MovimientoBancario
        fields = ["clasificacion"]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).order_by("nombre")
        else:
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.none()


class MovimientoBancarioEdicionForm(forms.ModelForm):
    class Meta:
        model = MovimientoBancario
        fields = ["fecha", "descripcion", "referencia", "debito", "credito", "saldo", "clasificacion"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "descripcion": forms.TextInput(),
            "referencia": forms.TextInput(),
            "debito": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "credito": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "saldo": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).order_by("nombre")
        else:
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.none()
        self.fields["clasificacion"].required = False


class ReglaClasificacionBancoForm(forms.ModelForm):
    class Meta:
        model = ReglaClasificacionBanco
        fields = ["nombre", "texto_busqueda", "tipo_movimiento", "clasificacion", "prioridad", "activa"]
        widgets = {
            "texto_busqueda": forms.TextInput(attrs={"placeholder": "Ejemplo: COMISION, ENEE, DEPOSITO"}),
            "prioridad": forms.NumberInput(attrs={"min": "1"}),
        }
        help_texts = {
            "texto_busqueda": "Si la descripcion bancaria contiene este texto, se aplicara la clasificacion.",
            "prioridad": "Las reglas con numero menor se aplican primero.",
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.filter(empresa=empresa, activa=True).order_by("nombre")
        else:
            self.fields["clasificacion"].queryset = ClasificacionMovimientoBanco.objects.none()


class EnlazarMovimientoFacturaForm(forms.Form):
    factura = forms.ModelChoiceField(queryset=Factura.objects.none(), label="Factura pendiente")

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        movimiento = kwargs.pop("movimiento", None)
        super().__init__(*args, **kwargs)
        self.movimiento = movimiento
        if empresa:
            facturas = Factura.objects.filter(
                empresa=empresa,
                estado="emitida",
                estado_pago__in=["pendiente", "parcial"],
            ).select_related("cliente").order_by("-fecha_emision", "-id")
            self.fields["factura"].queryset = facturas
        self.fields["factura"].help_text = "El deposito se aplicara como pago de esta factura."

    def clean_factura(self):
        factura = self.cleaned_data["factura"]
        if self.movimiento and factura.saldo_pendiente < self.movimiento.credito:
            raise forms.ValidationError("La factura seleccionada tiene un saldo menor que el deposito. Usa una factura con saldo suficiente.")
        return factura


class EnlazarMovimientoCompraForm(forms.Form):
    compra = forms.ModelChoiceField(queryset=CompraInventario.objects.none(), label="Compra pendiente")

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        movimiento = kwargs.pop("movimiento", None)
        super().__init__(*args, **kwargs)
        self.movimiento = movimiento
        if empresa:
            self.fields["compra"].queryset = CompraInventario.objects.filter(
                empresa=empresa,
                estado="aplicada",
            ).select_related("proveedor").prefetch_related("pagos_compra").order_by("-fecha_documento", "-id")
        self.fields["compra"].help_text = "El debito bancario se aplicara como pago de esta compra."

    def clean_compra(self):
        compra = self.cleaned_data["compra"]
        if self.movimiento and compra.saldo_pendiente < self.movimiento.debito:
            raise forms.ValidationError("La compra seleccionada tiene un saldo menor que el debito bancario. Usa una compra con saldo suficiente.")
        return compra


class AsientoContableForm(forms.ModelForm):
    contabilizar_ahora = forms.BooleanField(required=False, initial=False, label="Contabilizar ahora")

    class Meta:
        model = AsientoContable
        fields = ["fecha", "descripcion", "referencia", "origen_modulo", "estado"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["estado"].help_text = "Puedes guardarlo en borrador o dejarlo listo para contabilizar."
        self.fields["origen_modulo"].label = "Origen"


class ConfiguracionContableEmpresaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionContableEmpresa
        fields = [
            "cuenta_caja",
            "cuenta_bancos",
            "cuenta_clientes",
            "cuenta_inventario",
            "cuenta_isv_por_pagar",
            "cuenta_proveedores",
            "cuenta_ventas",
            "cuenta_devoluciones_ventas",
        ]

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        cuentas = CuentaContable.objects.none()
        if empresa:
            cuentas = CuentaContable.objects.filter(
                empresa=empresa,
                activa=True,
                acepta_movimientos=True,
            ).order_by("codigo", "nombre")

        textos = {
            "cuenta_caja": ("Caja", "Cuenta usada para cobros y pagos en efectivo."),
            "cuenta_bancos": ("Bancos", "Cuenta usada para transferencias, depositos o pagos bancarios."),
            "cuenta_clientes": ("Cuentas por cobrar clientes", "Cuenta que recibe las facturas emitidas y se reduce con pagos o notas de credito."),
            "cuenta_inventario": ("Inventario", "Cuenta usada cuando se aplican compras de productos inventariables."),
            "cuenta_isv_por_pagar": ("ISV por pagar", "Cuenta de impuesto trasladado por facturas y notas de credito."),
            "cuenta_proveedores": ("Cuentas por pagar proveedores", "Cuenta que recibe compras aplicadas y se reduce con pagos a proveedores."),
            "cuenta_ventas": ("Ventas", "Cuenta de ingresos para las facturas emitidas."),
            "cuenta_devoluciones_ventas": ("Devoluciones sobre ventas", "Cuenta usada para notas de credito emitidas."),
        }
        for field_name, (label, help_text) in textos.items():
            self.fields[field_name].queryset = cuentas
            self.fields[field_name].required = False
            self.fields[field_name].label = label
            self.fields[field_name].help_text = help_text
            self.fields[field_name].empty_label = "Usar cuenta automatica por defecto"


class LineaAsientoContableForm(forms.ModelForm):
    class Meta:
        model = LineaAsientoContable
        fields = ["cuenta", "detalle", "debe", "haber"]


LineaAsientoFormSet = inlineformset_factory(
    AsientoContable,
    LineaAsientoContable,
    form=LineaAsientoContableForm,
    extra=2,
    can_delete=True,
)
