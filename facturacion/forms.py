from django import forms
from core.models import ConfiguracionAvanzadaEmpresa, ConfiguracionPowerBIEmpresa
from .models import CAI, CategoriaProductoFarmaceutico, Cliente, ConfiguracionFacturacionEmpresa, PagoCompra, PagoFactura, PerfilFarmaceuticoProducto, Producto, Proveedor, RegistroCompraFiscal, TipoImpuesto

DATE_INPUT_FORMATS_LATAM = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]


def configurar_campo_fecha(field, placeholder="dd/mm/aaaa"):
    field.input_formats = DATE_INPUT_FORMATS_LATAM
    field.widget = forms.DateInput(
        format="%d/%m/%Y",
        attrs={
            "type": "text",
            "class": "js-date-picker",
            "placeholder": placeholder,
            "autocomplete": "off",
        },
    )
    return field


class PagoFacturaForm(forms.ModelForm):
    class Meta:
        model = PagoFactura
        fields = ['monto', 'metodo', 'referencia']


class ConfiguracionFacturacionEmpresaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        permite_plantilla_notas_extensas = kwargs.pop("permite_plantilla_notas_extensas", False)
        permite_plantilla_independiente = kwargs.pop("permite_plantilla_independiente", False)
        super().__init__(*args, **kwargs)
        if not permite_plantilla_notas_extensas:
            self.fields["plantilla_factura_pdf"].choices = [
                choice
                for choice in self.fields["plantilla_factura_pdf"].choices
                if choice[0] != "notas_extensas"
            ]
        if not permite_plantilla_independiente:
            self.fields["plantilla_factura_pdf"].choices = [
                choice
                for choice in self.fields["plantilla_factura_pdf"].choices
                if choice[0] != "independiente"
            ]

    class Meta:
        model = ConfiguracionFacturacionEmpresa
        fields = [
            'plantilla_factura_pdf',
            'nombre_comercial_documentos',
            'color_primario',
            'color_secundario',
            'logo_ancho_pdf',
            'logo_alto_pdf',
            'mostrar_vendedor',
            'mostrar_descuentos',
            'mostrar_notas_linea',
            'leyenda_factura',
            'pie_factura',
        ]
        widgets = {
            'color_primario': forms.TextInput(attrs={'type': 'color'}),
            'color_secundario': forms.TextInput(attrs={'type': 'color'}),
            'logo_ancho_pdf': forms.NumberInput(attrs={'min': 40, 'max': 260, 'step': 5}),
            'logo_alto_pdf': forms.NumberInput(attrs={'min': 30, 'max': 160, 'step': 5}),
            'pie_factura': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'plantilla_factura_pdf': 'Define que formato usara el boton PDF principal de esta empresa.',
            'nombre_comercial_documentos': 'Si lo indicas, se mostrara en documentos en lugar del nombre legal.',
            'color_primario': 'Color principal para barras, titulos y detalles del documento.',
            'color_secundario': 'Color secundario para degradados o acentos visuales.',
            'logo_ancho_pdf': 'Ajusta el ancho visual del logo en el PDF para acomodarlo al estilo de la empresa.',
            'logo_alto_pdf': 'Ajusta la altura maxima del logo para equilibrarlo mejor dentro del encabezado.',
            'leyenda_factura': 'Texto visible al pie de la factura, por ejemplo una frase comercial.',
            'pie_factura': 'Texto fiscal o comercial adicional para el pie del PDF.',
        }


class ConfiguracionPowerBIForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionPowerBIEmpresa
        fields = [
            "activo",
            "mostrar_en_reportes",
            "titulo_panel",
            "descripcion_panel",
            "url_embed",
            "alto_iframe",
            "usa_token_seguro",
            "workspace_id",
            "report_id",
        ]
        widgets = {
            "descripcion_panel": forms.Textarea(attrs={"rows": 3}),
            "alto_iframe": forms.NumberInput(attrs={"min": 420, "max": 1800, "step": 20}),
        }
        help_texts = {
            "activo": "Activa el panel embebido de Power BI para esta empresa.",
            "mostrar_en_reportes": "Si esta activo, el dashboard se muestra dentro del modulo de reportes.",
            "titulo_panel": "Titulo visible para el bloque BI dentro del ERP.",
            "descripcion_panel": "Texto breve para explicar al usuario que esta viendo en el dashboard.",
            "url_embed": "Pega aqui la URL embed compartida desde Power BI.",
            "alto_iframe": "Altura del panel embebido en pixeles.",
            "usa_token_seguro": "Reserva esta opcion para una futura integracion con token seguro.",
            "workspace_id": "Dato opcional para una integracion avanzada con Power BI.",
            "report_id": "Dato opcional para una integracion avanzada con Power BI.",
        }


class PagoCompraForm(forms.ModelForm):
    class Meta:
        model = PagoCompra
        fields = ['fecha', 'monto', 'metodo', 'cuenta_financiera', 'referencia', 'observacion']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}),
            'monto': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'referencia': forms.TextInput(),
            'observacion': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'fecha': 'Fecha efectiva del pago registrado al proveedor.',
            'monto': 'No puede superar el saldo pendiente de la compra.',
            'metodo': 'Metodo de salida de fondos utilizado para cancelar la compra.',
            'cuenta_financiera': 'Banco, caja o tarjeta desde donde salio el pago.',
            'referencia': 'Numero de transferencia, cheque, lote bancario o referencia interna.',
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop('empresa', None)
        super().__init__(*args, **kwargs)
        self.fields['observacion'].required = False
        if empresa:
            from contabilidad.models import CuentaFinanciera

            self.fields['cuenta_financiera'].queryset = CuentaFinanciera.objects.filter(
                empresa=empresa,
                activa=True,
            ).select_related('cuenta_contable').order_by('nombre')
        else:
            self.fields['cuenta_financiera'].queryset = self.fields['cuenta_financiera'].queryset.none()
        self.fields['cuenta_financiera'].required = True
        self.fields['cuenta_financiera'].empty_label = 'Seleccione banco, caja o tarjeta'


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            'nombre',
            'rtn',
            'correo',
            'telefono',
            'telefono_whatsapp',
            'fecha_nacimiento',
            'acepta_promociones',
            'canal_preferido',
            'direccion',
            'ciudad',
            'activo',
        ]
        widgets = {
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['canal_preferido'].required = False

    def clean_canal_preferido(self):
        return self.cleaned_data.get('canal_preferido') or 'whatsapp'


class ProductoForm(forms.ModelForm):
    categoria_farmaceutica = forms.ModelChoiceField(
        queryset=CategoriaProductoFarmaceutico.objects.none(),
        required=False,
        label='Categoria farmaceutica',
    )
    principio_activo = forms.CharField(required=False, label='Principio activo')
    presentacion = forms.CharField(required=False, label='Presentacion')
    concentracion = forms.CharField(required=False, label='Concentracion')
    laboratorio = forms.CharField(required=False, label='Laboratorio / marca')
    registro_sanitario = forms.CharField(required=False, label='Registro sanitario')
    requiere_receta = forms.BooleanField(required=False, label='Requiere receta')
    requiere_refrigeracion = forms.BooleanField(required=False, label='Requiere refrigeracion')
    producto_controlado = forms.BooleanField(required=False, label='Producto controlado')
    alerta_vencimiento_dias = forms.IntegerField(required=False, min_value=1, label='Alerta vencimiento dias')

    class Meta:
        model = Producto
        fields = [
            'nombre',
            'codigo',
            'tipo_item',
            'unidad_medida',
            'precio',
            'fecha_referencia',
            'fecha_alerta',
            'nota_fecha',
            'impuesto_predeterminado',
            'controla_inventario',
            'categoria_farmaceutica',
            'principio_activo',
            'presentacion',
            'concentracion',
            'laboratorio',
            'registro_sanitario',
            'requiere_receta',
            'requiere_refrigeracion',
            'producto_controlado',
            'alerta_vencimiento_dias',
            'descripcion',
            'activo',
        ]
        help_texts = {
            'codigo': 'Codigo interno opcional para busquedas, inventario y futuras integraciones.',
            'tipo_item': 'Define si se trata de un articulo fisico o un servicio.',
            'unidad_medida': 'Unidad comercial principal para facturacion e inventario.',
            'precio': 'Precio base sugerido al seleccionar el producto en factura.',
            'impuesto_predeterminado': 'Se aplicara automaticamente al cargar el producto en nuevas lineas.',
            'controla_inventario': 'Activalo solo si este item debe mover existencias cuando construyamos inventario.',
            'descripcion': 'Descripcion comercial o tecnica del item.',
            'fecha_referencia': 'Fecha especial del producto o servicio: vencimiento, garantia, revision o seguimiento.',
            'fecha_alerta': 'Fecha en que el CRM debe alertar sobre este producto.',
            'nota_fecha': 'Detalle de la fecha: vencimiento, garantia, control medico, revision, etc.',
        }
        widgets = {
            'fecha_referencia': forms.DateInput(attrs={'type': 'date'}),
            'fecha_alerta': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        self.empresa = kwargs.pop('empresa', None)
        super().__init__(*args, **kwargs)
        self.mostrar_perfil_farmaceutico = False
        self.fields['impuesto_predeterminado'].queryset = TipoImpuesto.objects.filter(activo=True).order_by('porcentaje', 'nombre')
        if self.empresa:
            configuracion_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
            self.mostrar_perfil_farmaceutico = bool(configuracion_avanzada.usa_inventario_farmaceutico)
        if self.empresa:
            self.fields['categoria_farmaceutica'].queryset = CategoriaProductoFarmaceutico.objects.filter(
                empresa=self.empresa,
                activa=True,
            ).order_by('nombre')
        if not self.mostrar_perfil_farmaceutico:
            for field_name in [
                'categoria_farmaceutica',
                'principio_activo',
                'presentacion',
                'concentracion',
                'laboratorio',
                'registro_sanitario',
                'requiere_receta',
                'requiere_refrigeracion',
                'producto_controlado',
                'alerta_vencimiento_dias',
            ]:
                self.fields.pop(field_name, None)
        if self.instance and self.instance.pk:
            perfil = getattr(self.instance, 'perfil_farmaceutico', None)
            if perfil and self.mostrar_perfil_farmaceutico:
                self.fields['categoria_farmaceutica'].initial = perfil.categoria
                self.fields['principio_activo'].initial = perfil.principio_activo
                self.fields['presentacion'].initial = perfil.presentacion
                self.fields['concentracion'].initial = perfil.concentracion
                self.fields['laboratorio'].initial = perfil.laboratorio
                self.fields['registro_sanitario'].initial = perfil.registro_sanitario
                self.fields['requiere_receta'].initial = perfil.requiere_receta
                self.fields['requiere_refrigeracion'].initial = perfil.requiere_refrigeracion
                self.fields['producto_controlado'].initial = perfil.producto_controlado
                self.fields['alerta_vencimiento_dias'].initial = perfil.alerta_vencimiento_dias

    def save(self, commit=True):
        producto = super().save(commit=commit)
        if commit and self.empresa and self.mostrar_perfil_farmaceutico:
            self.guardar_perfil_farmaceutico(producto)
        return producto

    def guardar_perfil_farmaceutico(self, producto):
        categoria = self.cleaned_data.get('categoria_farmaceutica')
        return PerfilFarmaceuticoProducto.objects.update_or_create(
            empresa=self.empresa,
            producto=producto,
            defaults={
                'categoria': categoria,
                'principio_activo': self.cleaned_data.get('principio_activo') or '',
                'presentacion': self.cleaned_data.get('presentacion') or '',
                'concentracion': self.cleaned_data.get('concentracion') or '',
                'laboratorio': self.cleaned_data.get('laboratorio') or '',
                'registro_sanitario': self.cleaned_data.get('registro_sanitario') or '',
                'requiere_receta': self.cleaned_data.get('requiere_receta') or False,
                'requiere_refrigeracion': self.cleaned_data.get('requiere_refrigeracion') or False,
                'producto_controlado': self.cleaned_data.get('producto_controlado') or False,
                'alerta_vencimiento_dias': self.cleaned_data.get('alerta_vencimiento_dias') or 60,
            },
        )


class CategoriaProductoFarmaceuticoForm(forms.ModelForm):
    class Meta:
        model = CategoriaProductoFarmaceutico
        fields = [
            'nombre',
            'descripcion',
            'requiere_receta_default',
            'requiere_refrigeracion_default',
            'producto_controlado_default',
            'activa',
        ]
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
        }


class CAIForm(forms.ModelForm):
    class Meta:
        model = CAI
        fields = [
            'numero_cai',
            'uso_documento',
            'establecimiento',
            'punto_emision',
            'tipo_documento',
            'rango_inicial',
            'rango_final',
            'correlativo_actual',
            'fecha_activacion',
            'fecha_limite',
            'activo',
        ]
        help_texts = {
            'uso_documento': 'Define si este CAI sera usado para facturas o exclusivamente para notas de credito.',
            'tipo_documento': 'Codigo fiscal del documento autorizado para este rango.',
            'fecha_activacion': 'Desde esta fecha el sistema puede usar este CAI en facturas o notas de credito.',
            'fecha_limite': 'Hasta esta fecha el sistema puede usar este CAI para generar documentos.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configurar_campo_fecha(self.fields['fecha_activacion'])
        configurar_campo_fecha(self.fields['fecha_limite'])


class TipoImpuestoForm(forms.ModelForm):
    class Meta:
        model = TipoImpuesto
        fields = ['nombre', 'porcentaje', 'activo']


class ProveedorForm(forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ['nombre', 'rtn', 'contacto', 'telefono', 'correo', 'direccion', 'ciudad', 'condicion_pago', 'dias_credito', 'activo']
        help_texts = {
            'condicion_pago': 'Define si normalmente este proveedor se cancela al contado o con dias de credito.',
            'dias_credito': 'Dias sugeridos para calcular el vencimiento de nuevas compras a credito.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dias_credito'].required = False
        self.fields['dias_credito'].initial = self.instance.dias_credito if self.instance.pk else 0


class ImportarLibroComprasForm(forms.Form):
    archivo = forms.FileField(
        help_text='Sube el archivo Excel del libro de compras. Se leera la hoja COMPRAS si existe.',
    )
    periodo_anio = forms.IntegerField(min_value=2000, max_value=2100)
    periodo_mes = forms.IntegerField(min_value=1, max_value=12)


class RegistroCompraFiscalForm(forms.ModelForm):
    class Meta:
        model = RegistroCompraFiscal
        fields = [
            'proveedor',
            'proveedor_nombre',
            'proveedor_rtn',
            'numero_factura',
            'cai',
            'fecha_documento',
            'periodo_anio',
            'periodo_mes',
            'subtotal',
            'base_15',
            'isv_15',
            'base_18',
            'isv_18',
            'exento',
            'exonerado',
            'total',
            'observacion',
        ]
        widgets = {
            'fecha_documento': forms.DateInput(attrs={'type': 'date'}),
            'observacion': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop('empresa', None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['proveedor'].queryset = Proveedor.objects.filter(empresa=empresa, activo=True).order_by('nombre')
        self.fields['proveedor'].required = False
        self.fields['proveedor_nombre'].help_text = 'Nombre fiscal del proveedor como aparece en la factura.'
        self.fields['numero_factura'].help_text = 'El sistema valida este numero contra meses anteriores para evitar duplicados.'
        self.fields['exento'].label = 'Subtotal exento'
        self.fields['base_15'].label = 'Subtotal 15%'
        self.fields['base_18'].label = 'Subtotal 18%'
        self.fields['isv_15'].label = 'ISV 15%'
        self.fields['isv_18'].label = 'ISV 18%'
        self.fields['total'].label = 'Total factura'
        for field_name in ['subtotal', 'base_15', 'base_18', 'isv_15', 'isv_18', 'exento', 'exonerado', 'total']:
            self.fields[field_name].widget.attrs.update({'step': '0.01', 'min': '0', 'class': f'js-compra-{field_name}'})
            self.fields[field_name].required = False
        for field_name in ['subtotal', 'isv_15', 'isv_18', 'total']:
            self.fields[field_name].widget.attrs['readonly'] = 'readonly'


class AjusteInventarioForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.none())
    tipo_ajuste = forms.ChoiceField(
        choices=(
            ('ajuste_entrada', 'Ajuste Positivo'),
            ('ajuste_salida', 'Ajuste Negativo'),
        )
    )
    cantidad = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    observacion = forms.CharField(widget=forms.Textarea, required=False)
    stock_minimo = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop('empresa', None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True,
                controla_inventario=True
            ).order_by('nombre')
        self.fields['producto'].help_text = 'Solo se muestran productos fisicos con control de inventario activo.'
        self.fields['tipo_ajuste'].help_text = 'Usa ajuste positivo para sumar existencias y negativo para rebajarlas.'
        self.fields['stock_minimo'].help_text = 'Opcional. Si lo indicas, tambien se actualizara el minimo esperado del producto.'


class EntradaInventarioForm(forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.objects.none())
    cantidad = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    referencia = forms.CharField(max_length=120)
    observacion = forms.CharField(widget=forms.Textarea, required=False)
    stock_minimo = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop('empresa', None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['producto'].queryset = Producto.objects.filter(
                empresa=empresa,
                activo=True,
                controla_inventario=True
            ).order_by('nombre')
        self.fields['producto'].help_text = 'Selecciona el producto fisico al que deseas cargar existencias.'
        self.fields['cantidad'].help_text = 'Cantidad de unidades que ingresan al inventario.'
        self.fields['referencia'].help_text = 'Usa una referencia clara: compra, carga inicial, lote, traslado, etc.'
        self.fields['stock_minimo'].help_text = 'Opcional. Si lo indicas, actualiza tambien el stock minimo esperado.'
