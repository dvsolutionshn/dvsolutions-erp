from decimal import Decimal

from django.db import transaction

from .models import AsientoContable, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario, ReglaClasificacionBanco


CUENTAS_BASE = {
    "caja": {"codigo": "1101", "nombre": "Caja General", "tipo": "activo"},
    "bancos": {"codigo": "1102", "nombre": "Bancos", "tipo": "activo"},
    "clientes": {"codigo": "1130", "nombre": "Cuentas por Cobrar Clientes", "tipo": "activo"},
    "isr_retenido_clientes": {"codigo": "113003", "nombre": "ISR Retenido por Clientes", "tipo": "activo"},
    "isv_retenido_clientes": {"codigo": "113005", "nombre": "ISV Retenido por Clientes", "tipo": "activo"},
    "inventario": {"codigo": "1140", "nombre": "Inventario de Mercaderia", "tipo": "activo"},
    "isv_por_pagar": {"codigo": "2101", "nombre": "ISV por Pagar", "tipo": "pasivo"},
    "proveedores": {"codigo": "2102", "nombre": "Cuentas por Pagar Proveedores", "tipo": "pasivo"},
    "ventas": {"codigo": "4101", "nombre": "Ventas", "tipo": "ingreso"},
    "devoluciones_ventas": {"codigo": "5101", "nombre": "Devoluciones sobre Ventas", "tipo": "gasto"},
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
            if not cuenta_financiera.activa:
                cuenta_financiera.activa = True
                cuenta_financiera.save(update_fields=["activa"])

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


def obtener_configuracion_contable(empresa):
    configuracion, _ = ConfiguracionContableEmpresa.objects.get_or_create(empresa=empresa)
    return configuracion


def obtener_cuenta_operativa(empresa, clave):
    campo = f"cuenta_{clave}"
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
    if not contabilidad_activa_para_empresa(empresa):
        return None
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

        for linea in lineas:
            cuenta = linea["cuenta"]
            if isinstance(cuenta, str):
                cuenta = obtener_cuenta_operativa(empresa, cuenta)
            LineaAsientoContable.objects.create(
                asiento=asiento,
                cuenta=cuenta,
                detalle=linea.get("detalle"),
                debe=linea.get("debe", Decimal("0.00")),
                haber=linea.get("haber", Decimal("0.00")),
            )

        if asiento.esta_balanceado:
            asiento.generar_numero()
            asiento.estado = "contabilizado"
            asiento.save(update_fields=["numero", "estado"])
        return asiento


def registrar_asiento_factura_emitida(factura):
    if factura.estado != "emitida":
        return None
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
        lineas=[
            {
                "cuenta": "clientes",
                "detalle": f"Cobro a cliente {factura.cliente.nombre}",
                "debe": factura.total,
                "haber": Decimal("0.00"),
            },
            {
                "cuenta": "ventas",
                "detalle": "Venta neta",
                "debe": Decimal("0.00"),
                "haber": factura.subtotal,
            },
            {
                "cuenta": "isv_por_pagar",
                "detalle": "Impuesto trasladado",
                "debe": Decimal("0.00"),
                "haber": factura.impuesto,
            },
        ],
    )


def registrar_asiento_pago_cliente(pago):
    cuenta_caja = pago.cuenta_financiera.cuenta_contable if pago.cuenta_financiera_id else metodo_a_clave_cuenta(pago.metodo)
    lineas = []

    if pago.monto > 0:
        lineas.append(
            {
                "cuenta": cuenta_caja,
                "detalle": "Ingreso de efectivo o banco",
                "debe": pago.monto,
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

    lineas.append(
        {
            "cuenta": "clientes",
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
    return registrar_asiento_documento(
        empresa=compra.empresa,
        documento_tipo="compra",
        documento_id=compra.id,
        evento="aplicacion",
        fecha=compra.fecha_documento,
        descripcion=f"Compra aplicada {compra.numero_compra or compra.id}",
        referencia=compra.numero_compra or compra.referencia_documento or str(compra.id),
        origen_modulo="compras",
        lineas=[
            {
                "cuenta": "inventario",
                "detalle": f"Compra a proveedor {compra.proveedor_nombre}",
                "debe": compra.total_documento,
                "haber": Decimal("0.00"),
            },
            {
                "cuenta": "proveedores",
                "detalle": "Obligacion con proveedor",
                "debe": Decimal("0.00"),
                "haber": compra.total_documento,
            },
        ],
    )


def registrar_asiento_pago_proveedor(pago):
    cuenta_caja = pago.cuenta_financiera.cuenta_contable if pago.cuenta_financiera_id else metodo_a_clave_cuenta(pago.metodo)
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
                "cuenta": "proveedores",
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
        lineas=[
            {
                "cuenta": "devoluciones_ventas",
                "detalle": f"Devolucion cliente {nota.cliente.nombre}",
                "debe": nota.subtotal,
                "haber": Decimal("0.00"),
            },
            {
                "cuenta": "isv_por_pagar",
                "detalle": "Reversion de impuesto",
                "debe": nota.impuesto,
                "haber": Decimal("0.00"),
            },
            {
                "cuenta": "clientes",
                "detalle": "Disminucion de cuenta por cobrar",
                "debe": Decimal("0.00"),
                "haber": nota.total,
            },
        ],
    )


def registrar_reversion_documento(*, empresa, documento_tipo, documento_id, evento_origen, evento_reversion, fecha, descripcion, referencia, origen_modulo, creado_por=None):
    if not contabilidad_activa_para_empresa(empresa):
        return None
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
