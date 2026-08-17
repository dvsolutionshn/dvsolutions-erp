from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from clinica.models import (
    CitaClinica,
    ConfiguracionClinica,
    ExpedienteEvento,
    Paciente,
    ProfesionalSalud,
    ServicioClinico,
    TratamientoPaciente,
)
from contabilidad.models import (
    AsientoContable,
    ConfiguracionContableEmpresa,
    CuentaContable,
    CuentaFinanciera,
    LineaAsientoContable,
    MovimientoBancario,
    PeriodoContable,
)
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo
from crm.models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, PlantillaMensaje
from facturacion.models import (
    BodegaInventario,
    CAI,
    Cliente,
    CompraInventario,
    ConfiguracionFacturacionEmpresa,
    Cotizacion,
    Factura,
    InventarioProducto,
    LineaCompraInventario,
    LineaCotizacion,
    LineaFactura,
    PagoFactura,
    Producto,
    Proveedor,
    TipoImpuesto,
)
from rrhh.models import ConfiguracionRRHHEmpresa, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado
from rrhh.services import generar_planilla
from tecnicentro.models import (
    BahiaServicio,
    CitaTaller,
    ConfiguracionTecnicentro,
    CotizacionTaller,
    DiagnosticoVehicular,
    InspeccionRecepcion,
    LineaCotizacionTaller,
    OrdenServicio,
    Vehiculo,
)


DEMO_SLUG = "demo_1"
D = Decimal


def fecha_meses_atras(fecha, meses, dia=15):
    indice = fecha.year * 12 + fecha.month - 1 - meses
    anio, mes_cero = divmod(indice, 12)
    mes = mes_cero + 1
    return date(anio, mes, min(dia, monthrange(anio, mes)[1]))


class Command(BaseCommand):
    help = "Carga datos ficticios integrales exclusivamente en la empresa demo_1."

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa",
            default=DEMO_SLUG,
            help="Proteccion explicita: solamente se acepta demo_1.",
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Ejecuta todas las validaciones y revierte la transaccion al final.",
        )

    def handle(self, *args, **options):
        if options["empresa"] != DEMO_SLUG:
            raise CommandError("Operacion rechazada: este comando solo puede trabajar con demo_1.")

        try:
            empresa = Empresa.objects.get(slug=DEMO_SLUG)
        except Empresa.DoesNotExist as exc:
            raise CommandError("No existe la empresa demo_1; no se modifico ninguna empresa.") from exc

        self.empresa = empresa
        self.hoy = timezone.localdate()
        self.usuario = empresa.usuario_set.filter(es_administrador_empresa=True, is_active=True).first()
        self.contadores = {}

        with transaction.atomic():
            self._configurar_empresa()
            self._crear_catalogos()
            self._crear_facturacion_e_inventario()
            self._crear_contabilidad()
            self._crear_cobros_demo()
            self._crear_rrhh()
            self._crear_crm()
            self._crear_clinica()
            self._crear_tecnicentro()

            if options["simular"]:
                transaction.set_rollback(True)

        modo = "SIMULACION REVERSADA" if options["simular"] else "CARGA COMPLETADA"
        self.stdout.write(self.style.SUCCESS(f"{modo}: solo se proceso {DEMO_SLUG}."))
        for nombre, total in sorted(self.contadores.items()):
            self.stdout.write(f"  {nombre}: {total}")

    def _contar(self, nombre, cantidad=1):
        self.contadores[nombre] = self.contadores.get(nombre, 0) + cantidad

    def _configurar_empresa(self):
        e = self.empresa
        cambios = {
            "nombre": "DV Solutions - Empresa Demo Integral",
            "direccion": "Boulevard Centroamerica, edificio Demo, Tegucigalpa",
            "ciudad": "Tegucigalpa",
            "departamento": "Francisco Morazan",
            "pais": "Honduras",
            "telefono": "+504 2234-5678",
            "correo": "demo@dvsolutions.hn",
            "sitio_web": "www.dvsolutions.hn",
            "slogan": "Tecnologia que impulsa tu empresa",
            "condiciones_pago": "Contado y credito a 30 dias",
            "estado_licencia": "activa",
            "activa": True,
            "fecha_vencimiento_plan": self.hoy + timedelta(days=730),
        }
        for campo, valor in cambios.items():
            setattr(e, campo, valor)
        e.save(update_fields=list(cambios))

        for modulo in Modulo.objects.all():
            EmpresaModulo.objects.update_or_create(empresa=e, modulo=modulo, defaults={"activo": True})
        self._contar("modulos activos", Modulo.objects.count())

        ConfiguracionFacturacionEmpresa.objects.update_or_create(
            empresa=e,
            defaults={
                "plantilla_factura_pdf": "normal",
                "nombre_comercial_documentos": "DV Solutions Demo Integral",
                "color_primario": "#0f4c81",
                "color_secundario": "#22c55e",
                "mostrar_vendedor": True,
                "mostrar_descuentos": True,
                "leyenda_factura": "Documento ficticio para demostracion del sistema.",
                "pie_factura": "Gracias por su preferencia. Datos exclusivamente demostrativos.",
            },
        )
        ConfiguracionAvanzadaEmpresa.objects.update_or_create(
            empresa=e,
            defaults={
                "usa_cierre_caja": True,
                "usa_pagos_mixtos": True,
                "usa_reporte_bancos": True,
                "usa_inventario_farmaceutico": True,
                "usa_bodegas_internas": True,
                "permite_cai_historico": True,
                "permite_plantilla_factura_independiente": True,
                "bodega_venta_predeterminada": "Vitrina Demo",
                "notas": "Configuracion integral ficticia para demostraciones.",
            },
        )
        ConfiguracionRRHHEmpresa.objects.get_or_create(empresa=e)
        ConfiguracionCRM.objects.get_or_create(empresa=e)
        ConfiguracionClinica.objects.update_or_create(
            empresa=e,
            defaults={"nombre_comercial": "Centro Medico Demo", "especialidad_principal": "Medicina general"},
        )
        ConfiguracionTecnicentro.objects.update_or_create(
            empresa=e,
            defaults={"nombre_comercial": "AutoServicio Demo", "notificar_whatsapp": False},
        )

    def _crear_catalogos(self):
        e = self.empresa
        self.isv = TipoImpuesto.objects.filter(porcentaje=D("15.00"), activo=True).order_by("id").first()
        self.exento = TipoImpuesto.objects.filter(porcentaje=D("0.00"), activo=True).order_by("id").first()
        if not self.isv or not self.exento:
            raise CommandError("Faltan los tipos de impuesto globales ISV 15% o Exento; no se modifico ningun dato.")

        clientes = [
            ("Comercial La Ceiba Demo", "08019000100001", "compras@laceibademo.hn", "99880011", "La Ceiba"),
            ("Inversiones Copan Demo", "04019000100002", "admin@copandemo.hn", "99880022", "Santa Rosa de Copan"),
            ("Grupo Empresarial Sula Demo", "05019000100003", "finanzas@sulademo.hn", "99880033", "San Pedro Sula"),
            ("Maria Fernanda Demo", "0801199010004", "maria.demo@example.com", "99880044", "Tegucigalpa"),
            ("Ferreteria El Roble Demo", "08019000100005", "ventas@robledemo.hn", "99880055", "Comayagua"),
            ("Consumidor Final Demo", None, None, "99880066", "Tegucigalpa"),
        ]
        self.clientes = []
        for nombre, rtn, correo, telefono, ciudad in clientes:
            obj, _ = Cliente.objects.update_or_create(
                empresa=e,
                nombre=nombre,
                defaults={
                    "rtn": rtn,
                    "correo": correo,
                    "telefono": telefono,
                    "telefono_whatsapp": telefono,
                    "ciudad": ciudad,
                    "direccion": f"Direccion ficticia, {ciudad}",
                    "acepta_promociones": True,
                    "activo": True,
                },
            )
            self.clientes.append(obj)
        self._contar("clientes demo", len(self.clientes))

        productos = [
            ("DM-P001", "Laptop Empresarial Demo", "producto", "unidad", "24500.00", "17800.00", self.isv),
            ("DM-P002", "Impresora Termica Demo", "producto", "unidad", "6200.00", "4100.00", self.isv),
            ("DM-P003", "Lector Codigo de Barras Demo", "producto", "unidad", "2850.00", "1850.00", self.isv),
            ("DM-P004", "Router Empresarial Demo", "producto", "unidad", "4800.00", "3100.00", self.isv),
            ("DM-P005", "Resma Papel Carta Demo", "producto", "paquete", "165.00", "112.00", self.isv),
            ("DM-S001", "Implementacion ERP Demo", "servicio", "servicio", "18500.00", "0.00", self.isv),
            ("DM-S002", "Soporte Tecnico Mensual Demo", "servicio", "mes", "4500.00", "0.00", self.isv),
            ("DM-S003", "Capacitacion de Usuarios Demo", "servicio", "hora", "950.00", "0.00", self.isv),
            ("DM-S004", "Diagnostico Vehicular Demo", "servicio", "servicio", "750.00", "0.00", self.isv),
            ("DM-S005", "Consulta Medica General Demo", "servicio", "servicio", "900.00", "0.00", self.exento),
        ]
        self.productos = []
        for codigo, nombre, tipo, unidad, precio, costo, impuesto in productos:
            obj, _ = Producto.objects.update_or_create(
                empresa=e,
                codigo=codigo,
                defaults={
                    "nombre": nombre,
                    "tipo_item": tipo,
                    "unidad_medida": unidad,
                    "descripcion": "Registro ficticio preparado para demostracion.",
                    "precio": D(precio),
                    "costo_promedio": D(costo),
                    "costo_real_inventario": D(costo),
                    "controla_inventario": tipo == "producto",
                    "impuesto_predeterminado": impuesto,
                    "activo": True,
                    "eliminado": False,
                },
            )
            self.productos.append(obj)
            if tipo == "producto":
                InventarioProducto.objects.update_or_create(
                    empresa=e,
                    producto=obj,
                    defaults={"existencias": D("40.00") + D(str(len(self.productos) * 7)), "stock_minimo": D("10.00")},
                )
        self._contar("productos y servicios demo", len(self.productos))

        self.proveedores = []
        for nombre, rtn, condicion in [
            ("Tecnologia Mayorista Demo", "05019000200001", "credito"),
            ("Suministros Nacionales Demo", "08019000200002", "contado"),
            ("Servicios Logisticos Demo", "08019000200003", "credito"),
        ]:
            obj, _ = Proveedor.objects.update_or_create(
                empresa=e,
                nombre=nombre,
                defaults={
                    "rtn": rtn,
                    "contacto": "Contacto Demostrativo",
                    "telefono": "22340000",
                    "correo": "proveedor.demo@example.com",
                    "direccion": "Direccion ficticia, Honduras",
                    "condicion_pago": condicion,
                    "dias_credito": 30 if condicion == "credito" else 0,
                    "activo": True,
                },
            )
            self.proveedores.append(obj)
        BodegaInventario.objects.update_or_create(empresa=e, nombre="Bodega Principal Demo", defaults={"tipo": "principal", "activa": True})
        BodegaInventario.objects.update_or_create(empresa=e, nombre="Vitrina Demo", defaults={"tipo": "vitrina", "activa": True})
        self._contar("proveedores demo", len(self.proveedores))

    def _crear_facturacion_e_inventario(self):
        e = self.empresa
        inicio_anio = date(self.hoy.year, 1, 1)
        cai, _ = CAI.objects.get_or_create(
            empresa=e,
            numero_cai=f"DEMO-CAI-FICTICIO-{self.hoy.year}",
            defaults={
                "uso_documento": "factura",
                "establecimiento": "001",
                "punto_emision": "001",
                "tipo_documento": "01",
                "rango_inicial": 90000000,
                "rango_final": 90009999,
                "correlativo_actual": 90000000,
                "fecha_activacion": inicio_anio - timedelta(days=370),
                "fecha_limite": self.hoy + timedelta(days=730),
                "activo": True,
            },
        )

        for mes_atras in range(5, -1, -1):
            fecha = fecha_meses_atras(self.hoy, mes_atras, 8)
            for secuencia in range(1, 4):
                correlativo = 90000000 + (5 - mes_atras) * 10 + secuencia
                numero = f"001-001-01-{correlativo:08d}"
                cliente = self.clientes[(mes_atras + secuencia) % len(self.clientes)]
                factura, creada = Factura.objects.get_or_create(
                    empresa=e,
                    numero_factura=numero,
                    defaults={
                        "cliente": cliente,
                        "vendedor": self.usuario,
                        "fecha_emision": fecha,
                        "fecha_vencimiento": fecha + timedelta(days=30),
                        "estado": "emitida",
                        "estado_pago": "pagado" if secuencia != 3 else "pendiente",
                        "cai": cai,
                        "cai_numero": cai.numero_cai,
                        "cai_establecimiento": cai.establecimiento,
                        "cai_punto_emision": cai.punto_emision,
                        "cai_tipo_documento": cai.tipo_documento,
                        "cai_rango_inicial": cai.rango_inicial,
                        "cai_rango_final": cai.rango_final,
                        "cai_fecha_limite": cai.fecha_limite,
                    },
                )
                if creada:
                    items = [self.productos[(secuencia + mes_atras) % 5], self.productos[5 + (secuencia % 3)]]
                    cantidades = [D(str(secuencia)), D("1.00")]
                    for producto, cantidad in zip(items, cantidades):
                        LineaFactura.objects.create(
                            factura=factura,
                            producto=producto,
                            cantidad=cantidad,
                            precio_unitario=producto.precio,
                            costo_unitario=producto.costo_promedio,
                            descuento_porcentaje=D("5.00") if secuencia == 2 else D("0.00"),
                            impuesto=producto.impuesto_predeterminado,
                        )
                    factura.calcular_totales()
                    factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
                self._contar("facturas demo")

            compra_numero = f"DM1-COM-{fecha:%Y%m}"
            compra, creada = CompraInventario.objects.get_or_create(
                empresa=e,
                numero_compra=compra_numero,
                defaults={
                    "proveedor": self.proveedores[mes_atras % len(self.proveedores)],
                    "proveedor_nombre": self.proveedores[mes_atras % len(self.proveedores)].nombre,
                    "referencia_documento": f"PROV-DEMO-{fecha:%Y%m}",
                    "fecha_documento": fecha,
                    "condicion_pago": "credito" if mes_atras % 2 else "contado",
                    "metodo_pago": "transferencia",
                    "dias_credito": 30 if mes_atras % 2 else 0,
                    "estado": "aplicada",
                    "observacion": "Compra ficticia para historial demostrativo.",
                },
            )
            if creada:
                producto = self.productos[mes_atras % 5]
                LineaCompraInventario.objects.create(
                    compra=compra,
                    producto=producto,
                    cantidad=D("8.00") + D(str(mes_atras)),
                    costo_unitario=producto.costo_promedio,
                    impuesto=self.isv,
                )
            self._contar("compras demo")

        for i in range(3):
            cot, creada = Cotizacion.objects.get_or_create(
                empresa=e,
                numero=f"DM-COT-{self.hoy.year}-{i + 1:03d}",
                defaults={
                    "cliente": self.clientes[i],
                    "vendedor": self.usuario,
                    "fecha": self.hoy - timedelta(days=12 - i * 4),
                    "fecha_vencimiento": self.hoy + timedelta(days=15),
                    "asunto": "Propuesta comercial demostrativa",
                    "condiciones": "Validez 15 dias. Entrega segun disponibilidad.",
                    "estado": ["enviada", "aprobada", "borrador"][i],
                },
            )
            if creada:
                producto = self.productos[5 + i]
                LineaCotizacion.objects.create(
                    cotizacion=cot,
                    producto=producto,
                    cantidad=D("1.00"),
                    precio_unitario=producto.precio,
                    impuesto=producto.impuesto_predeterminado,
                )
                cot.calcular_totales()
                cot.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
            self._contar("cotizaciones demo")

    def _crear_contabilidad(self):
        e = self.empresa
        catalogo = [
            ("D-1101", "Caja Demo", "activo"),
            ("D-1102", "Bancos Demo", "activo"),
            ("D-1103", "Clientes Demo", "activo"),
            ("D-1104", "Inventario Demo", "activo"),
            ("D-2101", "Proveedores Demo", "pasivo"),
            ("D-2102", "ISV por pagar Demo", "pasivo"),
            ("D-3101", "Capital social Demo", "patrimonio"),
            ("D-4101", "Ventas Demo", "ingreso"),
            ("D-5101", "Costo de ventas Demo", "costo"),
            ("D-6101", "Gastos administrativos Demo", "gasto"),
            ("D-6102", "Gastos de planilla Demo", "gasto"),
        ]
        self.cuentas = {}
        for codigo, nombre, tipo in catalogo:
            cuenta, _ = CuentaContable.objects.update_or_create(
                empresa=e,
                codigo=codigo,
                defaults={"nombre": nombre, "tipo": tipo, "acepta_movimientos": True, "activa": True},
            )
            self.cuentas[codigo] = cuenta
        ConfiguracionContableEmpresa.objects.update_or_create(
            empresa=e,
            defaults={
                "cuenta_caja": self.cuentas["D-1101"],
                "cuenta_bancos": self.cuentas["D-1102"],
                "cuenta_clientes": self.cuentas["D-1103"],
                "cuenta_inventario": self.cuentas["D-1104"],
                "cuenta_isv_por_pagar": self.cuentas["D-2102"],
                "cuenta_proveedores": self.cuentas["D-2101"],
                "cuenta_ventas": self.cuentas["D-4101"],
            },
        )
        self.banco, _ = CuentaFinanciera.objects.update_or_create(
            empresa=e,
            nombre="Cuenta bancaria Demo Ficohsa",
            defaults={"tipo": "banco", "institucion": "Banco Demo", "numero": "**** 4587", "cuenta_contable": self.cuentas["D-1102"], "activa": True},
        )
        CuentaFinanciera.objects.update_or_create(
            empresa=e,
            nombre="Caja general Demo",
            defaults={"tipo": "caja", "institucion": "Oficina principal", "numero": "CAJA-01", "cuenta_contable": self.cuentas["D-1101"], "activa": True},
        )

        saldo = D("125000.00")
        for mes_atras in range(5, -1, -1):
            fecha = fecha_meses_atras(self.hoy, mes_atras, 20)
            PeriodoContable.objects.get_or_create(empresa=e, anio=fecha.year, mes=fecha.month, defaults={"estado": "abierto"})
            monto = D("28000.00") + D(str((5 - mes_atras) * 3500))
            asiento, _ = AsientoContable.objects.get_or_create(
                empresa=e,
                numero=f"DM-ASI-{fecha:%Y%m}-V",
                defaults={
                    "fecha": fecha,
                    "descripcion": "Resumen mensual de ventas demostrativas",
                    "referencia": f"DEMO-VENTAS-{fecha:%Y%m}",
                    "origen_modulo": "facturacion",
                    "estado": "contabilizado",
                    "creado_por": self.usuario,
                },
            )
            if not asiento.lineas.exists():
                LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["D-1103"], detalle="Cuentas por cobrar", debe=monto)
                LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["D-4101"], detalle="Ventas netas", haber=(monto / D("1.15")).quantize(D("0.01")))
                LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["D-2102"], detalle="ISV ventas", haber=monto - (monto / D("1.15")).quantize(D("0.01")))
            self._contar("asientos contables demo")

            saldo += monto
            MovimientoBancario.objects.update_or_create(
                empresa=e,
                cuenta_financiera=self.banco,
                referencia=f"DM-DEP-{fecha:%Y%m}",
                defaults={
                    "fecha": fecha + timedelta(days=2),
                    "descripcion": "Deposito de ventas del mes - DEMO",
                    "debito": D("0.00"),
                    "credito": monto,
                    "saldo": saldo,
                    "estado": "clasificado",
                    "conciliado": True,
                    "origen_importacion": "Carga demostrativa",
                },
            )
            gasto = D("8500.00") + D(str(mes_atras * 200))
            saldo -= gasto
            MovimientoBancario.objects.update_or_create(
                empresa=e,
                cuenta_financiera=self.banco,
                referencia=f"DM-EGR-{fecha:%Y%m}",
                defaults={
                    "fecha": fecha + timedelta(days=5),
                    "descripcion": "Pago de gastos operativos - DEMO",
                    "debito": gasto,
                    "credito": D("0.00"),
                    "saldo": saldo,
                    "estado": "pendiente" if mes_atras == 0 else "clasificado",
                    "conciliado": mes_atras != 0,
                    "origen_importacion": "Carga demostrativa",
                },
            )
            self._contar("movimientos bancarios demo", 2)
        self._contar("cuentas contables demo", len(catalogo))

    def _crear_rrhh(self):
        e = self.empresa
        empleados = [
            ("DM-E001", "Ana Lucia", "Martinez", "Gerente Administrativa", "Administracion", "32000.00"),
            ("DM-E002", "Carlos Eduardo", "Mejia", "Ejecutivo de Ventas", "Ventas", "22000.00"),
            ("DM-E003", "Sofia Isabel", "Flores", "Contadora", "Finanzas", "26000.00"),
            ("DM-E004", "Jose Manuel", "Reyes", "Tecnico de Soporte", "Tecnologia", "19500.00"),
            ("DM-E005", "Valeria", "Pineda", "Coordinadora RRHH", "Talento Humano", "23500.00"),
            ("DM-E006", "Marco Antonio", "Lopez", "Auxiliar de Bodega", "Operaciones", "16500.00"),
        ]
        self.empleados = []
        for i, (codigo, nombres, apellidos, puesto, departamento, salario) in enumerate(empleados):
            empleado, _ = Empleado.objects.update_or_create(
                empresa=e,
                codigo=codigo,
                defaults={
                    "nombres": nombres,
                    "apellidos": apellidos,
                    "identidad": f"0801-199{i}-0100{i}",
                    "fecha_nacimiento": date(1988 + i, (i % 12) + 1, 10),
                    "fecha_ingreso": fecha_meses_atras(self.hoy, 30 + i * 3, 1),
                    "puesto": puesto,
                    "departamento": departamento,
                    "correo": f"{codigo.lower()}@demo.local",
                    "telefono": f"9900100{i}",
                    "salario_mensual": D(salario),
                    "cuenta_bancaria": f"DEMO-001-{i + 1:04d}",
                    "banco": "Banco Demo",
                    "estado": "activo",
                },
            )
            self.empleados.append(empleado)

        for mes_atras in range(2, -1, -1):
            fecha = fecha_meses_atras(self.hoy, mes_atras, 1)
            fin = date(fecha.year, fecha.month, monthrange(fecha.year, fecha.month)[1])
            periodo, creada = PeriodoPlanilla.objects.get_or_create(
                empresa=e,
                nombre=f"Planilla mensual DEMO {fecha:%Y-%m}",
                defaults={
                    "frecuencia": "mensual",
                    "fecha_inicio": fecha,
                    "fecha_fin": fin,
                    "fecha_pago": fin,
                    "estado": "borrador",
                    "metodo_pago": "transferencia",
                    "creado_por": self.usuario,
                    "cuenta_financiera_pago": self.banco,
                },
            )
            if creada:
                MovimientoPlanilla.objects.create(
                    empleado=self.empleados[1], periodo=periodo, tipo="comision", descripcion="Comision de ventas DEMO", monto=D("1800.00") + D(str(mes_atras * 250))
                )
                MovimientoPlanilla.objects.create(
                    empleado=self.empleados[3], periodo=periodo, tipo="bono", descripcion="Bono por soporte DEMO", monto=D("900.00")
                )
                generar_planilla(periodo)
                periodo.estado = "pagada" if mes_atras else "calculada"
                periodo.save(update_fields=["estado"])
            self._contar("planillas demo")

        VacacionEmpleado.objects.get_or_create(
            empleado=self.empleados[0],
            fecha_inicio=self.hoy + timedelta(days=20),
            defaults={"fecha_fin": self.hoy + timedelta(days=24), "dias": D("5.00"), "estado": "aprobada", "observacion": "Vacaciones programadas DEMO", "aprobado_por": self.usuario},
        )
        self._contar("empleados demo", len(self.empleados))

    def _crear_cobros_demo(self):
        facturas = Factura.objects.filter(
            empresa=self.empresa,
            numero_factura__startswith="001-001-01-900000",
        ).order_by("fecha_emision", "numero_factura")
        for factura in facturas:
            correlativo = int(factura.numero_factura.rsplit("-", 1)[-1])
            secuencia = correlativo % 10
            if secuencia == 3 or factura.total <= 0:
                factura.actualizar_estado_pago()
                continue
            porcentaje = D("1.00") if secuencia == 1 else D("0.50")
            monto = (factura.total_documento_ajustado * porcentaje).quantize(D("0.01"))
            referencia = f"DM-COBRO-{factura.numero_factura}"
            PagoFactura.objects.get_or_create(
                factura=factura,
                referencia=referencia,
                defaults={
                    "fecha": factura.fecha_emision + timedelta(days=3),
                    "monto": monto,
                    "metodo": "transferencia",
                    "cuenta_financiera": self.banco,
                    "cajero": self.usuario,
                },
            )
            factura.actualizar_estado_pago()
            self._contar("cobros y recibos demo")

    def _crear_crm(self):
        e = self.empresa
        plantilla, _ = PlantillaMensaje.objects.update_or_create(
            empresa=e,
            nombre="Promocion clientes frecuentes DEMO",
            defaults={
                "tipo": "promocion",
                "canal": "ambos",
                "asunto": "Beneficio especial para nuestros clientes",
                "mensaje": "Hola {{cliente}}, {{empresa}} tiene una promocion demostrativa para ti.",
                "activa": True,
            },
        )
        campania, _ = CampaniaMarketing.objects.update_or_create(
            empresa=e,
            nombre=f"Campania comercial DEMO {self.hoy:%Y-%m}",
            defaults={"plantilla": plantilla, "audiencia": "promociones", "fecha_programada": timezone.now() + timedelta(days=2), "estado": "programada", "creado_por": self.usuario},
        )
        for cliente in self.clientes[:4]:
            EnvioCampania.objects.update_or_create(
                campania=campania,
                cliente=cliente,
                canal="whatsapp",
                defaults={"mensaje": plantilla.render(cliente=cliente), "estado": "preparado"},
            )
        for i in range(6):
            fecha_hora = timezone.make_aware(datetime.combine(self.hoy + timedelta(days=i - 2), time(9 + i, 0)))
            CitaCliente.objects.update_or_create(
                empresa=e,
                titulo=f"Seguimiento comercial DEMO {i + 1}",
                defaults={
                    "cliente": self.clientes[i % len(self.clientes)],
                    "producto": self.productos[5 + (i % 3)],
                    "fecha_hora": fecha_hora,
                    "duracion_minutos": 45,
                    "responsable": "Equipo Comercial Demo",
                    "estado": "realizada" if i < 2 else ("confirmada" if i < 5 else "pendiente"),
                    "observacion": "Cita ficticia para demostracion del CRM.",
                },
            )
        self._contar("campanias CRM demo")
        self._contar("citas CRM demo", 6)

    def _crear_clinica(self):
        e = self.empresa
        profesional, _ = ProfesionalSalud.objects.update_or_create(
            empresa=e,
            nombre="Dra. Elena Morales Demo",
            defaults={"especialidad": "Medicina general", "colegiacion": "CMH-DEMO-001", "telefono": "99002001", "activo": True},
        )
        servicio, _ = ServicioClinico.objects.update_or_create(
            empresa=e,
            nombre="Consulta medica general DEMO",
            defaults={"categoria": "consulta", "duracion_minutos": 45, "color_calendario": "#0ea5e9", "precio_referencia": D("900.00"), "activo": True},
        )
        for i in range(3):
            paciente, _ = Paciente.objects.update_or_create(
                empresa=e,
                expediente_codigo=f"DM-EXP-{i + 1:04d}",
                defaults={
                    "cliente": self.clientes[i + 3],
                    "nombre": self.clientes[i + 3].nombre,
                    "identidad": f"0801-199{i}-0200{i}",
                    "fecha_nacimiento": date(1990 + i, 3 + i, 12),
                    "sexo": "femenino" if i != 1 else "masculino",
                    "telefono": self.clientes[i + 3].telefono,
                    "whatsapp": self.clientes[i + 3].telefono_whatsapp,
                    "correo": self.clientes[i + 3].correo,
                    "antecedentes_medicos": "Sin antecedentes relevantes. Informacion ficticia.",
                    "activo": True,
                    "creado_por": self.usuario,
                },
            )
            cita_fecha = timezone.make_aware(datetime.combine(self.hoy + timedelta(days=i - 1), time(10 + i, 30)))
            cita, _ = CitaClinica.objects.update_or_create(
                empresa=e,
                paciente=paciente,
                fecha_hora=cita_fecha,
                defaults={
                    "profesional": profesional,
                    "servicio": servicio,
                    "estado": "completada" if i == 0 else "confirmada",
                    "canal": "recepcion",
                    "motivo": "Evaluacion general demostrativa",
                    "pagada": i == 0,
                    "sala": "Consultorio Demo 1",
                },
            )
            tratamiento, _ = TratamientoPaciente.objects.update_or_create(
                empresa=e,
                paciente=paciente,
                nombre="Plan preventivo DEMO",
                defaults={"servicio": servicio, "profesional": profesional, "fecha_inicio": self.hoy, "estado": "en_proceso", "objetivo": "Seguimiento preventivo ficticio", "monto_estimado": D("2700.00")},
            )
            if i == 0:
                ExpedienteEvento.objects.get_or_create(
                    empresa=e,
                    paciente=paciente,
                    titulo="Consulta inicial DEMO",
                    defaults={"cita": cita, "tratamiento": tratamiento, "profesional": profesional, "tipo": "consulta", "descripcion": "Evaluacion clinica ficticia satisfactoria.", "diagnostico": "Paciente estable (DEMO).", "plan": "Control en 30 dias.", "creado_por": self.usuario},
                )
        self._contar("pacientes clinica demo", 3)
        self._contar("citas clinica demo", 3)

    def _crear_tecnicentro(self):
        e = self.empresa
        bahia, _ = BahiaServicio.objects.update_or_create(
            empresa=e,
            codigo="DM-B01",
            defaults={"nombre": "Bahia de servicio DEMO", "especialidad": "Mantenimiento general", "activa": True},
        )
        for i in range(2):
            vehiculo, _ = Vehiculo.objects.update_or_create(
                empresa=e,
                placa=f"DMO-{100 + i}",
                defaults={
                    "cliente": self.clientes[i],
                    "vin": f"VIN-DEMO-000000{i}",
                    "marca": ["Toyota", "Honda"][i],
                    "modelo": ["Corolla", "CR-V"][i],
                    "anio": 2022 + i,
                    "color": ["Blanco", "Gris"][i],
                    "tipo": "turismo" if i == 0 else "suv",
                    "combustible": "gasolina",
                    "kilometraje_actual": 38000 + i * 12000,
                    "activo": True,
                },
            )
            orden, _ = OrdenServicio.objects.update_or_create(
                empresa=e,
                numero=f"DM-OT-{self.hoy.year}-{i + 1:04d}",
                defaults={
                    "cliente": self.clientes[i],
                    "vehiculo": vehiculo,
                    "asesor_recepcion": self.usuario,
                    "tecnico_asignado": self.usuario,
                    "bahia": bahia,
                    "estado": "entregado" if i == 0 else "reparacion",
                    "prioridad": "normal" if i == 0 else "alta",
                    "motivo_ingreso": "Mantenimiento preventivo y revision general DEMO",
                    "observaciones_recepcion": "Registro completamente ficticio.",
                    "kilometraje_entrada": vehiculo.kilometraje_actual,
                    "nivel_combustible": "medio",
                    "fecha_recepcion": timezone.now() - timedelta(days=5 - i * 3),
                },
            )
            InspeccionRecepcion.objects.update_or_create(
                orden=orden,
                defaults={"carroceria": "buena", "llantas": "desgaste", "parabrisas": "bueno", "llanta_repuesto": True, "herramientas": True, "aceptacion_cliente": True, "nombre_aceptante": self.clientes[i].nombre, "inspeccionado_por": self.usuario},
            )
            DiagnosticoVehicular.objects.update_or_create(
                orden=orden,
                defaults={"tecnico": self.usuario, "sintomas_reportados": "Revision preventiva", "hallazgos": "Desgaste normal de consumibles.", "causa_probable": "Uso regular del vehiculo.", "recomendaciones": "Cambio de aceite y filtros.", "estado": "completado"},
            )
            cot, _ = CotizacionTaller.objects.update_or_create(orden=orden, defaults={"estado": "aprobada", "notas": "Cotizacion ficticia aprobada por cliente demo."})
            LineaCotizacionTaller.objects.update_or_create(
                cotizacion=cot,
                descripcion="Mantenimiento preventivo DEMO",
                defaults={"tipo": "mano_obra", "producto": self.productos[8], "cantidad": D("1.00"), "precio_unitario": D("1850.00"), "porcentaje_impuesto": D("15.00")},
            )
            cot.recalcular()
            CitaTaller.objects.update_or_create(
                empresa=e,
                cliente=self.clientes[i],
                servicio_solicitado=f"Servicio programado DEMO {i + 1}",
                defaults={"vehiculo": vehiculo, "orden": orden if i == 0 else None, "fecha_hora": timezone.now() + timedelta(days=i + 2), "duracion_estimada_min": 90, "estado": "confirmada", "observaciones": "Cita ficticia de taller.", "creado_por": self.usuario},
            )
        self._contar("vehiculos tecnicentro demo", 2)
        self._contar("ordenes tecnicentro demo", 2)
