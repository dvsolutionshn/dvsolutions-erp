from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from facturacion.models import CompraInventario, Factura

from .models import AsientoContable, ClasificacionCompraFiscal, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, PeriodoContable, ReglaClasificacionBanco


class CuentaContableForm(forms.ModelForm):
    class Meta:
        model = CuentaContable
        fields = ["codigo", "nombre", "tipo", "cuenta_padre", "actividad_flujo", "descripcion", "acepta_movimientos", "activa"]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
        cuentas_padre = CuentaContable.objects.none()
        if empresa:
            from .alcance_catalogo import es_empresa_rumi
            if es_empresa_rumi(empresa):
                from django.db.models import Q
                cuentas_padre = CuentaContable.objects.filter(empresa=empresa).filter(
                    Q(activa=True) | Q(tipo="orden") | Q(pk=self.instance.cuenta_padre_id),
                ).order_by("codigo", "nombre")
            else:
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
            "actividad_flujo": ("Actividad en el flujo de efectivo", "Puede indicar operación, inversión o financiamiento para cuentas propias. La opción automática usa la clasificación de la cuenta o de su cuenta padre."),
            "descripcion": ("Descripcion", ""),
            "acepta_movimientos": ("Acepta movimientos", "Desmarca si quieres dejarla solo como cuenta agrupadora."),
            "activa": ("Cuenta activa", ""),
        }
        for field_name, (label, help_text) in textos.items():
            self.fields[field_name].label = label
            if help_text:
                self.fields[field_name].help_text = help_text
        from .alcance_catalogo import es_empresa_rumi
        if es_empresa_rumi(empresa):
            self.fields["tipo"].help_text = "Los controles extracontables se conservan para consulta y no admiten asientos financieros."
        else:
            self.fields["tipo"].choices = [(clave, etiqueta) for clave, etiqueta in self.fields["tipo"].choices if clave != "orden"]
            if self.instance.pk and self.instance.tipo == "orden":
                # Conserva una eventual cuenta importada antes de restringir el alcance.
                self.fields["tipo"].choices += [("orden", "Control extracontable existente")]
                self.fields["tipo"].disabled = True
                self.fields["cuenta_padre"].disabled = True
                self.fields["cuenta_padre"].queryset = CuentaContable.objects.filter(
                    empresa=empresa, pk=self.instance.cuenta_padre_id,
                )

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        empresa = getattr(self.instance, "empresa", None)
        if empresa and codigo:
            existe = CuentaContable.objects.filter(empresa=empresa, codigo__iexact=codigo).exclude(pk=self.instance.pk)
            if existe.exists():
                raise forms.ValidationError("Ya existe una cuenta contable con este codigo en esta empresa.")
        return codigo


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


class ImportarCatalogoCuentasRumiForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo Excel",
        help_text="Acepta .xlsx o .xlsm: formato estándar, catálogos jerárquicos y propuestas como RUMI, incluso con títulos antes del encabezado.",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xlsm"}),
    )
    actualizar_existentes = forms.BooleanField(
        required=False,
        initial=False,
        label="Actualizar cuentas existentes",
        help_text="Déjalo desmarcado para conservar las cuentas actuales. Si lo activas, se revisarán los cambios de nombre, tipo, padre y estado antes de confirmar.",
    )
    incluir_controles = forms.BooleanField(
        required=False,
        initial=True,
        label="Conservar controles extracontables para consulta",
        help_text="Conserva las cuentas de orden como referencia; no admiten asientos ni afectan los estados financieros.",
    )
    hoja = forms.CharField(
        required=False,
        max_length=150,
        label="Hoja del catálogo (opcional)",
        help_text="Se detecta automáticamente. Si el archivo contiene varios catálogos, escribe el nombre exacto de la hoja que deseas importar.",
    )
    prefijo_codigos = forms.RegexField(
        required=False,
        max_length=20,
        regex=r"^[A-Za-z0-9_.-]*$",
        label="Prefijo para cuentas nuevas (opcional)",
        help_text="Ejemplo: RUMI-. Permite agregar este catálogo sin ocupar los códigos del catálogo actual. También se aplica a sus cuentas padre.",
        error_messages={"invalid": "Usa únicamente letras, números, punto, guion o guion bajo en el prefijo."},
    )

    def __init__(self, *args, requiere_archivo=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["archivo"].required = requiere_archivo

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo is None:
            return archivo
        if not archivo.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("Sube un archivo Excel válido en formato .xlsx o .xlsm.")
        return archivo


class PeriodoContableForm(forms.ModelForm):
    class Meta:
        model = PeriodoContable
        fields = ["anio", "mes", "observacion"]
        widgets = {
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
        self._estado_original = self.instance.estado
        self._periodo_original = (self.instance.anio, self.instance.mes)
        self.fields["anio"].label = "Año"
        self.fields["mes"].label = "Mes"
        self.fields["observacion"].label = "Observación"
        if self.instance.pk:
            for nombre in ("anio", "mes"):
                self.fields[nombre].disabled = True
                self.fields[nombre].help_text = "El año y el mes de un período existente no se pueden cambiar."

    def clean(self):
        cleaned_data = super().clean()
        estado_enviado = self.data.get(self.add_prefix("estado"))
        if estado_enviado is not None and estado_enviado != self._estado_original:
            raise forms.ValidationError(
                "El estado no se modifica desde este formulario. Usa la revisión del cierre o la reapertura con motivo."
            )
        if self.instance.pk:
            for nombre, original in zip(("anio", "mes"), self._periodo_original):
                enviado = self.data.get(self.add_prefix(nombre))
                if enviado is not None:
                    try:
                        coincide = int(enviado) == original
                    except (TypeError, ValueError):
                        coincide = False
                    if not coincide:
                        self.add_error(nombre, "El año y el mes de un período existente no se pueden cambiar.")
        return cleaned_data


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
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
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
    tipo_cambio_cobro = forms.DecimalField(
        max_digits=14, decimal_places=6, required=False, min_value=Decimal('0.000001'),
        label="Tipo de cambio del depósito (HNL por USD)",
        help_text="Para facturas en dólares indique la tasa efectiva. Si lo deja vacío se usa la tasa de la factura.",
    )
    importe_moneda_factura = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal('0.01'),
        label="Importe en la moneda de la factura",
        help_text="Opcional. La conversión debe coincidir exactamente con el depósito en lempiras.",
    )

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

    def datos_aplicacion(self, factura, movimiento=None):
        from .moneda import convertir_a_lempiras, redondear_importe, validar_moneda_tipo_cambio
        movimiento = movimiento or self.movimiento
        if movimiento.empresa_id != factura.empresa_id:
            raise forms.ValidationError("El depósito y la factura deben pertenecer a la misma empresa.")
        _moneda, tasa = validar_moneda_tipo_cambio(
            factura.moneda, self.cleaned_data.get("tipo_cambio_cobro") or factura.tipo_cambio,
        )
        importe = self.cleaned_data.get("importe_moneda_factura")
        if importe is None:
            importe = redondear_importe(movimiento.credito / tasa)
        if convertir_a_lempiras(importe, factura.moneda, tasa) != movimiento.credito:
            raise forms.ValidationError(
                "La conversión no coincide con el depósito en lempiras. Indique el importe original y la tasa efectiva del cobro."
            )
        if importe <= 0 or importe > factura.saldo_pendiente:
            raise forms.ValidationError("La factura seleccionada tiene un saldo menor que el depósito. Use una factura con saldo suficiente.")
        return importe, tasa

    def clean(self):
        datos = super().clean()
        if datos.get("factura") and self.movimiento and not self.errors:
            self.datos_aplicacion(datos["factura"])
        return datos


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
        usuario = kwargs.pop("usuario", None)
        empresa = kwargs.pop("empresa", None)
        puede_contabilizar = kwargs.pop("puede_contabilizar", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.instance.empresa = empresa
        if puede_contabilizar is None:
            empresa = empresa or getattr(self.instance, "empresa", None)
            puede_contabilizar = bool(
                usuario
                and usuario.tiene_permiso_erp("puede_contabilizar_asientos", empresa)
            )
        self.puede_contabilizar = bool(puede_contabilizar)
        self.fields["fecha"].widget = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
        if (
            not self.is_bound and not self.instance.pk
            and not self.instance.fecha and not self.initial.get("fecha")
        ):
            self.initial["fecha"] = timezone.localdate()
        self.fields["fecha"].label = "Fecha"
        self.fields["descripcion"].label = "Descripción"
        self.fields["descripcion"].widget = forms.Textarea(attrs={
            "rows": 2,
            "maxlength": 255,
            "placeholder": "Describe la operación que estás registrando",
        })
        self.fields["referencia"].label = "Referencia"
        self.fields["referencia"].widget.attrs.update({
            "placeholder": "Ej.: recibo, transferencia o documento",
            "autocomplete": "off",
        })
        self.fields["estado"].choices = [
            opcion for opcion in AsientoContable.ESTADO_CHOICES
            if opcion[0] == "borrador" or (self.puede_contabilizar and opcion[0] == "contabilizado")
        ]
        self.fields["estado"].required = False
        self.fields["estado"].widget = forms.HiddenInput()
        self.fields["estado"].initial = "borrador"
        self.fields["contabilizar_ahora"].disabled = not self.puede_contabilizar
        self.fields["contabilizar_ahora"].widget = forms.HiddenInput()
        self.fields["origen_modulo"].label = "Origen"
        self.fields["origen_modulo"].widget.attrs.update({
            "placeholder": "Ej.: manual",
            "autocomplete": "off",
        })

    def clean(self):
        cleaned_data = super().clean()
        estado_enviado = self.data.get(self.add_prefix("estado"))
        contabilizar_enviado = forms.BooleanField(required=False).to_python(
            self.data.get(self.add_prefix("contabilizar_ahora"))
        )
        if not self.puede_contabilizar and (estado_enviado == "contabilizado" or contabilizar_enviado):
            raise forms.ValidationError("Tu rol no tiene permiso para contabilizar asientos.")
        cleaned_data["estado"] = cleaned_data.get("estado") or "borrador"
        return cleaned_data


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
            "isv_compras_recuperable",
            "cuenta_isv_acreditable",
            "cuenta_entradas_inventario",
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
            "cuenta_isv_acreditable": ("ISV acreditable de compras", "Crédito fiscal utilizado únicamente si el ISV de compras es recuperable para esta empresa."),
            "cuenta_entradas_inventario": ("Contrapartida de entradas manuales de inventario", "Seleccione la cuenta según el origen: saldo inicial, aporte o deuda documentada. Para compras reales utilice el módulo de Compras. Es obligatoria al aplicar entradas manuales con costo."),
        }
        for field_name, (label, help_text) in textos.items():
            self.fields[field_name].queryset = cuentas
            tipo = {
                'cuenta_caja': 'activo', 'cuenta_bancos': 'activo', 'cuenta_clientes': 'activo',
                'cuenta_inventario': 'activo', 'cuenta_isv_acreditable': 'activo',
                'cuenta_proveedores': 'pasivo', 'cuenta_isv_por_pagar': 'pasivo',
                'cuenta_ventas': 'ingreso', 'cuenta_devoluciones_ventas': 'ingreso',
            }.get(field_name)
            if tipo:
                self.fields[field_name].queryset = cuentas.filter(tipo=tipo)
            self.fields[field_name].required = False
            self.fields[field_name].label = label
            self.fields[field_name].help_text = help_text
            self.fields[field_name].empty_label = "Usar cuenta automatica por defecto"
        self.fields['cuenta_entradas_inventario'].empty_label = 'Sin configurar; sólo entradas sin costo'
        self.fields["isv_compras_recuperable"].label = "Esta empresa recupera el ISV de compras"
        self.fields["isv_compras_recuperable"].help_text = (
            "Al activarlo, el ISV se separa como crédito fiscal. Si se deja desactivado, "
            "forma parte del costo. Se aplica a nuevas compras; conserva la valoración de compras ya aplicadas."
        )


class LineaAsientoContableForm(forms.ModelForm):
    class Meta:
        model = LineaAsientoContable
        fields = ["cuenta", "detalle", "debe", "haber"]

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is None and self.instance.asiento_id:
            empresa = self.instance.asiento.empresa
        self.fields["cuenta"].queryset = (
            CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimientos=True)
            .order_by("codigo", "nombre")
            if empresa else CuentaContable.objects.none()
        )
        self.fields["cuenta"].label = "Cuenta contable"
        self.fields["cuenta"].empty_label = "Selecciona una cuenta"
        self.fields["detalle"].label = "Detalle"
        self.fields["detalle"].widget.attrs.update({
            "placeholder": "Detalle de esta partida (opcional)",
            "autocomplete": "off",
        })
        for nombre in ("debe", "haber"):
            self.fields[nombre].label = nombre.capitalize()
            self.fields[nombre].widget.attrs.update({
                "inputmode": "decimal",
                "placeholder": "0.00",
                "autocomplete": "off",
            })


class LineasAsientoEmpresaFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        # También se ejecuta al construir empty_form; la plantilla y las
        # partidas agregadas conservan el mismo catálogo de la empresa.
        kwargs["empresa"] = self.instance.empresa if self.instance.empresa_id else None
        return kwargs


LineaAsientoFormSet = inlineformset_factory(
    AsientoContable,
    LineaAsientoContable,
    form=LineaAsientoContableForm,
    formset=LineasAsientoEmpresaFormSet,
    extra=2,
    can_delete=True,
)
