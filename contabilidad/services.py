from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import AsientoContable, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, ReglaClasificacionBanco


CUENTAS_BASE = {
    "caja": {"codigo": "1101", "nombre": "Caja General", "tipo": "activo"},
    "bancos": {"codigo": "110201", "nombre": "Banco Moneda Nacional", "tipo": "activo"},
    "clientes": {"codigo": "111001", "nombre": "Clientes Nacionales", "tipo": "activo"},
    "isr_retenido_clientes": {"codigo": "113003", "nombre": "ISR Retenido por Clientes", "tipo": "activo"},
    "isv_retenido_clientes": {"codigo": "113005", "nombre": "ISV Retenido por Clientes", "tipo": "activo"},
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
    "sueldos_por_pagar": {"codigo": "210301", "nombre": "Sueldos por Pagar", "tipo": "pasivo"},
    "ihss_por_pagar": {"codigo": "210302", "nombre": "IHSS por Pagar", "tipo": "pasivo"},
    "rap_por_pagar": {"codigo": "210303", "nombre": "RAP por Pagar", "tipo": "pasivo"},
    "isr_por_pagar": {"codigo": "210203", "nombre": "ISR por Pagar", "tipo": "pasivo"},
    "otras_deducciones_por_pagar": {"codigo": "210304", "nombre": "Otras Deducciones por Pagar", "tipo": "pasivo"},
    "anticipos_empleados": {"codigo": "114002", "nombre": "Anticipos a Empleados", "tipo": "activo"},
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


def asegurar_cuentas_financieras_base_honduras(empresa):
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


def asegurar_clasificaciones_bancarias_base_honduras(empresa):
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


def obtener_o_crear_cuenta_base(empresa, clave):
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


def cargar_catalogo_base_honduras(empresa):
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




def asegurar_cuenta_contable_cliente(cliente):
    if not cliente or not getattr(cliente, "empresa_id", None):
        return None
    if getattr(cliente, "cuenta_contable_id", None):
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


def asegurar_cuenta_contable_proveedor(proveedor):
    if not proveedor or not getattr(proveedor, "empresa_id", None):
        return None
    if getattr(proveedor, "cuenta_contable_id", None):
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


def obtener_cuenta_operativa(empresa, clave):
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
):
    if not lineas:
        return None

    asiento_existente = AsientoContable.objects.filter(
        empresa=empresa,
        documento_tipo=documento_tipo,
        documento_id=documento_id,
        evento=evento,
    ).first()
    if asiento_existente:
        return asiento_existente

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


def registrar_asiento_factura_emitida(factura):
    if factura.estado != "emitida":
        return None
    costo_ventas = sum(
        (
            Decimal(linea.costo_unitario or 0) * Decimal(linea.cantidad or 0)
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
    )


def registrar_asiento_pago_cliente(pago):
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

    cuenta_cliente = asegurar_cuenta_contable_cliente(pago.factura.cliente)
    lineas.append(
        {
            "cuenta": cuenta_cliente,
            "detalle": f"Aplicacion a cliente {pago.factura.cliente.nombre}",
            "debe": Decimal("0.00"),
            "haber": pago.total_aplicado,
        }
    )

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
    )


def registrar_asiento_compra_aplicada(compra):
    if compra.estado != "aplicada":
        return None
    total_inventario = sum(
        (Decimal(linea.total_linea) for linea in compra.lineas.select_related("producto") if linea.producto.controla_inventario),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    total_gasto = (Decimal(compra.total_documento) - total_inventario).quantize(Decimal("0.01"))
    lineas = []
    if total_inventario > 0:
        lineas.append({"cuenta": "inventario", "detalle": f"Inventario comprado a {compra.proveedor_nombre}", "debe": total_inventario, "haber": 0})
    if total_gasto > 0:
        lineas.append({"cuenta": "compras", "detalle": f"Compra no inventariable a {compra.proveedor_nombre}", "debe": total_gasto, "haber": 0})
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


def registrar_asiento_nota_credito(nota):
    if nota.estado != "emitida":
        return None
    costo_devuelto = sum(
        (
            Decimal(linea.costo_unitario or 0) * Decimal(linea.cantidad or 0)
            for linea in nota.lineas.select_related("producto").all()
            if linea.producto_id and linea.producto.controla_inventario
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    cuenta_cliente = asegurar_cuenta_contable_cliente(nota.cliente)
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


def registrar_reversion_documento(*, empresa, documento_tipo, documento_id, evento_origen, evento_reversion, fecha, descripcion, referencia, origen_modulo, creado_por=None):
    asiento_origen = AsientoContable.objects.filter(
        empresa=empresa,
        documento_tipo=documento_tipo,
        documento_id=documento_id,
        evento=evento_origen,
        estado="contabilizado",
    ).prefetch_related("lineas").first()
    if not asiento_origen:
        return None

    lineas = []
    for linea in asiento_origen.lineas.all():
        lineas.append({
            "cuenta": linea.cuenta,
            "detalle": f"Reversion {linea.detalle or asiento_origen.descripcion}",
            "debe": linea.haber,
            "haber": linea.debe,
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
    )
