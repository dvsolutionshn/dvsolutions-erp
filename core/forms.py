from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group

from .models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, PagoLicenciaEmpresa, PlanComercial, PlanModulo, RolSistema, Usuario


class SuperAdminLoginForm(forms.Form):
    username = forms.CharField(label="Usuario")
    password = forms.CharField(label="Contrasena", widget=forms.PasswordInput)

    error_messages = {
        "invalid_login": "Credenciales invalidas.",
        "not_superuser": "Este acceso es exclusivo para superadministradores.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")

        if username and password:
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise forms.ValidationError(self.error_messages["invalid_login"])
            if not self.user_cache.is_superuser:
                raise forms.ValidationError(self.error_messages["not_superuser"])

        return cleaned_data

    def get_user(self):
        return self.user_cache


class EmpresaControlForm(forms.ModelForm):
    modulos_activos = forms.ModelMultipleChoiceField(
        queryset=Modulo.objects.filter(es_comercial=True).order_by("nombre"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Modulos habilitados",
        help_text="Selecciona los modulos que esta empresa puede usar dentro del ERP.",
    )
    permite_cai_historico = forms.BooleanField(
        required=False,
        label="Permitir correccion fiscal historica",
        help_text="Activalo solo cuando necesites editar CAI usados, numeracion historica o correcciones fiscales especiales en esta empresa.",
    )
    permite_plantilla_factura_independiente = forms.BooleanField(
        required=False,
        label="Permitir plantilla de factura independiente",
        help_text="Activalo para habilitar un formato PDF exclusivo, mas sobrio y visualmente separado del resto del ERP.",
    )

    class Meta:
        model = Empresa
        fields = [
            "nombre",
            "slug",
            "rtn",
            "plan_comercial",
            "estado_licencia",
            "fecha_inicio_plan",
            "fecha_vencimiento_plan",
            "correo",
            "telefono",
            "sitio_web",
            "direccion",
            "ciudad",
            "departamento",
            "pais",
            "slogan",
            "condiciones_pago",
            "observaciones_comerciales",
            "logo",
            "activa",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "estado_licencia" in self.fields:
            self.fields["estado_licencia"].initial = self.instance.estado_licencia or "prueba"
            self.fields["estado_licencia"].required = False
        if self.instance.pk:
            self.fields["modulos_activos"].initial = Modulo.objects.filter(
                empresamodulo__empresa=self.instance,
                empresamodulo__activo=True,
                es_comercial=True,
            )
        configuracion_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.instance) if self.instance.pk else None
        self.fields["permite_cai_historico"].initial = bool(
            configuracion_avanzada and configuracion_avanzada.permite_cai_historico
        )
        self.fields["permite_plantilla_factura_independiente"].initial = bool(
            configuracion_avanzada and configuracion_avanzada.permite_plantilla_factura_independiente
        )
        self.fields["modulos_activos"].help_text = "Sirve como activacion adicional o ajuste especial por empresa, ademas del plan comercial."
        textos = {
            "nombre": ("Nombre de la empresa", ""),
            "slug": ("Slug / enlace privado", "Este slug define la ruta privada de acceso de la empresa dentro del ERP."),
            "rtn": ("RTN", ""),
            "plan_comercial": ("Plan comercial", "Selecciona el plan que esta empresa tiene contratado hoy."),
            "estado_licencia": ("Estado de licencia", "Controla si la empresa esta en prueba, activa, suspendida o vencida."),
            "fecha_inicio_plan": ("Inicio del servicio", "Fecha desde la cual corre el plan o la prueba comercial."),
            "fecha_vencimiento_plan": ("Vencimiento del servicio", "Si esta fecha ya paso, la empresa quedara como vencida en la operacion."),
            "correo": ("Correo", ""),
            "telefono": ("Telefono", ""),
            "sitio_web": ("Sitio web", ""),
            "direccion": ("Direccion", ""),
            "ciudad": ("Ciudad", ""),
            "departamento": ("Departamento", ""),
            "pais": ("Pais", ""),
            "slogan": ("Slogan", ""),
            "condiciones_pago": ("Condiciones de pago", ""),
            "observaciones_comerciales": ("Observaciones comerciales", "Aqui puedes dejar notas internas de contrato, seguimiento o cobro."),
            "logo": ("Logo", ""),
            "activa": ("Empresa activa", "Si la desactivas, toda la empresa queda fuera de operacion aunque tenga plan."),
            "permite_cai_historico": ("Permitir correccion fiscal historica", "Activalo solo cuando necesites ajustes especiales de CAI, facturacion historica o correcciones fiscales ya emitidas."),
            "permite_plantilla_factura_independiente": ("Permitir plantilla de factura independiente", "Activalo solo cuando quieras habilitar un PDF de factura exclusivo para esta empresa, con una presentacion separada del estilo general del ERP."),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text
        for field_name in ["fecha_inicio_plan", "fecha_vencimiento_plan"]:
            if field_name in self.fields:
                self.fields[field_name].widget = forms.DateInput(attrs={"type": "date"})

    def clean(self):
        cleaned_data = super().clean()
        inicio = cleaned_data.get("fecha_inicio_plan")
        vencimiento = cleaned_data.get("fecha_vencimiento_plan")
        if inicio and vencimiento and vencimiento < inicio:
            self.add_error("fecha_vencimiento_plan", "La fecha de vencimiento no puede ser menor que la fecha de inicio.")
        return cleaned_data

    def save(self, commit=True):
        empresa = super().save(commit=commit)
        if commit:
            self._save_modules(empresa)
            self._save_advanced_config(empresa)
        return empresa

    def save_m2m(self):
        super().save_m2m()
        if self.instance.pk:
            self._save_modules(self.instance)
            self._save_advanced_config(self.instance)

    def _save_modules(self, empresa):
        seleccionados = set(self.cleaned_data.get("modulos_activos", []))
        if empresa.plan_comercial_id:
            seleccionados.update(
                Modulo.objects.filter(
                    planmodulo__plan=empresa.plan_comercial,
                    planmodulo__activo=True,
                    es_comercial=True,
                )
            )
        actuales = {
            relacion.modulo_id: relacion
            for relacion in EmpresaModulo.objects.filter(empresa=empresa).select_related("modulo")
        }

        for modulo in Modulo.objects.filter(es_comercial=True):
            relacion = actuales.get(modulo.id)
            activo = modulo in seleccionados
            if relacion:
                if relacion.activo != activo:
                    relacion.activo = activo
                    relacion.save(update_fields=["activo"])
            elif activo:
                EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)

    def _save_advanced_config(self, empresa):
        configuracion, _ = ConfiguracionAvanzadaEmpresa.objects.get_or_create(empresa=empresa)
        valor = bool(self.cleaned_data.get("permite_cai_historico"))
        valor_plantilla_independiente = bool(self.cleaned_data.get("permite_plantilla_factura_independiente"))
        campos_actualizar = []
        if configuracion.permite_cai_historico != valor:
            configuracion.permite_cai_historico = valor
            campos_actualizar.append("permite_cai_historico")
        if configuracion.permite_plantilla_factura_independiente != valor_plantilla_independiente:
            configuracion.permite_plantilla_factura_independiente = valor_plantilla_independiente
            campos_actualizar.append("permite_plantilla_factura_independiente")
        if campos_actualizar:
            configuracion.save(update_fields=campos_actualizar)


class UsuarioControlCreateForm(UserCreationForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        label="Roles",
        widget=forms.CheckboxSelectMultiple,
        help_text="Asigna uno o varios roles basados en grupos de Django.",
    )

    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "empresa",
            "rol_sistema",
            "es_administrador_empresa",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        textos = {
            "username": ("Usuario", "Nombre con el que la persona iniciara sesion."),
            "first_name": ("Nombres", ""),
            "last_name": ("Apellidos", ""),
            "email": ("Correo electronico", ""),
            "empresa": ("Empresa", "Empresa a la que pertenecera este usuario."),
            "rol_sistema": ("Rol del sistema", "Perfil funcional que define a que areas del ERP podra entrar."),
            "es_administrador_empresa": ("Es administrador de empresa", "Activalo solo si esta persona puede ver todo dentro de su empresa."),
            "is_active": ("Usuario activo", "Si lo desactivas, el usuario ya no podra entrar al sistema."),
            "is_staff": ("Acceso tecnico al admin Django", "Usalo solo si realmente quieres permitir acceso al admin tecnico."),
            "is_superuser": ("Es superadministrador", "Reserva esta opcion solo para ti o para cuentas maestras internas."),
            "password1": ("Contrasena", "Define una contrasena segura para esta cuenta."),
            "password2": ("Confirmar contrasena", "Repite la contrasena para confirmar."),
            "groups": ("Roles complementarios", "Roles adicionales basados en grupos de Django, utiles para crecer despues."),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text

    def save(self, commit=True):
        usuario = super().save(commit=commit)
        if commit:
            usuario.groups.set(self.cleaned_data.get("groups", []))
        return usuario


class UsuarioControlUpdateForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        label="Roles",
        widget=forms.CheckboxSelectMultiple,
    )
    nueva_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        label="Nueva contrasena",
        help_text="Dejalo vacio si no deseas cambiar la contrasena actual.",
    )
    confirmar_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        label="Confirmar contrasena",
    )

    class Meta:
        model = Usuario
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "empresa",
            "rol_sistema",
            "es_administrador_empresa",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["groups"].initial = self.instance.groups.all()
        textos = {
            "username": ("Usuario", "Nombre con el que la persona iniciara sesion."),
            "first_name": ("Nombres", ""),
            "last_name": ("Apellidos", ""),
            "email": ("Correo electronico", ""),
            "empresa": ("Empresa", "Empresa a la que pertenece este usuario."),
            "rol_sistema": ("Rol del sistema", "Perfil funcional que determina a que secciones puede entrar."),
            "es_administrador_empresa": ("Es administrador de empresa", "Si esta activo, el usuario podra operar toda su empresa."),
            "is_active": ("Usuario activo", "Si lo desactivas, la cuenta queda bloqueada sin borrar el historial."),
            "is_staff": ("Acceso tecnico al admin Django", "Usalo solo si quieres permitir el ingreso al admin tecnico."),
            "is_superuser": ("Es superadministrador", "Esta opcion da control total del sistema."),
            "groups": ("Roles complementarios", "Roles adicionales basados en grupos de Django."),
            "nueva_password": ("Nueva contrasena", "Dejalo vacio si no deseas cambiar la contrasena actual."),
            "confirmar_password": ("Confirmar contrasena", ""),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get("nueva_password")
        confirmar = cleaned_data.get("confirmar_password")
        if nueva or confirmar:
            if nueva != confirmar:
                raise forms.ValidationError("Las contrasenas no coinciden.")
            if not nueva:
                raise forms.ValidationError("Debes escribir la nueva contrasena.")
        return cleaned_data

    def save(self, commit=True):
        usuario = super().save(commit=commit)
        if commit:
            usuario.groups.set(self.cleaned_data.get("groups", []))
            nueva = self.cleaned_data.get("nueva_password")
            if nueva:
                usuario.set_password(nueva)
                usuario.save(update_fields=["password"])
        return usuario


class PlanComercialForm(forms.ModelForm):
    modulos = forms.ModelMultipleChoiceField(
        queryset=Modulo.objects.filter(es_comercial=True).order_by("nombre"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Modulos incluidos",
    )

    class Meta:
        model = PlanComercial
        fields = ["nombre", "codigo", "descripcion", "precio_mensual", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["modulos"].initial = Modulo.objects.filter(
                planmodulo__plan=self.instance,
                planmodulo__activo=True,
                es_comercial=True,
            )
        textos = {
            "nombre": ("Nombre del plan", ""),
            "codigo": ("Codigo interno", "Identificador tecnico del plan, por ejemplo plan-pro."),
            "descripcion": ("Descripcion", ""),
            "precio_mensual": ("Precio mensual", ""),
            "activo": ("Plan activo", "Si lo desactivas, ya no deberia ofrecerse a nuevas empresas."),
            "modulos": ("Modulos incluidos", "Esto define exactamente que areas del ERP estas vendiendo en este plan."),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text

    def save(self, commit=True):
        plan = super().save(commit=commit)
        if commit:
            self._save_modules(plan)
        return plan

    def save_m2m(self):
        super().save_m2m()
        if self.instance.pk:
            self._save_modules(self.instance)

    def _save_modules(self, plan):
        seleccionados = set(self.cleaned_data.get("modulos", []))
        actuales = {
            relacion.modulo_id: relacion
            for relacion in PlanModulo.objects.filter(plan=plan).select_related("modulo")
        }

        for modulo in Modulo.objects.filter(es_comercial=True):
            relacion = actuales.get(modulo.id)
            activo = modulo in seleccionados
            if relacion:
                if relacion.activo != activo:
                    relacion.activo = activo
                    relacion.save(update_fields=["activo"])
            elif activo:
                PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)


class RolSistemaForm(forms.ModelForm):
    class Meta:
        model = RolSistema
        fields = [
            "nombre",
            "codigo",
            "descripcion",
            "activo",
            "puede_facturas",
            "puede_clientes",
            "puede_productos",
            "puede_proveedores",
            "puede_inventario",
            "puede_compras",
            "puede_cai",
            "puede_impuestos",
            "puede_notas_credito",
            "puede_recibos",
            "puede_egresos",
            "puede_reportes",
            "puede_cxc",
            "puede_cxp",
            "puede_contabilidad",
            "puede_crear_facturas",
            "puede_editar_facturas",
            "puede_anular_facturas",
            "puede_eliminar_borradores",
            "puede_registrar_pagos_clientes",
            "puede_crear_clientes",
            "puede_editar_clientes",
            "puede_crear_productos",
            "puede_editar_productos",
            "puede_crear_proveedores",
            "puede_editar_proveedores",
            "puede_ajustar_inventario",
            "puede_crear_compras",
            "puede_editar_compras",
            "puede_aplicar_compras",
            "puede_anular_compras",
            "puede_registrar_pagos_proveedores",
            "puede_crear_notas_credito",
            "puede_editar_notas_credito",
            "puede_anular_notas_credito",
            "puede_exportar_reportes",
            "puede_catalogo_cuentas",
            "puede_crear_asientos",
            "puede_contabilizar_asientos",
            "puede_reportes_contables",
            "puede_rrhh",
            "puede_empleados",
            "puede_planillas",
            "puede_vacaciones",
            "puede_configuracion_rrhh",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        textos = {
            "nombre": ("Nombre del rol", ""),
            "codigo": ("Codigo interno", "Identificador tecnico del rol, por ejemplo solo-facturas."),
            "descripcion": ("Descripcion", ""),
            "activo": ("Rol activo", ""),
            "puede_facturas": ("Puede entrar a facturas", ""),
            "puede_clientes": ("Puede entrar a clientes", ""),
            "puede_productos": ("Puede entrar a productos", ""),
            "puede_proveedores": ("Puede entrar a proveedores", ""),
            "puede_inventario": ("Puede entrar a inventario", ""),
            "puede_compras": ("Puede entrar a compras", ""),
            "puede_cai": ("Puede entrar a CAI", ""),
            "puede_impuestos": ("Puede entrar a impuestos", ""),
            "puede_notas_credito": ("Puede entrar a notas de credito", ""),
            "puede_recibos": ("Puede entrar a recibos", ""),
            "puede_egresos": ("Puede entrar a egresos", ""),
            "puede_reportes": ("Puede entrar a reportes", ""),
            "puede_cxc": ("Puede entrar a cuentas por cobrar", ""),
            "puede_cxp": ("Puede entrar a cuentas por pagar", ""),
            "puede_contabilidad": ("Puede entrar a contabilidad", ""),
            "puede_crear_facturas": ("Puede crear facturas", ""),
            "puede_editar_facturas": ("Puede editar facturas", ""),
            "puede_anular_facturas": ("Puede anular facturas", ""),
            "puede_eliminar_borradores": ("Puede eliminar borradores", ""),
            "puede_registrar_pagos_clientes": ("Puede registrar pagos de clientes", ""),
            "puede_crear_clientes": ("Puede crear clientes", ""),
            "puede_editar_clientes": ("Puede editar clientes", ""),
            "puede_crear_productos": ("Puede crear productos", ""),
            "puede_editar_productos": ("Puede editar productos", ""),
            "puede_crear_proveedores": ("Puede crear proveedores", ""),
            "puede_editar_proveedores": ("Puede editar proveedores", ""),
            "puede_ajustar_inventario": ("Puede ajustar inventario", ""),
            "puede_crear_compras": ("Puede crear compras", ""),
            "puede_editar_compras": ("Puede editar compras", ""),
            "puede_aplicar_compras": ("Puede aplicar compras", ""),
            "puede_anular_compras": ("Puede anular compras", ""),
            "puede_registrar_pagos_proveedores": ("Puede registrar pagos a proveedores", ""),
            "puede_crear_notas_credito": ("Puede crear notas de credito", ""),
            "puede_editar_notas_credito": ("Puede editar notas de credito", ""),
            "puede_anular_notas_credito": ("Puede anular notas de credito", ""),
            "puede_exportar_reportes": ("Puede exportar reportes", ""),
            "puede_catalogo_cuentas": ("Puede gestionar catalogo de cuentas", ""),
            "puede_crear_asientos": ("Puede crear asientos contables", ""),
            "puede_contabilizar_asientos": ("Puede contabilizar asientos", ""),
            "puede_reportes_contables": ("Puede ver reportes contables", ""),
            "puede_rrhh": ("Puede entrar a recursos humanos", ""),
            "puede_empleados": ("Puede gestionar empleados", ""),
            "puede_planillas": ("Puede gestionar planillas", ""),
            "puede_vacaciones": ("Puede gestionar vacaciones", ""),
            "puede_configuracion_rrhh": ("Puede configurar RRHH", ""),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text


class PagoLicenciaEmpresaForm(forms.ModelForm):
    class Meta:
        model = PagoLicenciaEmpresa
        fields = [
            "plan_comercial",
            "fecha_pago",
            "cantidad_meses",
            "monto",
            "metodo",
            "referencia",
            "observacion",
        ]

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        if "fecha_pago" in self.fields:
            self.fields["fecha_pago"].widget = forms.DateInput(attrs={"type": "date"})
        if empresa:
            self.fields["plan_comercial"].initial = empresa.plan_comercial
        textos = {
            "plan_comercial": ("Plan cobrado", "Puedes confirmar con que plan se cobro esta renovacion."),
            "fecha_pago": ("Fecha del pago", ""),
            "cantidad_meses": ("Meses pagados", "Ejemplo: 1 mensual, 3 trimestral, 12 anual."),
            "monto": ("Monto recibido", ""),
            "metodo": ("Metodo de pago", ""),
            "referencia": ("Referencia", "Numero de transferencia, deposito o comprobante."),
            "observacion": ("Observacion", "Nota interna comercial sobre este pago o renovacion."),
        }
        for field_name, (label, help_text) in textos.items():
            if field_name in self.fields:
                self.fields[field_name].label = label
                if help_text:
                    self.fields[field_name].help_text = help_text

    def clean_cantidad_meses(self):
        cantidad = self.cleaned_data["cantidad_meses"]
        if cantidad <= 0:
            raise forms.ValidationError("La cantidad de meses debe ser mayor que cero.")
        return cantidad
