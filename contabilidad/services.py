from decimal import Decimal
from functools import wraps

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AsientoContable, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, ReglaClasificacionBanco
from .moneda import convertir_a_lempiras, convertir_partidas_balanceadas, distribuir_conversion, importe_historico_parcial, redondear_importe, validar_moneda_tipo_cambio


CUENTAS_BASE = {
    "caja": {"codigo": "1101", "nombre": "Caja General", "tipo": "activo"},
    "bancos": {"codigo": "110201", "nombre": "Banco Moneda Nacional", "tipo": "activo"},
    "clientes": {"codigo": "111001", "nombre": "Clientes Nacionales", "tipo": "activo"},
    "isr_retenido_clientes": {"codigo": "113003", "nombre": "ISR Retenido por Clientes", "tipo": "activo"},
    "isv_retenido_clientes": {"codigo": "113005", "nombre": "ISV Retenido por Clientes", "tipo": "activo"},
    "isv_acreditable": {"codigo": "113001", "nombre": "ISV Acreditable en Compras", "tipo": "activo"},
    "inventario": {"codigo": "112001", "nombre": "Inventario de Mercaderia", "tipo": "activo"},
    "isv_por_pagar": {"codigo": "210201", "nombre": "ISV Cobrado 15%", "tipo": "pasivo"},
    "isv_15_por_pagar": {"codigo": "210201", "nombre": "ISV Cobrado 15%", "tipo": "pasivo"},
    "isv_18_por_pagar": {"codigo": "210202", "nombre": "ISV Cobrado 18%", "tipo": "pasivo"},
    "proveedores": {"codigo": "210101", "nombre": "Proveedores Nacionales", "tipo": "pasivo"},
    "ventas": {"codigo": "410101", "nombre": "Ventas Gravadas 15%", "tipo": "ingreso"},
    "ventas_15": {"codigo": "410101", "nombre": "Ventas Gravadas 15%", "tipo": "ingreso"},
    "ventas_18": {"codigo": "410102", "nombre": "Ventas Gravadas 18%", "tipo": "ingreso"},
    "ventas_exentas": {"codigo": "410103", "nombre": "Ventas Exentas", "tipo": "ingreso"},
    "ventas_exoneradas": {"codigo": "410104", "nombre": "Ventas Exoneradas", "tipo": "ingreso"},
    "compras": {"codigo": "5102", "nombre": "Compras", "tipo": "costo"},
    "devoluciones_ventas": {"codigo": "410202", "nombre": "Devoluciones sobre Ventas", "tipo": "ingreso"},
    "costo_ventas": {"codigo": "5101", "nombre": "Costo de Ventas", "tipo": "costo"},
    "gasto_salarios": {"codigo": "610101", "nombre": "Sueldos y Salarios", "tipo": "gasto"},
    "gasto_comisiones_ventas": {"codigo": "610302", "nombre": "Comisiones sobre Ventas", "tipo": "gasto"},
    "sueldos_por_pagar": {"codigo": "210301", "nombre": "Sueldos por Pagar", "tipo": "pasivo"},
    "ihss_por_pagar": {"codigo": "210302", "nombre": "IHSS por Pagar", "tipo": "pasivo"},
    "rap_por_pagar": {"codigo": "210303", "nombre": "RAP por Pagar", "tipo": "pasivo"},
    "isr_por_pagar": {"codigo": "210203", "nombre": "ISR por Pagar", "tipo": "pasivo"},
    "otras_deducciones_por_pagar": {"codigo": "210304", "nombre": "Otras Deducciones por Pagar", "tipo": "pasivo"},
    "anticipos_empleados": {"codigo": "114002", "nombre": "Anticipos a Empleados", "tipo": "activo"},
    "ganancia_cambiaria": {"codigo": "420101", "nombre": "Ganancia por Diferencia Cambiaria", "tipo": "ingreso"},
    "perdida_cambiaria": {"codigo": "610203", "nombre": "Pérdida por Diferencia Cambiaria", "tipo": "gasto"},
    "ajustes_positivos_inventario": {"codigo": "420102", "nombre": "Diferencias Positivas de Inventario", "tipo": "ingreso"},
    "ajustes_negativos_inventario": {"codigo": "610114", "nombre": "Pérdidas y Ajustes Negativos de Inventario", "tipo": "gasto"},
}


CATALOGO_BASE_HONDURAS = [
    ("1", "ACTIVOS", "activo", "", False),
    ("11", "ACTIVO CORRIENTE", "activo", "1", False),
    ("1101", "Caja General", "activo", "11", True),
    ("110101", "Caja Chica", "activo", "1101", True),
    ("1102", "Bancos", "activo", "11", True),
    ("110201", "Banco Moneda Nacional", "activo", "1102", True),
    ("110202", "Banco Moneda Extranjera", "activo", "1102", True),
    ("1103", "Tarjetas y Pasarelas por Cobrar", "activo", "11", True),
    ("1110", "Cuentas por Cobrar Clientes", "activo", "11", True),
    ("111001", "Clientes Nacionales", "activo", "1110", True),
    ("111002", "Documentos por Cobrar", "activo", "1110", True),
    ("111003", "Estimacion para Cuentas Incobrables", "activo", "1110", True),
    ("1120", "Inventarios", "activo", "11", True),
    ("112001", "Inventario de Mercaderia", "activo", "1120", True),
    ("112002", "Inventario en Transito", "activo", "1120", True),
    ("1130", "Impuestos por Acreditar", "activo", "11", False),
    ("113001", "ISV Pagado 15%", "activo", "1130", True),
    ("113002", "ISV Pagado 18%", "activo", "1130", True),
    ("113003", "ISR Retenido por Clientes", "activo", "1130", True),
    ("113004", "Pagos a Cuenta ISR", "activo", "1130", True),
    ("113005", "ISV Retenido por Clientes", "activo", "1130", True),
    ("1140", "Anticipos", "activo", "11", False),
    ("114001", "Anticipos a Proveedores", "activo", "1140", True),
    ("114002", "Anticipos a Empleados", "activo", "1140", True),
    ("12", "ACTIVO NO CORRIENTE", "activo", "1", False),
    ("1201", "Propiedad Planta y Equipo", "activo", "12", False),
    ("120101", "Terrenos", "activo", "1201", True),
    ("120102", "Edificios", "activo", "1201", True),
    ("120103", "Mobiliario y Equipo", "activo", "1201", True),
    ("120104", "Equipo de Computo", "activo", "1201", True),
    ("120105", "Vehiculos", "activo", "1201", True),
    ("1202", "Depreciacion Acumulada", "activo", "12", False),
    ("120201", "Depreciacion Acumulada Edificios", "activo", "1202", True),
    ("120202", "Depreciacion Acumulada Mobiliario y Equipo", "activo", "1202", True),
    ("120203", "Depreciacion Acumulada Equipo de Computo", "activo", "1202", True),
    ("120204", "Depreciacion Acumulada Vehiculos", "activo", "1202", True),
    ("2", "PASIVOS", "pasivo", "", False),
    ("21", "PASIVO CORRIENTE", "pasivo", "2", False),
    ("2101", "Cuentas por Pagar Proveedores", "pasivo", "21", True),
    ("210101", "Proveedores Nacionales", "pasivo", "2101", True),
    ("210102", "Proveedores Extranjeros", "pasivo", "2101", True),
    ("2102", "Impuestos por Pagar", "pasivo", "21", False),
    ("210201", "ISV Cobrado 15%", "pasivo", "2102", True),
    ("210202", "ISV Cobrado 18%", "pasivo", "2102", True),
    ("210203", "ISR por Pagar", "pasivo", "2102", True),
    ("210204", "Retenciones por Pagar", "pasivo", "2102", True),
    ("210205", "Impuestos Municipales por Pagar", "pasivo", "2102", True),
    ("2103", "Obligaciones Laborales", "pasivo", "21", False),
    ("210301", "Sueldos por Pagar", "pasivo", "2103", True),
    ("210302", "IHSS por Pagar", "pasivo", "2103", True),
    ("210303", "RAP por Pagar", "pasivo", "2103", True),
    ("210304", "INJUPEMP u Otras Deducciones por Pagar", "pasivo", "2103", True),
    ("210305", "Decimo Tercer Mes por Pagar", "pasivo", "2103", True),
    ("210306", "Decimo Cuarto Mes por Pagar", "pasivo", "2103", True),
    ("210307", "Prestaciones Laborales por Pagar", "pasivo", "2103", True),
    ("2104", "Prestamos y Tarjetas por Pagar", "pasivo", "21", False),
    ("210401", "Prestamos Bancarios Corto Plazo", "pasivo", "2104", True),
    ("210402", "Tarjetas de Credito por Pagar", "pasivo", "2104", True),
    ("22", "PASIVO NO CORRIENTE", "pasivo", "2", False),
    ("2201", "Prestamos Bancarios Largo Plazo", "pasivo", "22", True),
    ("3", "PATRIMONIO", "patrimonio", "", False),
    ("3101", "Capital Social", "patrimonio", "3", True),
    ("3102", "Aportaciones de Socios", "patrimonio", "3", True),
    ("3103", "Utilidades Acumuladas", "patrimonio", "3", True),
    ("3104", "Utilidad o Perdida del Periodo", "patrimonio", "3", True),
    ("4", "INGRESOS", "ingreso", "", False),
    ("4101", "Ventas", "ingreso", "4", False),
    ("410101", "Ventas Gravadas 15%", "ingreso", "4101", True),
    ("410102", "Ventas Gravadas 18%", "ingreso", "4101", True),
    ("410103", "Ventas Exentas", "ingreso", "4101", True),
    ("410104", "Ventas Exoneradas", "ingreso", "4101", True),
    ("4102", "Descuentos y Devoluciones sobre Ventas", "ingreso", "4", False),
    ("410201", "Descuentos sobre Ventas", "ingreso", "4102", True),
    ("410202", "Devoluciones sobre Ventas", "ingreso", "4102", True),
    ("4201", "Otros Ingresos", "ingreso", "4", True),
    ("420101", "Ganancia por Diferencia Cambiaria", "ingreso", "4201", True),
    ("420102", "Diferencias Positivas de Inventario", "ingreso", "4201", True),
    ("5", "COSTOS", "costo", "", False),
    ("5101", "Costo de Ventas", "costo", "5", True),
    ("5102", "Compras", "costo", "5", True),
    ("5103", "Fletes sobre Compras", "costo", "5", True),
    ("5104", "Ajustes de Inventario", "costo", "5", True),
    ("6", "GASTOS", "gasto", "", False),
    ("6101", "Gastos Administrativos", "gasto", "6", False),
    ("610101", "Sueldos y Salarios", "gasto", "6101", True),
    ("610102", "Honorarios Profesionales", "gasto", "6101", True),
    ("610103", "Alquileres", "gasto", "6101", True),
    ("610104", "Servicios Publicos", "gasto", "6101", True),
    ("610105", "Internet y Telefonia", "gasto", "6101", True),
    ("610106", "Papeleria y Utiles", "gasto", "6101", True),
    ("610107", "Combustibles y Lubricantes", "gasto", "6101", True),
    ("610108", "Mantenimiento y Reparaciones", "gasto", "6101", True),
    ("610109", "Seguridad y Vigilancia", "gasto", "6101", True),
    ("610110", "Seguros", "gasto", "6101", True),
    ("610111", "Gastos de Viaje y Viaticos", "gasto", "6101", True),
    ("610112", "Impuestos y Tasas Municipales", "gasto", "6101", True),
    ("610113", "Depreciaciones", "gasto", "6101", True),
    ("610114", "Pérdidas y Ajustes Negativos de Inventario", "gasto", "6101", True),
    ("6102", "Gastos Financieros", "gasto", "6", False),
    ("610201", "Comisiones Bancarias", "gasto", "6102", True),
    ("610202", "Intereses Bancarios", "gasto", "6102", True),
    ("610203", "Diferencial Cambiario", "gasto", "6102", True),
    ("6103", "Gastos de Venta", "gasto", "6", False),
    ("610301", "Publicidad y Mercadeo", "gasto", "6103", True),
    ("610302", "Comisiones sobre Ventas", "gasto", "6103", True),
    ("610303", "Fletes sobre Ventas", "gasto", "6103", True),
]


CUENTAS_FINANCIERAS_BASE_HONDURAS = [
    ("Caja General", "caja", "1101", "Caja"),
    ("Banco Principal HNL", "banco", "110201", "Banco"),
]


CLASIFICACIONES_BANCARIAS_BASE_HONDURAS = [
    ("Cobro de Clientes", "ingreso", "1110"),
    ("Otros Ingresos Bancarios", "ingreso", "4201"),
    ("Pago a Proveedores", "egreso", "2101"),
    ("Comisiones Bancarias", "egreso", "610201"),
    ("Intereses Bancarios", "egreso", "610202"),
    ("Servicios Publicos", "egreso", "610104"),
    ("Combustibles y Lubricantes", "egreso", "610107"),
    ("Internet y Telefonia", "egreso", "610105"),
]


def contabilidad_activa_para_empresa(empresa):
    return bool(empresa and empresa.tiene_modulo_activo("contabilidad"))


def _serializar_catalogo_rumi(funcion):
    """Coordina la creación automática con la instalación del catálogo propio."""
    @wraps(funcion)
    def ejecutada(empresa, *args, **kwargs):
        from core.models import Empresa

        from .alcance_catalogo import es_empresa_rumi

        if not es_empresa_rumi(empresa):
            return funcion(empresa, *args, **kwargs)
        with transaction.atomic():
            Empresa.objects.select_for_update().get(pk=empresa.pk)
            return funcion(empresa, *args, **kwargs)

    return ejecutada


def _serializar_auxiliar_rumi(funcion):
    @wraps(funcion)
    def ejecutada(entidad, *args, **kwargs):
        if not entidad or not getattr(entidad, "empresa_id", None):
            return funcion(entidad, *args, **kwargs)
        return _serializar_catalogo_rumi(
            lambda empresa: funcion(entidad, *args, **kwargs)
        )(entidad.empresa)

    return ejecutada


@_serializar_catalogo_rumi
def asegurar_cuentas_financieras_base_honduras(empresa):
    from .catalogo_rumi_roles import usa_catalogo_rumi

    if usa_catalogo_rumi(empresa):
        return {"creadas": 0, "existentes": 0}

    creadas = 0
    existentes = 0

    for nombre, tipo, codigo_cuenta, institucion in CUENTAS_FINANCIERAS_BASE_HONDURAS:
        cuenta_contable = CuentaContable.objects.filter(
            empresa=empresa,
            codigo=codigo_cuenta,
            activa=True,
            acepta_movimientos=True,
        ).first()
        if not cuenta_contable:
            continue

        cuenta_financiera, creada = CuentaFinanciera.objects.get_or_create(
            empresa=empresa,
            nombre=nombre,
            defaults={
                "tipo": tipo,
                "institucion": institucion,
                "cuenta_contable": cuenta_contable,
                "activa": True,
            },
        )
        if creada:
            creadas += 1
        else:
            existentes += 1
            cambios = []
            if not cuenta_financiera.activa:
                cuenta_financiera.activa = True
                cambios.append("activa")
            if cuenta_financiera.cuenta_contable_id != cuenta_contable.id:
                cuenta_financiera.cuenta_contable = cuenta_contable
                cambios.append("cuenta_contable")
            if cuenta_financiera.tipo != tipo:
                cuenta_financiera.tipo = tipo
                cambios.append("tipo")
            if cuenta_financiera.institucion != institucion:
                cuenta_financiera.institucion = institucion
                cambios.append("institucion")
            if cambios:
                cuenta_financiera.save(update_fields=cambios)

    return {"creadas": creadas, "existentes": existentes}


@_serializar_catalogo_rumi
def asegurar_clasificaciones_bancarias_base_honduras(empresa):
    from .catalogo_rumi_roles import usa_catalogo_rumi

    if usa_catalogo_rumi(empresa):
        return {"creadas": 0, "existentes": 0}

    creadas = 0
    existentes = 0

    for nombre, tipo, codigo_cuenta in CLASIFICACIONES_BANCARIAS_BASE_HONDURAS:
        cuenta_contable = CuentaContable.objects.filter(
            empresa=empresa,
            codigo=codigo_cuenta,
            activa=True,
            acepta_movimientos=True,
        ).first()
        if not cuenta_contable:
            continue

        clasificacion, creada = ClasificacionMovimientoBanco.objects.get_or_create(
            empresa=empresa,
            nombre=nombre,
            defaults={
                "tipo": tipo,
                "cuenta_contable": cuenta_contable,
                "activa": True,
            },
        )
        if creada:
            creadas += 1
        else:
            existentes += 1
            if not clasificacion.activa:
                clasificacion.activa = True
                clasificacion.save(update_fields=["activa"])

    return {"creadas": creadas, "existentes": existentes}


def aplicar_reglas_clasificacion_bancaria(empresa, *, movimiento_ids=None):
    reglas = list(
        ReglaClasificacionBanco.objects.filter(empresa=empresa, activa=True)
        .select_related("clasificacion")
        .order_by("prioridad", "nombre")
    )
    if not reglas:
        return {"actualizados": 0, "sin_regla": 0}

    movimientos = MovimientoBancario.objects.filter(
        empresa=empresa,
        estado="pendiente",
        clasificacion__isnull=True,
    )
    if movimiento_ids is not None:
        movimientos = movimientos.filter(id__in=movimiento_ids)

    actualizados = 0
    sin_regla = 0
    with transaction.atomic():
        for movimiento in movimientos:
            regla_aplicada = None
            for regla in reglas:
                if regla.aplica_a(movimiento):
                    regla_aplicada = regla
                    break
            if not regla_aplicada:
                sin_regla += 1
                continue

            movimiento.clasificacion = regla_aplicada.clasificacion
            movimiento.estado = "clasificado"
            movimiento.save(update_fields=["clasificacion", "estado"])
            actualizados += 1

    return {"actualizados": actualizados, "sin_regla": sin_regla}


@_serializar_catalogo_rumi
def obtener_o_crear_cuenta_base(empresa, clave):
    from .catalogo_rumi_roles import resolver_cuenta_rumi, usa_catalogo_rumi

    if usa_catalogo_rumi(empresa):
        return resolver_cuenta_rumi(empresa, clave)

    definicion = CUENTAS_BASE[clave]
    cuenta, _ = CuentaContable.objects.get_or_create(
        empresa=empresa,
        codigo=definicion["codigo"],
        defaults={
            "nombre": definicion["nombre"],
            "tipo": definicion["tipo"],
            "acepta_movimientos": True,
            "activa": True,
        },
    )
    return cuenta


@_serializar_catalogo_rumi
def cargar_catalogo_base_honduras(empresa):
    from .catalogo_rumi_roles import usa_catalogo_rumi

    if usa_catalogo_rumi(empresa):
        raise ValidationError(
            "RUMI ya utiliza su catálogo propio. No se puede volver a cargar el catálogo base de Honduras."
        )

    creadas = 0
    existentes = 0
    cuentas_por_codigo = {
        cuenta.codigo: cuenta
        for cuenta in CuentaContable.objects.filter(empresa=empresa)
    }

    with transaction.atomic():
        for codigo, nombre, tipo, _codigo_padre, acepta_movimientos in CATALOGO_BASE_HONDURAS:
            cuenta = cuentas_por_codigo.get(codigo)
            if cuenta:
                existentes += 1
                continue
            cuenta = CuentaContable.objects.create(
                empresa=empresa,
                codigo=codigo,
                nombre=nombre,
                tipo=tipo,
                acepta_movimientos=acepta_movimientos,
                activa=True,
                descripcion="Catalogo base Honduras generado por DV Solutions ERP.",
            )
            cuentas_por_codigo[codigo] = cuenta
            creadas += 1

        for codigo, _nombre, _tipo, codigo_padre, _acepta_movimientos in CATALOGO_BASE_HONDURAS:
            if not codigo_padre:
                continue
            cuenta = cuentas_por_codigo[codigo]
            cuenta_padre = cuentas_por_codigo.get(codigo_padre)
            if cuenta_padre and cuenta.cuenta_padre_id != cuenta_padre.id:
                cuenta.cuenta_padre = cuenta_padre
                cuenta.full_clean()
                cuenta.save(update_fields=["cuenta_padre"])

    cuentas_financieras = asegurar_cuentas_financieras_base_honduras(empresa)
    clasificaciones_bancarias = asegurar_clasificaciones_bancarias_base_honduras(empresa)

    return {
        "creadas": creadas,
        "existentes": existentes,
        "total_base": len(CATALOGO_BASE_HONDURAS),
        "cuentas_financieras_creadas": cuentas_financieras["creadas"],
        "cuentas_financieras_existentes": cuentas_financieras["existentes"],
        "clasificaciones_bancarias_creadas": clasificaciones_bancarias["creadas"],
        "clasificaciones_bancarias_existentes": clasificaciones_bancarias["existentes"],
    }




def _siguiente_codigo_auxiliar(cuenta_padre):
    prefijo = f"{cuenta_padre.codigo}."
    longitud_maxima = CuentaContable._meta.get_field("codigo").max_length
    if len(prefijo) + 4 > longitud_maxima:
        raise ValidationError(
            "El código de la cuenta padre es demasiado largo para crear una cuenta auxiliar. "
            "Seleccione una cuenta padre con un código de hasta 25 caracteres."
        )
    usados = set(
        CuentaContable.objects.filter(
            empresa_id=cuenta_padre.empresa_id, codigo__startswith=prefijo
        ).values_list("codigo", flat=True)
    )
    consecutivo = 1
    while True:
        codigo = f"{prefijo}{consecutivo:04d}"
        if len(codigo) > longitud_maxima:
            raise ValidationError("La cuenta padre ya no admite nuevos códigos auxiliares.")
        if codigo not in usados:
            return codigo
        consecutivo += 1


def _validar_auxiliar_rumi(entidad, tipo, etiqueta):
    cuenta = CuentaContable.objects.filter(pk=entidad.cuenta_contable_id).first()
    if not cuenta or cuenta.empresa_id != entidad.empresa_id or cuenta.tipo != tipo:
        raise ValidationError(
            f"La cuenta del {etiqueta} debe ser de {tipo} y pertenecer a su empresa."
        )
    if not cuenta.activa or not cuenta.acepta_movimientos:
        raise ValidationError(
            f"La cuenta del {etiqueta} está inactiva o no admite movimientos. "
            "Revise su enlace contable antes de registrar una nueva operación."
        )
    return cuenta


def _asegurar_auxiliar_rumi(entidad, clave, tipo, etiqueta):
    from core.models import Empresa

    from .catalogo_rumi_roles import resolver_cuenta_rumi

    if getattr(entidad, "cuenta_contable_id", None):
        return _validar_auxiliar_rumi(entidad, tipo, etiqueta)
    if not entidad.pk:
        raise ValidationError(f"Guarde el {etiqueta} antes de crear su cuenta auxiliar.")

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=entidad.empresa_id, slug="rumi")
        entidad_guardada = (
            type(entidad).objects.select_for_update().get(pk=entidad.pk, empresa_id=entidad.empresa_id)
        )
        if entidad_guardada.cuenta_contable_id:
            cuenta = _validar_auxiliar_rumi(entidad_guardada, tipo, etiqueta)
            entidad.cuenta_contable = cuenta
            return cuenta
        cuenta_padre = resolver_cuenta_rumi(entidad.empresa, clave)
        # El padre se bloquea antes de elegir el consecutivo compartido por sus auxiliares.
        cuenta_padre = CuentaContable.objects.select_for_update().get(
            pk=cuenta_padre.pk, empresa_id=entidad.empresa_id
        )
        if cuenta_padre.tipo != tipo or not cuenta_padre.activa or not cuenta_padre.acepta_movimientos:
            raise ValidationError(
                f"La cuenta configurada para {clave} debe estar activa, admitir movimientos y ser de {tipo}."
            )
        codigo = _siguiente_codigo_auxiliar(cuenta_padre)
        es_cliente = clave == "clientes"
        cuenta = CuentaContable(
            empresa=entidad.empresa,
            cuenta_padre=cuenta_padre,
            codigo=codigo,
            nombre=f"{'Cliente' if es_cliente else 'Proveedor'} - {entidad.nombre}"[:200],
            tipo=tipo,
            descripcion=(
                f"Cuenta por {'cobrar' if es_cliente else 'pagar'} individual del {etiqueta} {entidad.nombre}."
            ),
            acepta_movimientos=True,
            activa=True,
        )
        cuenta.full_clean()
        cuenta.save()
        type(entidad).objects.filter(pk=entidad.pk, empresa_id=entidad.empresa_id).update(cuenta_contable=cuenta)
        entidad.cuenta_contable = cuenta
        return cuenta


@_serializar_auxiliar_rumi
def asegurar_cuenta_contable_cliente(cliente):
    if not cliente or not getattr(cliente, "empresa_id", None):
        return None
    from .catalogo_rumi_roles import usa_catalogo_rumi

    if usa_catalogo_rumi(cliente.empresa):
        return _asegurar_auxiliar_rumi(cliente, "clientes", "activo", "cliente")
    if getattr(cliente, "cuenta_contable_id", None):
        if cliente.cuenta_contable.empresa_id != cliente.empresa_id or cliente.cuenta_contable.tipo != 'activo':
            raise ValidationError('La cuenta del cliente debe ser de activo y pertenecer a su empresa.')
        return cliente.cuenta_contable

    cuenta_padre = obtener_o_crear_cuenta_base(cliente.empresa, "clientes")
    prefijo = f"{cuenta_padre.codigo}."
    usados = set(
        CuentaContable.objects.filter(empresa=cliente.empresa, codigo__startswith=prefijo)
        .values_list("codigo", flat=True)
    )
    consecutivo = 1
    while True:
        codigo = f"{prefijo}{consecutivo:04d}"
        if codigo not in usados:
            break
        consecutivo += 1

    cuenta = CuentaContable.objects.create(
        empresa=cliente.empresa,
        cuenta_padre=cuenta_padre,
        codigo=codigo,
        nombre=f"Cliente - {cliente.nombre}",
        tipo="activo",
        descripcion=f"Cuenta por cobrar individual del cliente {cliente.nombre}.",
        acepta_movimientos=True,
        activa=True,
    )
    type(cliente).objects.filter(pk=cliente.pk).update(cuenta_contable=cuenta)
    cliente.cuenta_contable = cuenta
    return cuenta


@_serializar_auxiliar_rumi
def asegurar_cuenta_contable_proveedor(proveedor):
    if not proveedor or not getattr(proveedor, "empresa_id", None):
        return None
    from .catalogo_rumi_roles import usa_catalogo_rumi

    if usa_catalogo_rumi(proveedor.empresa):
        return _asegurar_auxiliar_rumi(proveedor, "proveedores", "pasivo", "proveedor")
    if getattr(proveedor, "cuenta_contable_id", None):
        if proveedor.cuenta_contable.empresa_id != proveedor.empresa_id or proveedor.cuenta_contable.tipo != 'pasivo':
            raise ValidationError('La cuenta del proveedor debe ser de pasivo y pertenecer a su empresa.')
        return proveedor.cuenta_contable

    cuenta_padre = obtener_o_crear_cuenta_base(proveedor.empresa, "proveedores")
    prefijo = f"{cuenta_padre.codigo}."
    usados = set(
        CuentaContable.objects.filter(empresa=proveedor.empresa, codigo__startswith=prefijo)
        .values_list("codigo", flat=True)
    )
    consecutivo = 1
    while True:
        codigo = f"{prefijo}{consecutivo:04d}"
        if codigo not in usados:
            break
        consecutivo += 1

    cuenta = CuentaContable.objects.create(
        empresa=proveedor.empresa,
        cuenta_padre=cuenta_padre,
        codigo=codigo,
        nombre=f"Proveedor - {proveedor.nombre}",
        tipo="pasivo",
        descripcion=f"Cuenta por pagar individual del proveedor {proveedor.nombre}.",
        acepta_movimientos=True,
        activa=True,
    )
    type(proveedor).objects.filter(pk=proveedor.pk).update(cuenta_contable=cuenta)
    proveedor.cuenta_contable = cuenta
    return cuenta

def obtener_configuracion_contable(empresa):
    configuracion, _ = ConfiguracionContableEmpresa.objects.get_or_create(empresa=empresa)
    return configuracion


@_serializar_catalogo_rumi
def obtener_cuenta_operativa(empresa, clave):
    from .catalogo_rumi_roles import resolver_cuenta_rumi, usa_catalogo_rumi

    if usa_catalogo_rumi(empresa):
        return resolver_cuenta_rumi(empresa, clave)

    alias_configuracion = {
        "ventas_15": "ventas", "ventas_18": "ventas", "ventas_exentas": "ventas", "ventas_exoneradas": "ventas",
        "isv_15_por_pagar": "isv_por_pagar", "isv_18_por_pagar": "isv_por_pagar",
    }
    campo = f"cuenta_{alias_configuracion.get(clave, clave)}"
    configuracion = obtener_configuracion_contable(empresa)
    cuenta = getattr(configuracion, campo, None)
    if cuenta and cuenta.activa and cuenta.acepta_movimientos and cuenta.empresa_id == empresa.id:
        return cuenta
    return obtener_o_crear_cuenta_base(empresa, clave)


def metodo_a_clave_cuenta(metodo):
    return "caja" if metodo == "efectivo" else "bancos"


def registrar_asiento_documento(
    *,
    empresa,
    documento_tipo,
    documento_id,
    evento,
    fecha,
    descripcion,
    referencia,
    origen_modulo,
    creado_por=None,
    lineas=None,
    actualizar_borrador=False,
    moneda_origen="HNL",
    tipo_cambio=1,
    conversion_verificada=True,
):
    if not lineas:
        return None

    asiento_existente = AsientoContable.objects.filter(
        empresa=empresa,
        documento_tipo=documento_tipo,
        documento_id=documento_id,
        evento=evento,
    ).first()
    if asiento_existente and not (actualizar_borrador and asiento_existente.estado == "borrador"):
        return asiento_existente

    moneda_origen, tipo_cambio = validar_moneda_tipo_cambio(moneda_origen, tipo_cambio)
    lineas_normalizadas = []
    total_debe = Decimal("0.00")
    total_haber = Decimal("0.00")
    for linea in lineas:
        debe = Decimal(linea.get("debe", 0) or 0).quantize(Decimal("0.01"))
        haber = Decimal(linea.get("haber", 0) or 0).quantize(Decimal("0.01"))
        if debe < 0 or haber < 0 or (debe > 0 and haber > 0):
            raise ValidationError("Las lineas automaticas deben contener valores validos en debe o haber.")
        if debe == 0 and haber == 0:
            continue
        lineas_normalizadas.append({**linea, "debe": debe, "haber": haber})
        total_debe += debe
        total_haber += haber
    if not lineas_normalizadas or total_debe <= 0 or total_debe != total_haber:
        raise ValidationError(
            f"El asiento automatico no esta balanceado: debe {total_debe:.2f}, haber {total_haber:.2f}."
        )

    try:
        with transaction.atomic():
            # El cierre y todas las partidas de la empresa toman este mismo bloqueo.
            # Siempre se bloquea la empresa antes que el asiento para evitar invertir el orden.
            type(empresa).objects.select_for_update().get(pk=empresa.pk)
            asiento_existente = AsientoContable.objects.select_for_update().filter(
                empresa=empresa, documento_tipo=documento_tipo, documento_id=documento_id, evento=evento,
            ).first()
            if asiento_existente and not (actualizar_borrador and asiento_existente.estado == "borrador"):
                return asiento_existente
            if asiento_existente:
                asiento = AsientoContable.objects.select_for_update().get(pk=asiento_existente.pk)
                if asiento.estado != "borrador":
                    return asiento
                asiento.lineas.all().delete()
                asiento.fecha = fecha
                asiento.descripcion = descripcion
                asiento.referencia = referencia
                asiento.origen_modulo = origen_modulo
                asiento.creado_por = creado_por
                asiento.moneda_origen = moneda_origen
                asiento.tipo_cambio = tipo_cambio
                asiento.conversion_verificada = conversion_verificada
                asiento.save(update_fields=[
                    "fecha",
                    "descripcion",
                    "referencia",
                    "origen_modulo",
                    "creado_por",
                    "moneda_origen",
                    "tipo_cambio",
                    "conversion_verificada",
                ])
            else:
                asiento = AsientoContable.objects.create(
                    empresa=empresa,
                    fecha=fecha,
                    descripcion=descripcion,
                    referencia=referencia,
                    origen_modulo=origen_modulo,
                    documento_tipo=documento_tipo,
                    documento_id=documento_id,
                    evento=evento,
                    creado_por=creado_por,
                    estado="borrador",
                    moneda_origen=moneda_origen,
                    tipo_cambio=tipo_cambio,
                    conversion_verificada=conversion_verificada,
                )

            for linea in lineas_normalizadas:
                cuenta = linea["cuenta"]
                if isinstance(cuenta, str):
                    cuenta = obtener_cuenta_operativa(empresa, cuenta)
                movimiento = LineaAsientoContable(
                    asiento=asiento,
                    cuenta=cuenta,
                    detalle=linea.get("detalle"),
                    debe=linea["debe"],
                    haber=linea["haber"],
                    moneda_origen=linea.get("moneda_origen", "HNL"),
                    tipo_cambio=linea.get("tipo_cambio", 1),
                    debe_origen=linea.get("debe_origen", linea["debe"]),
                    haber_origen=linea.get("haber_origen", linea["haber"]),
                )
                movimiento.full_clean()
                movimiento.save()

            asiento.generar_numero()
            asiento.estado = "contabilizado"
            asiento.save(update_fields=["numero", "estado"])
            return asiento
    except IntegrityError:
        existente = AsientoContable.objects.filter(
            empresa=empresa,
            documento_tipo=documento_tipo,
            documento_id=documento_id,
            evento=evento,
        ).first()
        if existente:
            return existente
        raise


def _asiento_documento_existente(empresa, documento_tipo, documento_id, evento):
    return AsientoContable.objects.filter(
        empresa=empresa, documento_tipo=documento_tipo, documento_id=documento_id, evento=evento,
    ).first()


def _revision_moneda_factura(factura, asiento_origen=None):
    from .models import RegularizacionMonedaFactura

    fuente = asiento_origen or _asiento_documento_existente(factura.empresa, "factura", factura.pk, "emision")
    if fuente and _revision_conversion(fuente):
        # La revisión integral ya compensa el ajuste parcial anterior.
        return None

    revisiones = RegularizacionMonedaFactura.objects.filter(
        empresa=factura.empresa, factura=factura,
        asiento_origen__empresa=factura.empresa, asiento_origen__documento_tipo="factura",
        asiento_origen__documento_id=factura.pk, asiento_origen__evento="emision",
        asiento_origen__estado="contabilizado", cuenta_cliente__empresa=factura.empresa,
    ).select_related("cuenta_cliente", "asiento_ajuste")
    if asiento_origen:
        revisiones = revisiones.filter(asiento_origen=asiento_origen)
    revision = revisiones.first()
    if not revision:
        return None
    if revision.asiento_ajuste_id and (
        revision.asiento_ajuste.empresa_id != factura.empresa_id
        or revision.asiento_ajuste.estado != "contabilizado"
    ):
        raise ValidationError("El asiento que respalda la regularización monetaria requiere revisión.")
    if revision.total_original <= 0 or revision.total_hnl <= 0:
        raise ValidationError("La regularización monetaria no conserva los importes completos de la factura.")
    validar_moneda_tipo_cambio("USD", revision.tipo_cambio)
    return revision


def _revision_conversion(asiento):
    from .revision_conversion import revision_conversion_vigente

    return revision_conversion_vigente(asiento)


def _validar_fecha_aplicacion_revision(revision, fecha_aplicacion):
    if revision and fecha_aplicacion and fecha_aplicacion < revision.fecha:
        raise ValidationError("La nueva aplicación debe tener una fecha igual o posterior a la revisión integral que respalda su saldo.")


def _fuente_documento_revisada(asiento, lineas, importe_documento, *, lado, nombre, fecha_aplicacion=None):
    """Obtiene una obligación de la revisión, sin convertir otra vez el legado."""
    revision = _revision_conversion(asiento)
    if not revision:
        return None
    _validar_fecha_aplicacion_revision(revision, fecha_aplicacion)
    lineas = list(lineas)
    if not lineas or len({linea.cuenta_id for linea in lineas}) != 1:
        raise ValidationError(f"La cuenta de {nombre} no tiene un respaldo único; revise sus partidas.")
    cuenta = lineas[0].cuenta
    tipo_cuenta = "activo" if lado == "debe" else "pasivo"
    if cuenta.empresa_id != asiento.empresa_id or cuenta.tipo != tipo_cuenta:
        raise ValidationError(f"La cuenta de {nombre} debe pertenecer a esta empresa.")
    ids = {linea.pk for linea in lineas}
    partidas = [partida for partida in revision.partidas if partida["linea_id"] in ids]
    if len(partidas) != len(ids) or any(partida["moneda"] != "USD" or partida.get("lado", lado) != lado for partida in partidas):
        raise ValidationError(f"Documente en dólares las partidas originales de {nombre} antes de aplicar movimientos.")
    tasas = {validar_moneda_tipo_cambio("USD", partida["tipo_cambio"])[1] for partida in partidas}
    original = sum((Decimal(partida["importe_original"]) for partida in partidas), Decimal("0"))
    importe_documento = redondear_importe(importe_documento)
    if len(tasas) != 1 or redondear_importe(original) != importe_documento or importe_documento <= 0:
        raise ValidationError(f"Los dólares y el tipo de cambio revisados de {nombre} no coinciden con el documento original.")
    lempiras = sum((Decimal(partida[f"{lado}_hnl"]) for partida in partidas), Decimal("0"))
    if lempiras <= 0:
        raise ValidationError(f"El importe revisado de {nombre} debe ser mayor que cero.")
    return "USD", tasas.pop(), cuenta, importe_documento, redondear_importe(lempiras)


def _importes_aplicacion_revisada(asiento, lineas, importe_documento, *, moneda_documento, debe_es_aplicacion=False, exigir_revision=False, fecha_aplicacion=None):
    """Suma una vez por asiento, con el signo de la aplicación o su reversión."""
    lineas = list(lineas)
    factor = Decimal("1") if debe_es_aplicacion else Decimal("-1")
    revision = _revision_conversion(asiento)
    if revision:
        _validar_fecha_aplicacion_revision(revision, fecha_aplicacion)
        ids = {linea.pk for linea in lineas}
        partidas = [partida for partida in revision.partidas if partida["linea_id"] in ids]
        if len(partidas) != len(ids) or any(partida["moneda"] != moneda_documento for partida in partidas):
            raise ValidationError("La aplicación revisada debe conservar la moneda original del documento.")
        original = Decimal("0")
        lempiras = Decimal("0")
        for partida in partidas:
            debe = Decimal(partida["debe_hnl"])
            haber = Decimal(partida["haber_hnl"])
            lado = partida.get("lado") or ("debe" if debe > haber else "haber")
            signo = factor if lado == "debe" else -factor
            original += signo * Decimal(partida["importe_original"])
            lempiras += factor * (debe - haber)
        if abs(redondear_importe(original)) != redondear_importe(importe_documento):
            raise ValidationError("La aplicación revisada no coincide con el importe original del cobro, pago o nota de crédito.")
        return redondear_importe(original), redondear_importe(lempiras)
    if exigir_revision and (
        not asiento.conversion_verificada
        or asiento.moneda_origen != moneda_documento
        or any(linea.moneda_origen != moneda_documento for linea in lineas)
    ):
        raise ValidationError("Hay un cobro, pago o nota de crédito histórico en dólares pendiente de revisión integral. Revise sus partidas antes de registrar una nueva aplicación.")
    lempiras = sum((factor * (linea.debe - linea.haber) for linea in lineas), Decimal("0"))
    if moneda_documento == "USD" and any(linea.moneda_origen != "USD" for linea in lineas):
        # En el legado las columnas de origen contienen HNL. El documento
        # conserva los USD; se cuenta una sola vez aunque tenga varias partidas.
        original = redondear_importe(importe_documento) * (1 if lempiras > 0 else -1 if lempiras < 0 else 0)
    else:
        original = sum((factor * (
            linea.debe_origen - linea.haber_origen
            if linea.debe_origen or linea.haber_origen else linea.debe - linea.haber
        ) for linea in lineas), Decimal("0"))
    return redondear_importe(original), redondear_importe(lempiras)


def _fuente_historica_factura(factura, fecha_aplicacion=None):
    """Lee el respaldo publicado, sin recalcular ni modificar el asiento original."""
    if factura.cliente.empresa_id != factura.empresa_id:
        raise ValidationError("El cliente de la factura debe pertenecer a esta empresa.")
    asiento = _asiento_documento_existente(factura.empresa, "factura", factura.pk, "emision")
    if not asiento and factura.estado == "emitida":
        # Una aplicación no puede crear un saldo negativo por faltar la emisión.
        # Si el mes original está cerrado, las protecciones del registro impiden
        # reconstruirlo y exigen regularizar la fuente en un período abierto.
        asiento = registrar_asiento_factura_emitida(factura)
    if asiento and asiento.estado != "contabilizado":
        raise ValidationError("Primero contabiliza la factura de origen antes de registrar su aplicación.")
    if not asiento:
        raise ValidationError("La aplicación contable requiere una factura de origen emitida y contabilizada.")
    lineas_cliente = list(asiento.lineas.filter(detalle__startswith="Cobro a cliente", debe__gt=0).select_related("cuenta"))
    if not lineas_cliente:
        lineas_cliente = list(asiento.lineas.filter(cuenta_id=factura.cliente.cuenta_contable_id, debe__gt=0).select_related("cuenta"))
    revisada = _fuente_documento_revisada(asiento, lineas_cliente, factura.total, lado="debe", nombre="la cuenta por cobrar", fecha_aplicacion=fecha_aplicacion)
    if revisada:
        return revisada
    revision = _revision_moneda_factura(factura, asiento)
    if revision:
        moneda, tasa = validar_moneda_tipo_cambio("USD", revision.tipo_cambio)
        return moneda, tasa, revision.cuenta_cliente, revision.total_original, revision.total_hnl
    linea = asiento.lineas.filter(detalle__startswith="Cobro a cliente").select_related("cuenta").first()
    if not linea:
        linea = asiento.lineas.filter(cuenta_id=factura.cliente.cuenta_contable_id, debe__gt=0).select_related("cuenta").first()
    if not linea:
        raise ValidationError("La cuenta por cobrar de la factura requiere revisión contable.")
    if not asiento.conversion_verificada or (factura.moneda == "USD" and asiento.moneda_origen != "USD"):
        raise ValidationError(
            "La factura en dólares tiene un asiento histórico sin conversión documentada. "
            "Revisa y regulariza su saldo en un período abierto antes de aplicar cobros o notas de crédito."
        )
    moneda, tasa = validar_moneda_tipo_cambio(asiento.moneda_origen, asiento.tipo_cambio)
    if moneda == "USD" and linea.debe_origen <= 0:
        raise ValidationError("El asiento en dólares no conserva el importe original de la factura; requiere revisión.")
    original = linea.debe_origen or redondear_importe(factura.total)
    return moneda, tasa, linea.cuenta, original, linea.debe


def _aplicaciones_historicas_factura(factura, cuenta, *, pago_actual=None, nota_actual=None, incluir_pagos=True, fecha_aplicacion=None):
    from django.db.models import Q

    pagos = factura.pagos_facturacion.filter(anulado=False).exclude(pk=pago_actual)
    notas = factura.notas_credito.exclude(pk=nota_actual)
    if notas.exclude(moneda=factura.moneda).exists():
        raise ValidationError("Hay una nota de crédito histórica cuya moneda no coincide con la factura de origen. Revise la moneda y el respaldo del documento antes de usar sus importes.")
    documentos = Q(asiento__documento_tipo="nota_credito", asiento__documento_id__in=notas.values_list("pk", flat=True))
    if incluir_pagos:
        documentos |= Q(asiento__documento_tipo="pago_factura", asiento__documento_id__in=pagos.values_list("pk", flat=True))
    lineas = LineaAsientoContable.objects.filter(
        documentos,
        asiento__empresa=factura.empresa, asiento__estado="contabilizado", cuenta=cuenta,
    )
    original = Decimal("0.00")
    lempiras = Decimal("0.00")
    revision = _revision_moneda_factura(factura)
    importes_documentos = {
        ("nota_credito", pk): importe for pk, importe in notas.values_list("pk", "total")
    }
    if incluir_pagos:
        importes_documentos.update({
            ("pago_factura", pago.pk): pago.total_aplicado for pago in pagos
        })
    fuente = _asiento_documento_existente(factura.empresa, "factura", factura.pk, "emision")
    exigir_revision = bool(fuente and _revision_conversion(fuente))
    if exigir_revision and factura.moneda == "USD":
        pendientes = [("nota_credito", pk, "emision") for pk in notas.filter(estado="emitida").values_list("pk", flat=True)]
        pendientes.extend(("pago_factura", pk, "cobro") for pk in pagos.values_list("pk", flat=True))
        fuentes_aplicaciones = list(AsientoContable.objects.filter(
            empresa=factura.empresa, estado="contabilizado",
            documento_tipo__in={documento[0] for documento in pendientes},
            documento_id__in={documento[1] for documento in pendientes},
            evento__in={documento[2] for documento in pendientes},
        ))
        fuentes = {(asiento.documento_tipo, asiento.documento_id, asiento.evento) for asiento in fuentes_aplicaciones}
        if any(documento not in fuentes for documento in pendientes):
            raise ValidationError("Hay un cobro o nota de crédito histórico sin asiento contabilizado. Complete su respaldo antes de aplicar un nuevo movimiento.")
        for aplicacion in fuentes_aplicaciones:
            revision_aplicacion = _revision_conversion(aplicacion)
            if not revision_aplicacion and (not aplicacion.conversion_verificada or aplicacion.moneda_origen != "USD"):
                raise ValidationError("Hay un cobro, pago o nota de crédito histórico en dólares pendiente de revisión integral. Revise sus partidas antes de registrar una nueva aplicación.")
            _validar_fecha_aplicacion_revision(revision_aplicacion, fecha_aplicacion)
    grupos = {}
    for linea in lineas.select_related("asiento"):
        grupos.setdefault(linea.asiento_id, []).append(linea)
    for partidas in grupos.values():
        asiento = partidas[0].asiento
        clave = (asiento.documento_tipo, asiento.documento_id)
        aplicado_original, aplicado_hnl = _importes_aplicacion_revisada(
            asiento, partidas, importes_documentos[clave], moneda_documento=factura.moneda,
            exigir_revision=exigir_revision, fecha_aplicacion=fecha_aplicacion,
        )
        original += aplicado_original
        lempiras += aplicado_hnl
    if revision:
        if incluir_pagos:
            lempiras += revision.aplicado_hnl_corregido - revision.aplicado_hnl_anterior
        else:
            lempiras += revision.notas_hnl_corregido - revision.notas_hnl_anterior
    return original, lempiras


def _linea_diferencia_cambiaria(diferencia):
    diferencia = redondear_importe(diferencia)
    if diferencia > 0:
        return {"cuenta": "ganancia_cambiaria", "detalle": "Ganancia por diferencia cambiaria realizada", "debe": 0, "haber": diferencia}
    if diferencia < 0:
        return {"cuenta": "perdida_cambiaria", "detalle": "Pérdida por diferencia cambiaria realizada", "debe": -diferencia, "haber": 0}
    return None


@transaction.atomic
def registrar_asiento_factura_emitida(factura):
    from facturacion.costos_inventario import valor_contable_linea_inventario
    if factura.estado != "emitida":
        return None
    type(factura.empresa).objects.select_for_update().get(pk=factura.empresa_id)
    existente = _asiento_documento_existente(factura.empresa, "factura", factura.pk, "emision")
    if existente:
        return existente
    moneda, tasa = validar_moneda_tipo_cambio(factura.moneda, factura.tipo_cambio)
    costo_ventas = sum(
        (
            valor_contable_linea_inventario(linea)
            for linea in factura.lineas.select_related("producto").all()
            if linea.producto_id and linea.producto.controla_inventario
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    cuenta_cliente = asegurar_cuenta_contable_cliente(factura.cliente)
    resumen = factura.resumen_fiscal()
    lineas = [
        {
            "cuenta": cuenta_cliente,
            "detalle": f"Cobro a cliente {factura.cliente.nombre}",
            "debe": factura.total,
            "haber": Decimal("0.00"),
        },
    ]
    for cuenta, clave, detalle in [
        ("ventas_15", "base_15", "Venta gravada 15%"),
        ("ventas_18", "base_18", "Venta gravada 18%"),
        ("ventas_exentas", "base_exento", "Venta exenta"),
        ("ventas_exoneradas", "base_exonerado", "Venta exonerada"),
        ("isv_15_por_pagar", "isv_15", "ISV trasladado 15%"),
        ("isv_18_por_pagar", "isv_18", "ISV trasladado 18%"),
    ]:
        monto = Decimal(resumen.get(clave, 0) or 0).quantize(Decimal("0.01"))
        if monto > 0:
            lineas.append({"cuenta": cuenta, "detalle": detalle, "debe": 0, "haber": monto})
    lineas = convertir_partidas_balanceadas(lineas, moneda, tasa)
    if costo_ventas > 0:
        lineas.extend([
            {"cuenta": "costo_ventas", "detalle": "Costo de mercaderia vendida", "debe": costo_ventas, "haber": 0},
            {"cuenta": "inventario", "detalle": "Salida contable de inventario", "debe": 0, "haber": costo_ventas},
        ])
    return registrar_asiento_documento(
        empresa=factura.empresa,
        documento_tipo="factura",
        documento_id=factura.id,
        evento="emision",
        fecha=factura.fecha_emision,
        descripcion=f"Emision factura {factura.numero_factura or factura.id}",
        referencia=factura.numero_factura or str(factura.id),
        origen_modulo="facturacion",
        creado_por=factura.vendedor,
        lineas=lineas,
        moneda_origen=moneda,
        tipo_cambio=tasa,
    )


@transaction.atomic
def registrar_asiento_pago_cliente(pago):
    type(pago.factura.empresa).objects.select_for_update().get(pk=pago.factura.empresa_id)
    if pago.anulado or type(pago).objects.filter(pk=pago.pk, anulado=True).exists():
        raise ValidationError("El cobro está anulado y no se puede volver a contabilizar.")
    existente = _asiento_documento_existente(pago.factura.empresa, "pago_factura", pago.pk, "cobro")
    if existente:
        return existente
    moneda, tasa_historica, cuenta_cliente, total_original, total_historico = _fuente_historica_factura(pago.factura, fecha_aplicacion=pago.fecha)
    _, tasa_cobro = validar_moneda_tipo_cambio(moneda, pago.tipo_cambio if pago.tipo_cambio is not None else tasa_historica)
    cuenta_caja = pago.cuenta_financiera.cuenta_contable if pago.cuenta_financiera_id else metodo_a_clave_cuenta(pago.metodo)
    lineas = []

    if pago.separar_isv and pago.subtotal_recibido > 0:
        lineas.append(
            {
                "cuenta": cuenta_caja,
                "detalle": "Ingreso neto sin ISV",
                "debe": pago.subtotal_recibido,
                "haber": Decimal("0.00"),
            }
        )
    elif pago.monto > 0 and not pago.separar_isv:
        lineas.append(
            {
                "cuenta": cuenta_caja,
                "detalle": "Ingreso de efectivo o banco",
                "debe": pago.monto,
                "haber": Decimal("0.00"),
            }
        )
    if pago.separar_isv and pago.impuesto_recibido > 0:
        cuenta_impuesto = (
            pago.cuenta_financiera_impuesto.cuenta_contable
            if pago.cuenta_financiera_impuesto_id
            else cuenta_caja
        )
        lineas.append(
            {
                "cuenta": cuenta_impuesto,
                "detalle": "ISV cobrado separado",
                "debe": pago.impuesto_recibido,
                "haber": Decimal("0.00"),
            }
        )
    if pago.retencion_isr > 0:
        lineas.append(
            {
                "cuenta": "isr_retenido_clientes",
                "detalle": f"ISR retenido por cliente {pago.factura.cliente.nombre}",
                "debe": pago.retencion_isr,
                "haber": Decimal("0.00"),
            }
        )
    if pago.retencion_isv > 0:
        lineas.append(
            {
                "cuenta": "isv_retenido_clientes",
                "detalle": f"ISV retenido por cliente {pago.factura.cliente.nombre}",
                "debe": pago.retencion_isv,
                "haber": Decimal("0.00"),
            }
        )

    originales_debe = [linea["debe"] for linea in lineas]
    convertidos_debe = distribuir_conversion(originales_debe, moneda, tasa_cobro)
    lineas = [
        {**linea, "debe": convertidos_debe[indice], "moneda_origen": moneda,
         "tipo_cambio": tasa_cobro, "debe_origen": redondear_importe(linea["debe"]), "haber_origen": Decimal("0.00")}
        for indice, linea in enumerate(lineas)
    ]
    compensado_hnl = convertir_a_lempiras(pago.comision_compensada, moneda, tasa_cobro)
    if pago.comision_compensada > 0:
        cuenta_proveedor = asegurar_cuenta_contable_proveedor(pago.proveedor_comision)
        lineas.append({
            "cuenta": cuenta_proveedor,
            "detalle": f"Compensación de comisión retenida por cliente a favor de {pago.proveedor_comision.nombre}",
            "debe": compensado_hnl, "haber": Decimal("0.00"),
            "moneda_origen": moneda, "tipo_cambio": tasa_cobro,
            "debe_origen": pago.comision_compensada, "haber_origen": Decimal("0.00"),
        })
    aplicado_original, aplicado_historico = _aplicaciones_historicas_factura(
        pago.factura, cuenta_cliente, pago_actual=pago.pk,
        fecha_aplicacion=pago.fecha,
    )
    cancelacion_historica = importe_historico_parcial(
        total_origen=total_original, total_lempiras=total_historico,
        aplicado_origen=aplicado_original, aplicado_lempiras=aplicado_historico,
        importe_origen=pago.total_aplicado,
    )
    lineas.append(
        {
            "cuenta": cuenta_cliente,
            "detalle": f"Aplicacion a cliente {pago.factura.cliente.nombre}",
            "debe": Decimal("0.00"),
            "haber": cancelacion_historica,
            "moneda_origen": moneda,
            "tipo_cambio": tasa_historica,
            "debe_origen": Decimal("0.00"),
            "haber_origen": pago.total_aplicado,
        }
    )
    diferencia = _linea_diferencia_cambiaria(sum(convertidos_debe, Decimal("0")) + compensado_hnl - cancelacion_historica)
    if diferencia:
        lineas.append(diferencia)

    if pago.monto_comision > 0:
        cuenta_proveedor = asegurar_cuenta_contable_proveedor(pago.proveedor_comision)
        partidas_comision = convertir_partidas_balanceadas(
            [
                {
                    "cuenta": "gasto_comisiones_ventas",
                    "detalle": f"Comision sobre cobro a favor de {pago.proveedor_comision.nombre}",
                    "debe": pago.monto_comision,
                    "haber": Decimal("0.00"),
                },
                {
                    "cuenta": cuenta_proveedor,
                    "detalle": f"Comision por pagar a {pago.proveedor_comision.nombre}",
                    "debe": Decimal("0.00"),
                    "haber": pago.monto_comision,
                },
            ], moneda, tasa_cobro,
        )
        if pago.comision_compensada > 0:
            # La compensación conserva la misma conversión que su obligación,
            # sin asignar diferencias de redondeo al dinero recibido en banco.
            partidas_comision[0]['debe'] = compensado_hnl
            partidas_comision[1]['haber'] = compensado_hnl
        lineas.extend(partidas_comision)

    return registrar_asiento_documento(
        empresa=pago.factura.empresa,
        documento_tipo="pago_factura",
        documento_id=pago.id,
        evento="cobro",
        fecha=pago.fecha,
        descripcion=f"Cobro factura {pago.factura.numero_factura or pago.factura.id}",
        referencia=pago.referencia or (pago.factura.numero_factura or str(pago.factura.id)),
        origen_modulo="facturacion",
        creado_por=pago.factura.vendedor,
        lineas=lineas,
        moneda_origen=moneda,
        tipo_cambio=tasa_cobro,
    )


@transaction.atomic
def registrar_asiento_movimiento_inventario_manual(movimiento):
    """Enlaza variaciones manuales valoradas, sin duplicar compras ni ventas."""
    if movimiento.tipo not in {"entrada", "ajuste_entrada", "ajuste_salida", "regalia"}:
        return None
    empresa = movimiento.empresa
    if not contabilidad_activa_para_empresa(empresa):
        return None
    if not movimiento.pk:
        raise ValidationError("Guarda el movimiento de inventario antes de registrar su asiento.")
    type(empresa).objects.select_for_update().get(pk=empresa.pk)
    existente = _asiento_documento_existente(empresa, "movimiento_inventario", movimiento.pk, "ajuste")
    if existente:
        return existente
    if movimiento.producto.empresa_id != empresa.pk:
        raise ValidationError("El producto y el movimiento de inventario deben pertenecer a la misma empresa.")
    if movimiento.entrada_documento_id:
        if movimiento.entrada_documento.empresa_id != empresa.pk:
            raise ValidationError("El documento de entrada debe pertenecer a la empresa del movimiento.")
        fecha = movimiento.entrada_documento.fecha_documento
    else:
        fecha = timezone.localdate(movimiento.fecha)
    from .models import validar_fecha_contable_abierta
    validar_fecha_contable_abierta(empresa.pk, fecha)
    if movimiento.valor_contable is None:
        raise ValidationError("El movimiento de inventario requiere capturar su valoración en lempiras.")
    valor = redondear_importe(movimiento.valor_contable)
    if valor < 0:
        raise ValidationError("La valoración del movimiento de inventario no puede ser negativa.")
    if not valor:
        return None
    if movimiento.tipo == "entrada":
        contrapartida = obtener_configuracion_contable(empresa).cuenta_entradas_inventario
        if not contrapartida or not contrapartida.activa or not contrapartida.acepta_movimientos or contrapartida.empresa_id != empresa.pk:
            raise ValidationError(
                "Configura la contrapartida de entradas de inventario en Contabilidad antes de aplicar esta entrada. "
                "Para adquisiciones a proveedores utiliza el módulo de Compras."
            )
    else:
        contrapartida = "ajustes_positivos_inventario" if movimiento.tipo == "ajuste_entrada" else "ajustes_negativos_inventario"
    entrada = movimiento.tipo in {"entrada", "ajuste_entrada"}
    lineas = [
        {"cuenta": "inventario", "detalle": f"Variación de inventario: {movimiento.producto.nombre}"[:200],
         "debe": valor if entrada else 0, "haber": 0 if entrada else valor},
        {"cuenta": contrapartida, "detalle": movimiento.get_tipo_display(),
         "debe": 0 if entrada else valor, "haber": valor if entrada else 0},
    ]
    return registrar_asiento_documento(
        empresa=empresa, documento_tipo="movimiento_inventario", documento_id=movimiento.pk,
        evento="ajuste", fecha=fecha,
        descripcion=f"{movimiento.get_tipo_display()}: {movimiento.producto.nombre}"[:255],
        referencia=movimiento.referencia or str(movimiento.pk), origen_modulo="inventario",
        creado_por=movimiento.usuario, lineas=lineas,
    )


@transaction.atomic
def registrar_asiento_compra_aplicada(compra):
    if compra.estado != "aplicada":
        return None
    from facturacion.costos_inventario import valorar_linea_compra
    from .models import bloquear_empresas_contables, validar_fecha_contable_abierta

    bloquear_empresas_contables([compra.empresa_id])

    existente = AsientoContable.objects.filter(
        empresa=compra.empresa, documento_tipo="compra", documento_id=compra.id, evento="aplicacion",
    ).first()
    if existente:
        return existente
    validar_fecha_contable_abierta(compra.empresa_id, compra.fecha_documento)
    total_inventario = Decimal("0.00")
    total_gasto = Decimal("0.00")
    total_isv_acreditable = Decimal("0.00")
    for linea in compra.lineas.select_related("producto", "compra", "impuesto"):
        valor = valorar_linea_compra(linea, capturar=True)
        if linea.producto.controla_inventario:
            total_inventario += valor["costo_total"]
        else:
            total_gasto += valor["costo_total"]
        total_isv_acreditable += valor["isv_acreditable"]
    lineas = []
    if total_inventario > 0:
        lineas.append({"cuenta": "inventario", "detalle": f"Inventario comprado a {compra.proveedor_nombre}", "debe": total_inventario, "haber": 0})
    if total_gasto > 0:
        lineas.append({"cuenta": "compras", "detalle": f"Compra no inventariable a {compra.proveedor_nombre}", "debe": total_gasto, "haber": 0})
    if total_isv_acreditable > 0:
        lineas.append({"cuenta": "isv_acreditable", "detalle": "ISV recuperable según la política de esta empresa", "debe": total_isv_acreditable, "haber": 0})
    cuenta_proveedor = asegurar_cuenta_contable_proveedor(compra.proveedor) if compra.proveedor_id else "proveedores"
    lineas.append({"cuenta": cuenta_proveedor, "detalle": "Obligacion con proveedor", "debe": 0, "haber": compra.total_documento})
    return registrar_asiento_documento(
        empresa=compra.empresa,
        documento_tipo="compra",
        documento_id=compra.id,
        evento="aplicacion",
        fecha=compra.fecha_documento,
        descripcion=f"Compra aplicada {compra.numero_compra or compra.id}",
        referencia=compra.numero_compra or compra.referencia_documento or str(compra.id),
        origen_modulo="compras",
        lineas=lineas,
    )


def registrar_asiento_pago_proveedor(pago):
    cuenta_caja = pago.cuenta_financiera.cuenta_contable if pago.cuenta_financiera_id else metodo_a_clave_cuenta(pago.metodo)
    cuenta_proveedor = asegurar_cuenta_contable_proveedor(pago.compra.proveedor) if pago.compra.proveedor_id else "proveedores"
    return registrar_asiento_documento(
        empresa=pago.compra.empresa,
        documento_tipo="pago_compra",
        documento_id=pago.id,
        evento="egreso",
        fecha=pago.fecha,
        descripcion=f"Pago compra {pago.compra.numero_compra or pago.compra.id}",
        referencia=pago.referencia or (pago.compra.numero_compra or str(pago.compra.id)),
        origen_modulo="compras",
        lineas=[
            {
                "cuenta": cuenta_proveedor,
                "detalle": f"Pago a proveedor {pago.compra.proveedor_nombre}",
                "debe": pago.monto,
                "haber": Decimal("0.00"),
            },
            {
                "cuenta": cuenta_caja,
                "detalle": "Salida de caja o banco",
                "debe": Decimal("0.00"),
                "haber": pago.monto,
            },
        ],
    )


@transaction.atomic
def registrar_asiento_pago_comision_proveedor(pago):
    type(pago.empresa).objects.select_for_update().get(pk=pago.empresa.pk)
    if (pago.comision_origen.anulado
            or type(pago.comision_origen).objects.filter(pk=pago.comision_origen_id, anulado=True).exists()):
        raise ValidationError("La comisión pertenece a un cobro anulado y no se puede contabilizar su pago.")
    if pago.metodo == 'compensacion':
        pago.full_clean()
        return registrar_asiento_pago_cliente(pago.comision_origen)
    existente = _asiento_documento_existente(pago.empresa, "pago_comision_proveedor", pago.pk, "egreso")
    if existente:
        return existente
    cuenta_salida = pago.cuenta_financiera.cuenta_contable
    cuenta_proveedor = asegurar_cuenta_contable_proveedor(pago.proveedor)
    origen = pago.comision_origen
    if origen.factura.empresa_id != pago.empresa.pk or pago.proveedor.empresa_id != pago.empresa.pk or cuenta_salida.empresa_id != pago.empresa.pk:
        raise ValidationError("La comisión, el proveedor y la cuenta financiera deben pertenecer a esta empresa.")
    asiento_origen = _asiento_documento_existente(pago.empresa, "pago_factura", origen.pk, "cobro")
    if not asiento_origen:
        asiento_origen = registrar_asiento_pago_cliente(origen)
    if asiento_origen.estado != "contabilizado":
        raise ValidationError("Primero contabiliza el cobro que genera la comisión antes de pagarla.")
    lineas_origen = list(asiento_origen.lineas.filter(detalle__startswith="Comision por pagar", haber__gt=0).select_related("cuenta"))
    if not lineas_origen:
        lineas_origen = list(asiento_origen.lineas.filter(cuenta=cuenta_proveedor, haber__gt=0).select_related("cuenta"))
    if not lineas_origen:
        raise ValidationError("No se encontró la obligación contable que respalda la comisión.")
    if len({linea.cuenta_id for linea in lineas_origen}) != 1 or any(linea.cuenta.empresa_id != pago.empresa.pk or linea.cuenta.tipo != "pasivo" for linea in lineas_origen):
        raise ValidationError("La obligación de comisión requiere una sola cuenta por pagar de esta empresa.")
    cuenta_proveedor = lineas_origen[0].cuenta
    revisada = _fuente_documento_revisada(asiento_origen, lineas_origen, origen.monto_comision, lado="haber", nombre="la comisión por pagar", fecha_aplicacion=pago.fecha)
    if revisada:
        moneda, tasa_historica, cuenta_proveedor, total_original, total_historico = revisada
    else:
        if not asiento_origen.conversion_verificada or (origen.factura.moneda == "USD" and asiento_origen.moneda_origen != "USD"):
            raise ValidationError("La comisión en dólares requiere una revisión integral de su conversión histórica antes de pagarla.")
        tasas = {validar_moneda_tipo_cambio(linea.moneda_origen, linea.tipo_cambio) for linea in lineas_origen}
        if len(tasas) != 1:
            raise ValidationError("La obligación de comisión debe conservar una sola moneda y un solo tipo de cambio.")
        moneda, tasa_historica = tasas.pop()
        if moneda == "USD" and any(linea.haber_origen <= 0 for linea in lineas_origen):
            raise ValidationError("La obligación en dólares no conserva el importe original de la comisión; requiere revisión.")
        total_original = sum((linea.haber_origen or linea.haber for linea in lineas_origen), Decimal("0"))
        total_historico = sum((linea.haber for linea in lineas_origen), Decimal("0"))
        if redondear_importe(total_original) != redondear_importe(origen.monto_comision):
            raise ValidationError("La obligación contable de comisión no coincide con su documento original.")
    _, tasa_pago = validar_moneda_tipo_cambio(moneda, pago.tipo_cambio if pago.tipo_cambio is not None else tasa_historica)
    pagos_anteriores = dict(origen.pagos_comision_proveedor.exclude(pk=pago.pk).values_list("pk", "monto"))
    if moneda == "USD":
        respaldados = set(AsientoContable.objects.filter(
            empresa=pago.empresa, documento_tipo="pago_comision_proveedor", documento_id__in=pagos_anteriores,
            evento="egreso", estado="contabilizado",
        ).values_list("documento_id", flat=True))
        if set(pagos_anteriores) - respaldados:
            raise ValidationError("Hay un pago histórico de comisión sin asiento contabilizado. Complete su respaldo antes de pagar el saldo restante.")
    lineas_anteriores = LineaAsientoContable.objects.filter(
        asiento__empresa=pago.empresa, asiento__documento_tipo="pago_comision_proveedor",
        asiento__documento_id__in=pagos_anteriores, asiento__estado="contabilizado", cuenta=cuenta_proveedor,
    ).select_related("asiento")
    grupos = {}
    for linea in lineas_anteriores:
        grupos.setdefault(linea.asiento_id, []).append(linea)
    aplicado_original = Decimal("0")
    aplicado_historico = Decimal("0")
    for partidas in grupos.values():
        asiento_anterior = partidas[0].asiento
        importe_original, importe_hnl = _importes_aplicacion_revisada(
            asiento_anterior, partidas, pagos_anteriores[asiento_anterior.documento_id],
            moneda_documento=moneda, debe_es_aplicacion=True, exigir_revision=moneda == "USD", fecha_aplicacion=pago.fecha,
        )
        aplicado_original += importe_original
        aplicado_historico += importe_hnl
    cancelacion = importe_historico_parcial(
        total_origen=total_original, total_lempiras=total_historico,
        aplicado_origen=aplicado_original, aplicado_lempiras=aplicado_historico,
        importe_origen=pago.monto,
    )
    salida = convertir_a_lempiras(pago.monto, moneda, tasa_pago)
    lineas = [
        {"cuenta": cuenta_proveedor, "detalle": f"Cancelación de comisión por pagar a {pago.proveedor.nombre}",
         "debe": cancelacion, "haber": 0, "moneda_origen": moneda, "tipo_cambio": tasa_historica,
         "debe_origen": pago.monto, "haber_origen": 0},
        {"cuenta": cuenta_salida, "detalle": "Salida de caja o banco por pago de comisión",
         "debe": 0, "haber": salida, "moneda_origen": moneda, "tipo_cambio": tasa_pago,
         "debe_origen": 0, "haber_origen": pago.monto},
    ]
    diferencia = _linea_diferencia_cambiaria(cancelacion - salida)
    if diferencia:
        lineas.append(diferencia)
    return registrar_asiento_documento(
        empresa=pago.empresa,
        documento_tipo="pago_comision_proveedor",
        documento_id=pago.id,
        evento="egreso",
        fecha=pago.fecha,
        descripcion=f"Pago de comision a {pago.proveedor.nombre}",
        referencia=pago.referencia or f"COMISION-{pago.comision_origen_id}",
        origen_modulo="cuentas_por_pagar",
        creado_por=pago.registrado_por,
        lineas=lineas,
        moneda_origen=moneda,
        tipo_cambio=tasa_pago,
    )


@transaction.atomic
def registrar_asiento_nota_credito(nota):
    if nota.estado != "emitida":
        return None
    type(nota.empresa).objects.select_for_update().get(pk=nota.empresa_id)
    existente = _asiento_documento_existente(nota.empresa, "nota_credito", nota.pk, "emision")
    if existente:
        return existente
    from facturacion.costos_inventario import capturar_costos_devolucion, validar_devoluciones_nota, valor_contable_linea_inventario
    validar_devoluciones_nota(nota)
    if nota.factura_origen_id:
        moneda, tasa, cuenta_cliente, total_original, total_historico = _fuente_historica_factura(nota.factura_origen, fecha_aplicacion=nota.fecha_emision)
        aplicado_original, aplicado_historico = _aplicaciones_historicas_factura(
            nota.factura_origen, cuenta_cliente, nota_actual=nota.pk, incluir_pagos=False,
            fecha_aplicacion=nota.fecha_emision,
        )
        cancelacion_historica = importe_historico_parcial(
            total_origen=total_original, total_lempiras=total_historico,
            aplicado_origen=aplicado_original, aplicado_lempiras=aplicado_historico,
            importe_origen=nota.total,
        )
        if nota.moneda != moneda:
            raise ValidationError("La nota de crédito debe usar la moneda original de la factura.")
    else:
        moneda, tasa = validar_moneda_tipo_cambio(nota.moneda, nota.tipo_cambio)
        cuenta_cliente = asegurar_cuenta_contable_cliente(nota.cliente)
        cancelacion_historica = convertir_a_lempiras(nota.total, moneda, tasa)
    capturar_costos_devolucion(nota)
    costo_devuelto = sum(
        (
            valor_contable_linea_inventario(linea)
            for linea in nota.lineas.select_related("producto").all()
            if linea.producto_id and linea.producto.controla_inventario
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    resumen = nota.resumen_fiscal()
    lineas = [
        {"cuenta": "devoluciones_ventas", "detalle": f"Devolucion cliente {nota.cliente.nombre}", "debe": nota.subtotal, "haber": 0},
        {"cuenta": cuenta_cliente, "detalle": "Disminucion de cuenta por cobrar", "debe": 0, "haber": nota.total},
    ]
    for cuenta, clave, detalle in [
        ("isv_15_por_pagar", "isv_15", "Reversion ISV 15%"),
        ("isv_18_por_pagar", "isv_18", "Reversion ISV 18%"),
    ]:
        monto = Decimal(resumen.get(clave, 0) or 0).quantize(Decimal("0.01"))
        if monto > 0:
            lineas.append({"cuenta": cuenta, "detalle": detalle, "debe": monto, "haber": 0})
    lineas = convertir_partidas_balanceadas(lineas, moneda, tasa, total_lempiras=cancelacion_historica)
    if costo_devuelto > 0:
        lineas.extend([
            {"cuenta": "inventario", "detalle": "Reintegro contable de inventario", "debe": costo_devuelto, "haber": 0},
            {"cuenta": "costo_ventas", "detalle": "Reversion del costo de venta", "debe": 0, "haber": costo_devuelto},
        ])
    return registrar_asiento_documento(
        empresa=nota.empresa,
        documento_tipo="nota_credito",
        documento_id=nota.id,
        evento="emision",
        fecha=nota.fecha_emision,
        descripcion=f"Nota de credito {nota.numero_nota or nota.id}",
        referencia=nota.numero_nota or str(nota.id),
        origen_modulo="facturacion",
        creado_por=nota.vendedor,
        lineas=lineas,
        moneda_origen=moneda,
        tipo_cambio=tasa,
    )


def registrar_asiento_planilla_cerrada(periodo):
    detalles = list(periodo.detalles.all())
    total_devengado = sum((Decimal(item.total_devengado or 0) for item in detalles), Decimal("0.00"))
    total_neto = sum((Decimal(item.neto_pagar or 0) for item in detalles), Decimal("0.00"))
    conceptos = [
        ("ihss_por_pagar", sum((Decimal(item.ihss or 0) for item in detalles), Decimal("0.00")), "IHSS retenido"),
        ("rap_por_pagar", sum((Decimal(item.rap or 0) for item in detalles), Decimal("0.00")), "RAP retenido"),
        ("isr_por_pagar", sum((Decimal(item.isr or 0) for item in detalles), Decimal("0.00")), "ISR retenido"),
        ("anticipos_empleados", sum((Decimal(item.prestamos or 0) for item in detalles), Decimal("0.00")), "Recuperacion de prestamos"),
        ("otras_deducciones_por_pagar", sum((Decimal(item.otras_deducciones or 0) for item in detalles), Decimal("0.00")), "Otras deducciones"),
        ("sueldos_por_pagar", total_neto, "Neto por pagar a empleados"),
    ]
    lineas = [{"cuenta": "gasto_salarios", "detalle": "Devengo de nomina", "debe": total_devengado, "haber": 0}]
    lineas.extend(
        {"cuenta": cuenta, "detalle": detalle, "debe": 0, "haber": monto}
        for cuenta, monto, detalle in conceptos if monto > 0
    )
    return registrar_asiento_documento(
        empresa=periodo.empresa, documento_tipo="planilla", documento_id=periodo.id,
        evento="cierre", fecha=periodo.fecha_fin, descripcion=f"Devengo planilla {periodo.nombre}",
        referencia=periodo.nombre, origen_modulo="rrhh", creado_por=periodo.creado_por, lineas=lineas,
        actualizar_borrador=True,
    )


def registrar_asiento_planilla_pagada(periodo, usuario=None):
    cuenta = periodo.cuenta_financiera_pago.cuenta_contable
    total_neto = Decimal(periodo.total_neto or 0).quantize(Decimal("0.01"))
    return registrar_asiento_documento(
        empresa=periodo.empresa, documento_tipo="planilla", documento_id=periodo.id,
        evento="pago", fecha=periodo.fecha_pago, descripcion=f"Pago planilla {periodo.nombre}",
        referencia=periodo.nombre, origen_modulo="rrhh", creado_por=usuario or periodo.creado_por,
        lineas=[
            {"cuenta": "sueldos_por_pagar", "detalle": "Cancelacion de nomina", "debe": total_neto, "haber": 0},
            {"cuenta": cuenta, "detalle": "Salida de caja o banco por nomina", "debe": 0, "haber": total_neto},
        ],
    )


@transaction.atomic
def registrar_reversion_documento(*, empresa, documento_tipo, documento_id, evento_origen, evento_reversion, fecha, descripcion, referencia, origen_modulo, creado_por=None):
    from .models import bloquear_empresas_contables
    from .proteccion_conversion import asiento_tiene_revision

    bloquear_empresas_contables([empresa.pk])
    asiento_origen = AsientoContable.objects.filter(
        empresa=empresa,
        documento_tipo=documento_tipo,
        documento_id=documento_id,
        evento=evento_origen,
        estado="contabilizado",
    ).prefetch_related("lineas").first()
    if not asiento_origen:
        return None
    if asiento_tiene_revision(asiento_origen.pk):
        raise ValidationError("El documento tiene una revisión integral documentada. Registre un nuevo ajuste para conservar su respaldo.")
    if documento_tipo == "factura":
        from django.db.models import Q
        from facturacion.models import NotaCredito, PagoFactura

        aplicaciones_revisadas = AsientoContable.objects.filter(empresa=empresa, revision_conversion__isnull=False).filter(
            Q(documento_tipo="pago_factura", documento_id__in=PagoFactura.objects.filter(factura_id=documento_id).values("pk"))
            | Q(documento_tipo="nota_credito", documento_id__in=NotaCredito.objects.filter(factura_origen_id=documento_id).values("pk"))
        )
        if aplicaciones_revisadas.exists():
            raise ValidationError("El documento tiene una revisión integral documentada. Registre un nuevo ajuste para conservar su respaldo.")

    lineas = []
    for linea in asiento_origen.lineas.all():
        lineas.append({
            "cuenta": linea.cuenta,
            "detalle": f"Reversion {linea.detalle or asiento_origen.descripcion}",
            "debe": linea.haber,
            "haber": linea.debe,
            "moneda_origen": linea.moneda_origen,
            "tipo_cambio": linea.tipo_cambio,
            "debe_origen": linea.haber_origen or (linea.haber if linea.moneda_origen == "HNL" else 0),
            "haber_origen": linea.debe_origen or (linea.debe if linea.moneda_origen == "HNL" else 0),
        })

    return registrar_asiento_documento(
        empresa=empresa,
        documento_tipo=documento_tipo,
        documento_id=documento_id,
        evento=evento_reversion,
        fecha=fecha,
        descripcion=descripcion,
        referencia=referencia,
        origen_modulo=origen_modulo,
        creado_por=creado_por,
        lineas=lineas,
        moneda_origen=asiento_origen.moneda_origen,
        tipo_cambio=asiento_origen.tipo_cambio,
        conversion_verificada=asiento_origen.conversion_verificada,
    )
