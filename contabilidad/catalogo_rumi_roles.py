"""Roles operativos del catálogo propio de RUMI, sin crear cuentas supletorias."""

from django.core.exceptions import ValidationError


ALIAS = {
    "ventas_15": "ventas",
    "ventas_18": "ventas",
    "ventas_exentas": "ventas",
    "ventas_exoneradas": "ventas",
    "isv_15_por_pagar": "isv_por_pagar",
    "isv_18_por_pagar": "isv_por_pagar",
}

CAMPOS_CONFIG = {
    "caja": "cuenta_caja",
    "bancos": "cuenta_bancos",
    "clientes": "cuenta_clientes",
    "inventario": "cuenta_inventario",
    "isv_por_pagar": "cuenta_isv_por_pagar",
    "proveedores": "cuenta_proveedores",
    "ventas": "cuenta_ventas",
    "devoluciones_ventas": "cuenta_devoluciones_ventas",
    "isv_acreditable": "cuenta_isv_acreditable",
    "entradas_inventario": "cuenta_entradas_inventario",
}

# Un código sugerido identifica un equivalente del archivo; nunca autoriza su
# activación ni supone que el impuesto, la nómina o la operación sean aplicables.
ROLES = {
    "caja": {"etiqueta": "Caja", "tipos": ("activo",), "codigo_sugerido": None},
    "bancos": {"etiqueta": "Bancos", "tipos": ("activo",), "codigo_sugerido": None},
    "clientes": {"etiqueta": "Clientes por cobrar", "tipos": ("activo",), "codigo_sugerido": None},
    "isr_retenido_clientes": {"etiqueta": "ISR retenido por clientes", "tipos": ("activo",), "codigo_sugerido": "10303002"},
    "isv_retenido_clientes": {"etiqueta": "ISV retenido por clientes", "tipos": ("activo",), "codigo_sugerido": None},
    "isv_acreditable": {"etiqueta": "ISV acreditable de compras", "tipos": ("activo",), "codigo_sugerido": "10303003"},
    "inventario": {"etiqueta": "Inventario", "tipos": ("activo",), "codigo_sugerido": None},
    "isv_por_pagar": {"etiqueta": "ISV por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20401004"},
    "proveedores": {"etiqueta": "Proveedores nacionales por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20201001"},
    "ventas": {"etiqueta": "Ingresos de facturación", "tipos": ("ingreso",), "codigo_sugerido": None},
    "compras": {"etiqueta": "Compras llevadas al resultado", "tipos": ("costo", "gasto"), "codigo_sugerido": None},
    "devoluciones_ventas": {"etiqueta": "Devoluciones de facturación", "tipos": ("ingreso",), "codigo_sugerido": None},
    "costo_ventas": {"etiqueta": "Costo de ventas", "tipos": ("costo", "gasto"), "codigo_sugerido": None},
    "gasto_salarios": {"etiqueta": "Sueldos y salarios", "tipos": ("gasto",), "codigo_sugerido": "60301001"},
    "gasto_comisiones_ventas": {"etiqueta": "Comisiones comerciales", "tipos": ("gasto",), "codigo_sugerido": None},
    "sueldos_por_pagar": {"etiqueta": "Sueldos por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20301001"},
    "ihss_por_pagar": {"etiqueta": "IHSS por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20302001"},
    "rap_por_pagar": {"etiqueta": "RAP por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20302002"},
    "isr_por_pagar": {"etiqueta": "ISR retenido a empleados por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20402001"},
    "otras_deducciones_por_pagar": {"etiqueta": "Otras deducciones de empleados por pagar", "tipos": ("pasivo",), "codigo_sugerido": "20302004"},
    "anticipos_empleados": {"etiqueta": "Anticipos y liquidaciones de empleados", "tipos": ("activo",), "codigo_sugerido": "10302002"},
    "ganancia_cambiaria": {"etiqueta": "Ganancia por diferencia de cambio", "tipos": ("ingreso",), "codigo_sugerido": "50103001"},
    "perdida_cambiaria": {"etiqueta": "Pérdida por diferencia de cambio", "tipos": ("gasto",), "codigo_sugerido": "60103001"},
    "ajustes_positivos_inventario": {"etiqueta": "Ajustes positivos de inventario", "tipos": ("ingreso",), "codigo_sugerido": None},
    "ajustes_negativos_inventario": {"etiqueta": "Ajustes negativos de inventario", "tipos": ("gasto",), "codigo_sugerido": None},
    "entradas_inventario": {
        "etiqueta": "Contrapartida de entradas manuales de inventario",
        "tipos": ("activo", "pasivo", "patrimonio", "ingreso", "costo", "gasto"),
        "codigo_sugerido": None,
    },
}


def usa_catalogo_rumi(empresa):
    """Consulta la empresa y el indicador guardados, sin confiar en el formulario."""
    if not empresa or not getattr(empresa, "pk", None):
        return False
    from .alcance_catalogo import SLUG_EMPRESA_CATALOGO_RUMI
    from .models import ConfiguracionContableEmpresa

    using = getattr(getattr(empresa, "_state", None), "db", None) or "default"
    return ConfiguracionContableEmpresa.objects.using(using).filter(
        empresa_id=empresa.pk,
        empresa__slug=SLUG_EMPRESA_CATALOGO_RUMI,
        catalogo_rumi_instalado=True,
    ).exists()


def validar_cuenta_rol_rumi(empresa_id, clave, cuenta):
    """Valida un enlace vigente antes de que una operación utilice su cuenta."""
    clave = ALIAS.get(clave, clave)
    definicion = ROLES.get(clave)
    if definicion is None:
        raise ValidationError("El rol contable solicitado no está definido para el catálogo de RUMI.")
    etiqueta = definicion["etiqueta"]
    if cuenta is None:
        raise ValidationError(
            f"Configure la cuenta de «{etiqueta}» en RUMI antes de registrar esta operación. "
            "El sistema no creará una cuenta del catálogo base Honduras."
        )
    if cuenta.empresa_id != empresa_id:
        raise ValidationError(f"La cuenta de «{etiqueta}» debe pertenecer a RUMI.")
    if cuenta.tipo not in definicion["tipos"]:
        tipos = ", ".join(definicion["tipos"])
        raise ValidationError(f"La cuenta de «{etiqueta}» debe ser de tipo {tipos}.")
    if not cuenta.activa or not cuenta.acepta_movimientos:
        raise ValidationError(
            f"La cuenta de «{etiqueta}» está inactiva o no admite movimientos. "
            "Revise el enlace y la activación de la cuenta antes de continuar."
        )
    return cuenta


def resolver_cuenta_rumi(empresa, clave):
    """Resuelve solo cuentas propias explícitamente enlazadas y activas de RUMI."""
    from .alcance_catalogo import SLUG_EMPRESA_CATALOGO_RUMI
    from .models import ConfiguracionContableEmpresa, CuentaContable, VinculoCuentaOperativaRumi

    if not empresa or not getattr(empresa, "pk", None):
        raise ValidationError("Seleccione una empresa RUMI guardada para resolver sus cuentas operativas.")
    using = getattr(getattr(empresa, "_state", None), "db", None) or "default"
    configuracion = ConfiguracionContableEmpresa.objects.using(using).filter(
        empresa_id=empresa.pk,
        empresa__slug=SLUG_EMPRESA_CATALOGO_RUMI,
        catalogo_rumi_instalado=True,
    ).first()
    if configuracion is None:
        raise ValidationError("El catálogo propio de RUMI no está instalado en esta empresa.")
    clave = ALIAS.get(clave, clave)
    if clave not in ROLES:
        raise ValidationError("El rol contable solicitado no está definido para el catálogo de RUMI.")
    campo = CAMPOS_CONFIG.get(clave)
    cuenta_id = getattr(configuracion, f"{campo}_id", None) if campo else None
    if not cuenta_id:
        cuenta_id = VinculoCuentaOperativaRumi.objects.using(using).filter(
            empresa_id=empresa.pk, clave=clave,
        ).values_list("cuenta_id", flat=True).first()
    cuenta = CuentaContable.objects.using(using).filter(pk=cuenta_id).first() if cuenta_id else None
    return validar_cuenta_rol_rumi(empresa.pk, clave, cuenta)
