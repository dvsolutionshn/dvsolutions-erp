from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
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
from contabilidad.services import (
    cargar_catalogo_base_honduras,
    registrar_asiento_compra_aplicada,
    registrar_asiento_factura_emitida,
    registrar_asiento_pago_cliente,
    registrar_asiento_pago_proveedor,
    registrar_asiento_planilla_cerrada,
    registrar_asiento_planilla_pagada,
)
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo
from crm.models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, PlantillaMensaje
from facturacion.models import (
    BodegaInventario,
    CAI,
    Cliente,
    CompraInventario,
    ComprobanteEgresoCompra,
    ConfiguracionFacturacionEmpresa,
    Cotizacion,
    Factura,
    InventarioProducto,
    LineaCompraInventario,
    LineaCotizacion,
    LineaFactura,
    MovimientoInventario,
    PagoCompra,
    PagoFactura,
    Producto,
    Proveedor,
    ReciboPago,
    TipoImpuesto,
)
from rrhh.models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado
from rrhh.services import calcular_detalle_planilla
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
        parser.add_argument(
            "--restablecer",
            action="store_true",
            help="Elimina solamente los registros identificados como parte de esta demo antes de regenerarlos.",
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
            if options["restablecer"]:
                self._restablecer_datos_demo()
            self._configurar_empresa()
            self._crear_catalogos()
            self._crear_contabilidad()
            self._crear_facturacion_e_inventario()
            self._crear_cobros_demo()
            self._crear_rrhh()
            self._crear_gastos_y_bancos()
            self._crear_crm()
            self._crear_clinica()
            self._crear_tecnicentro()
            self._cerrar_periodos_contables()

            if options["simular"]:
                transaction.set_rollback(True)

        modo = "SIMULACION REVERSADA" if options["simular"] else "CARGA COMPLETADA"
        self.stdout.write(self.style.SUCCESS(f"{modo}: solo se proceso {DEMO_SLUG}."))
        for nombre, total in sorted(self.contadores.items()):
            self.stdout.write(f"  {nombre}: {total}")

    def _contar(self, nombre, cantidad=1):
        self.contadores[nombre] = self.contadores.get(nombre, 0) + cantidad

    def _restablecer_datos_demo(self):
        """Retira solo datos producidos por este comando, siempre dentro de demo_1."""
        e = self.empresa
        PeriodoContable.objects.filter(empresa=e, anio__gte=2024).update(
            estado="abierto", cerrado_por=None, fecha_cierre=None
        )

        facturas = Factura.objects.filter(empresa=e).filter(
            Q(numero_factura__startswith="001-001-01-900000")
            | Q(numero_factura__startswith="001-001-01-91")
        )
        compras = CompraInventario.objects.filter(empresa=e).filter(
            Q(numero_compra__startswith="DM1-COM-") | Q(numero_compra__startswith="DM-COM-")
        )
        pagos_factura_ids = list(PagoFactura.objects.filter(factura__in=facturas).values_list("id", flat=True))
        pagos_compra_ids = list(PagoCompra.objects.filter(compra__in=compras).values_list("id", flat=True))
        documento_filtros = (
            Q(documento_tipo="factura", documento_id__in=facturas.values("id"))
            | Q(documento_tipo="pago_factura", documento_id__in=pagos_factura_ids)
            | Q(documento_tipo="compra", documento_id__in=compras.values("id"))
            | Q(documento_tipo="pago_compra", documento_id__in=pagos_compra_ids)
        )
        planillas = PeriodoPlanilla.objects.filter(empresa=e, nombre__icontains="DEMO")
        documento_filtros |= Q(documento_tipo="planilla", documento_id__in=planillas.values("id"))
        AsientoContable.objects.filter(empresa=e).filter(
            documento_filtros
            | Q(numero__startswith="DM-")
            | Q(referencia__startswith="DEMO-")
            | Q(referencia__startswith="DM-")
        ).delete()
        MovimientoBancario.objects.filter(empresa=e, origen_importacion="SEED DEMO_1").delete()
        MovimientoInventario.objects.filter(empresa=e, referencia__startswith="DEMO-").delete()
        ReciboPago.objects.filter(factura__in=facturas).delete()
        ComprobanteEgresoCompra.objects.filter(compra__in=compras).delete()
        facturas.delete()
        compras.delete()
        Cotizacion.objects.filter(empresa=e).filter(
            Q(numero__startswith="DM-COT-") | Q(numero__startswith="DM1-COT-")
        ).delete()
        planillas.delete()
        CitaCliente.objects.filter(empresa=e, titulo__icontains="DEMO").delete()
        CampaniaMarketing.objects.filter(empresa=e, nombre__icontains="DEMO").delete()
        self._contar("restablecimientos ejecutados")

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
            ("Maria Fernanda Demo", "08011990100004", "maria.demo@example.com", "99880044", "Tegucigalpa"),
            ("Ferreteria El Roble Demo", "08019000100005", "ventas@robledemo.hn", "99880055", "Comayagua"),
            ("Distribuidora Valle Verde Demo", "06019000100006", "pagos@valleverdedemo.hn", "99880066", "Choluteca"),
            ("Clinica Santa Lucia Demo", "08019000100007", "administracion@santaluciademo.hn", "99880077", "Tegucigalpa"),
            ("Agroservicios Olancho Demo", "15019000100008", "compras@agroolanchodemo.hn", "99880088", "Juticalpa"),
            ("Hoteles del Lago Demo", "12019000100009", "contabilidad@lagodemo.hn", "99880099", "Lago de Yojoa"),
            ("Constructora Horizonte Demo", "08019000100010", "tesoreria@horizontedemo.hn", "99880110", "Tegucigalpa"),
            ("Fundacion Crecer Demo", "08019000100011", "proyectos@crecerdemo.hn", "99880121", "Danli"),
            ("Cafe Montana Azul Demo", "14019000100012", "gerencia@montanaazuldemo.hn", "99880132", "La Esperanza"),
            ("Prospecto Logistica Maya Demo", "05019000100013", "operaciones@mayademo.hn", "99880143", "Puerto Cortes"),
            ("Prospecto Mercado Central Demo", "08019000100014", "gerencia@mercadocentraldemo.hn", "99880154", "Tegucigalpa"),
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
            ("DM-P006", "Terminal Punto de Venta Demo", "producto", "unidad", "8900.00", "6250.00", self.isv),
            ("DM-P007", "UPS 1200VA Demo", "producto", "unidad", "3950.00", "2675.00", self.isv),
            ("DM-P008", "Rollo Termico 80mm Demo", "producto", "caja", "1450.00", "910.00", self.isv),
            ("DM-S001", "Implementacion ERP Demo", "servicio", "servicio", "18500.00", "0.00", self.isv),
            ("DM-S002", "Soporte Tecnico Mensual Demo", "servicio", "mes", "4500.00", "0.00", self.isv),
            ("DM-S003", "Capacitacion de Usuarios Demo", "servicio", "hora", "950.00", "0.00", self.isv),
            ("DM-S004", "Diagnostico Vehicular Demo", "servicio", "servicio", "750.00", "0.00", self.isv),
            ("DM-S005", "Consulta Medica General Demo", "servicio", "servicio", "900.00", "0.00", self.exento),
            ("DM-S006", "Integracion y Automatizacion Demo", "servicio", "servicio", "12500.00", "0.00", self.isv),
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
                    defaults={"existencias": D("0.00"), "stock_minimo": D("10.00")},
                )
        self._contar("productos y servicios demo", len(self.productos))

        self.proveedores = []
        for nombre, rtn, condicion in [
            ("Tecnologia Mayorista Demo", "05019000200001", "credito"),
            ("Suministros Nacionales Demo", "08019000200002", "contado"),
            ("Servicios Logisticos Demo", "08019000200003", "credito"),
            ("Importaciones Digitales Demo", "05019000200004", "credito"),
            ("Papeleria Capital Demo", "08019000200005", "credito"),
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
        self.facturas_demo = []
        self.compras_demo = []
        bienes = [producto for producto in self.productos if producto.controla_inventario]
        servicios = [producto for producto in self.productos if not producto.controla_inventario]

        for anio in range(2024, self.hoy.year + 1):
            base = 91000000 + ((anio - 2024) * 10000)
            cai, _ = CAI.objects.get_or_create(
                empresa=e,
                numero_cai=f"DEMO-CAI-HISTORICO-{anio}",
                defaults={
                    "uso_documento": "factura", "establecimiento": "001", "punto_emision": "001",
                    "tipo_documento": "01", "rango_inicial": base, "rango_final": base + 9999,
                    "correlativo_actual": base, "fecha_activacion": date(anio, 1, 1),
                    "fecha_limite": date(anio, 12, 31), "activo": True,
                },
            )
            secuencia_anual = 0
            for mes in range(1, 13):
                cantidad_mes = 9 if mes <= 4 else 8
                dias_mes = monthrange(anio, mes)[1]
                for posicion in range(cantidad_mes):
                    dia = 3 + ((posicion * (dias_mes - 5)) // max(cantidad_mes - 1, 1))
                    fecha = date(anio, mes, min(dia, dias_mes - 1))
                    if fecha > self.hoy:
                        continue
                    secuencia_anual += 1
                    correlativo = base + secuencia_anual
                    numero = f"001-001-01-{correlativo:08d}"
                    cliente_indice = (secuencia_anual * 3 + mes + anio) % 12
                    cliente = self.clientes[cliente_indice]
                    anulada = secuencia_anual % 29 == 0
                    factura, creada = Factura.objects.update_or_create(
                        empresa=e,
                        numero_factura=numero,
                        defaults={
                            "cliente": cliente, "vendedor": self.usuario, "fecha_emision": fecha,
                            "fecha_vencimiento": fecha + timedelta(days=30 if cliente_indice not in {2, 7} else 45),
                            "estado": "anulada" if anulada else "emitida", "estado_pago": "pendiente",
                            "cai": cai, "cai_numero": cai.numero_cai,
                            "cai_establecimiento": cai.establecimiento, "cai_punto_emision": cai.punto_emision,
                            "cai_tipo_documento": cai.tipo_documento, "cai_rango_inicial": cai.rango_inicial,
                            "cai_rango_final": cai.rango_final, "cai_fecha_limite": cai.fecha_limite,
                        },
                    )
                    if creada or not factura.lineas.exists():
                        producto = bienes[(secuencia_anual + mes) % len(bienes)]
                        servicio = servicios[(secuencia_anual + anio) % len(servicios)]
                        factor_temporada = D("1.25") if mes in {3, 6, 11, 12} else (D("0.80") if mes in {1, 2, 9} else D("1.00"))
                        LineaFactura.objects.create(
                            factura=factura, producto=producto,
                            cantidad=D(str(1 + (secuencia_anual % 3))),
                            precio_unitario=(producto.precio * factor_temporada).quantize(D("0.01")),
                            costo_unitario=producto.costo_promedio,
                            descuento_porcentaje=D("5.00") if secuencia_anual % 11 == 0 else D("0.00"),
                            impuesto=producto.impuesto_predeterminado,
                        )
                        LineaFactura.objects.create(
                            factura=factura, producto=servicio, cantidad=D("1.00"),
                            precio_unitario=(servicio.precio * factor_temporada * D("4.00")).quantize(D("0.01")),
                            costo_unitario=D("0.00"),
                            descuento_porcentaje=D("7.50") if secuencia_anual % 13 == 0 else D("0.00"),
                            impuesto=servicio.impuesto_predeterminado,
                        )
                        factura.calcular_totales()
                        factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
                    if factura.estado == "emitida":
                        registrar_asiento_factura_emitida(factura)
                    self.facturas_demo.append((factura, secuencia_anual, cliente_indice))
                    self._contar(f"facturas {anio}")

                if date(anio, mes, 1) > self.hoy.replace(day=1):
                    continue
                for compra_indice in range(1, 3):
                    compra_fecha = date(anio, mes, 2 + compra_indice * 2)
                    if compra_fecha > self.hoy:
                        continue
                    indice_global = (anio - 2024) * 24 + (mes - 1) * 2 + compra_indice
                    proveedor = self.proveedores[indice_global % len(self.proveedores)]
                    anulada_compra = indice_global % 23 == 0
                    credito = indice_global % 4 != 0
                    compra, creada = CompraInventario.objects.update_or_create(
                        empresa=e,
                        numero_compra=f"DM-COM-{anio}{mes:02d}-{compra_indice:02d}",
                        defaults={
                            "proveedor": proveedor, "proveedor_nombre": proveedor.nombre,
                            "referencia_documento": f"DEMO-PROV-{anio}{mes:02d}-{compra_indice:02d}",
                            "fecha_documento": compra_fecha, "condicion_pago": "credito" if credito else "contado",
                            "metodo_pago": "transferencia", "cuenta_financiera_pago": self.banco if not credito else None,
                            "dias_credito": 30 if credito else 0,
                            "fecha_vencimiento": compra_fecha + timedelta(days=30 if credito else 0),
                            "estado": "anulada" if anulada_compra else "aplicada",
                            "observacion": "Reposicion de inventario - SEED DEMO_1.",
                        },
                    )
                    if creada or not compra.lineas.exists():
                        for desplazamiento in range(2):
                            producto = bienes[(indice_global + desplazamiento * 3) % len(bienes)]
                            LineaCompraInventario.objects.create(
                                compra=compra, producto=producto,
                                cantidad=D(str(5 + ((indice_global + desplazamiento * 3) % 6))),
                                costo_unitario=producto.costo_promedio,
                                descuento_porcentaje=D("3.00") if indice_global % 9 == 0 else D("0.00"),
                                impuesto=self.isv,
                            )
                    if compra.estado == "aplicada":
                        registrar_asiento_compra_aplicada(compra)
                    self.compras_demo.append((compra, indice_global))
                    self._contar("compras demo")

        MovimientoInventario.objects.filter(empresa=e, referencia__startswith="DEMO-").delete()
        movimientos = []
        for producto in bienes:
            movimientos.append((date(2024, 1, 1), 0, producto, "entrada", D("12.00"), None, None, "DEMO-APERTURA"))
        for compra, _indice in self.compras_demo:
            if compra.estado != "aplicada":
                continue
            for linea in compra.lineas.all():
                movimientos.append((compra.fecha_documento, 1, linea.producto, "entrada_compra", linea.cantidad, None, compra, f"DEMO-{compra.numero_compra}"))
        for factura, _secuencia, _cliente_indice in self.facturas_demo:
            if factura.estado != "emitida":
                continue
            for linea in factura.lineas.select_related("producto"):
                if linea.producto.controla_inventario:
                    movimientos.append((factura.fecha_emision, 2, linea.producto, "salida_factura", -linea.cantidad, factura, None, f"DEMO-{factura.numero_factura}"))

        existencias = {producto.id: D("0.00") for producto in bienes}
        for fecha, orden, producto, tipo, cambio, factura, compra, referencia in sorted(
            movimientos, key=lambda item: (item[0], item[1], item[2].id, item[7])
        ):
            anterior = existencias[producto.id]
            resultante = anterior + cambio
            if resultante < 0:
                raise CommandError(f"Inventario demo incoherente para {producto.nombre} en {fecha}.")
            MovimientoInventario.objects.create(
                empresa=e, producto=producto, tipo=tipo, cantidad=abs(cambio),
                existencia_anterior=anterior, existencia_resultante=resultante,
                referencia=referencia, observacion="Movimiento integrado SEED DEMO_1",
                factura=factura, compra_documento=compra, usuario=self.usuario,
                fecha=timezone.make_aware(datetime.combine(fecha, time(12, 0))),
            )
            existencias[producto.id] = resultante
        for producto in bienes:
            InventarioProducto.objects.update_or_create(
                empresa=e, producto=producto,
                defaults={"existencias": existencias[producto.id], "stock_minimo": D("10.00")},
            )
        self._contar("movimientos de inventario", len(movimientos))

    def _crear_contabilidad(self):
        e = self.empresa
        resultado = cargar_catalogo_base_honduras(e)
        codigos = [
            "1101", "110201", "111001", "112001", "210101", "210201", "210301",
            "3101", "410101", "410103", "5101", "610101", "610103", "610104",
            "610105", "610106", "610108", "610201", "610301",
        ]
        self.cuentas = {
            cuenta.codigo: cuenta
            for cuenta in CuentaContable.objects.filter(empresa=e, codigo__in=codigos)
        }
        ConfiguracionContableEmpresa.objects.update_or_create(
            empresa=e,
            defaults={
                "cuenta_caja": self.cuentas["1101"],
                "cuenta_bancos": self.cuentas["110201"],
                "cuenta_clientes": self.cuentas["111001"],
                "cuenta_inventario": self.cuentas["112001"],
                "cuenta_isv_por_pagar": self.cuentas["210201"],
                "cuenta_proveedores": self.cuentas["210101"],
                "cuenta_ventas": self.cuentas["410101"],
            },
        )
        self.banco, _ = CuentaFinanciera.objects.update_or_create(
            empresa=e,
            nombre="Cuenta bancaria Demo Ficohsa",
            defaults={"tipo": "banco", "institucion": "Ficohsa", "numero": "**** 4587", "cuenta_contable": self.cuentas["110201"], "activa": True},
        )
        self.caja, _ = CuentaFinanciera.objects.update_or_create(
            empresa=e,
            nombre="Caja general Demo",
            defaults={"tipo": "caja", "institucion": "Oficina principal", "numero": "CAJA-01", "cuenta_contable": self.cuentas["1101"], "activa": True},
        )
        for anio in range(2024, self.hoy.year + 1):
            ultimo_mes = self.hoy.month if anio == self.hoy.year else 12
            for mes in range(1, ultimo_mes + 1):
                PeriodoContable.objects.update_or_create(
                    empresa=e, anio=anio, mes=mes,
                    defaults={"estado": "abierto", "observacion": "Periodo generado por SEED DEMO_1."},
                )

        asiento, creada = AsientoContable.objects.get_or_create(
            empresa=e,
            numero="DM-APERTURA-2024",
            defaults={
                "fecha": date(2024, 1, 1),
                "descripcion": "Aporte inicial y existencias de apertura - SEED DEMO_1",
                "referencia": "DEMO-APERTURA-2024",
                "origen_modulo": "contabilidad",
                "estado": "contabilizado",
                "creado_por": self.usuario,
            },
        )
        if creada:
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["110201"], detalle="Fondos iniciales", debe=D("3500000.00"))
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["112001"], detalle="Inventario inicial", debe=D("441564.00"))
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["3101"], detalle="Capital aportado", haber=D("3941564.00"))
        self._contar("cuentas contables disponibles", resultado["total_base"])
        self._contar("periodos contables", PeriodoContable.objects.filter(empresa=e, anio__gte=2024).count())

    def _crear_rrhh(self):
        e = self.empresa
        empleados = [
            ("DM-E001", "Ana Lucia", "Martinez", "Gerente Administrativa", "Administracion", "32000.00", date(2022, 5, 3), None, "activo"),
            ("DM-E002", "Carlos Eduardo", "Mejia", "Ejecutivo de Ventas", "Ventas", "22000.00", date(2023, 8, 14), None, "activo"),
            ("DM-E003", "Sofia Isabel", "Flores", "Contadora", "Finanzas", "26000.00", date(2021, 2, 8), None, "activo"),
            ("DM-E004", "Jose Manuel", "Reyes", "Tecnico de Soporte", "Tecnologia", "19500.00", date(2024, 3, 11), None, "activo"),
            ("DM-E005", "Valeria", "Pineda", "Coordinadora RRHH", "Talento Humano", "23500.00", date(2024, 9, 2), None, "activo"),
            ("DM-E006", "Marco Antonio", "Lopez", "Auxiliar de Bodega", "Operaciones", "16500.00", date(2025, 4, 7), None, "activo"),
            ("DM-E007", "Daniela", "Castillo", "Analista de Marketing", "Mercadeo", "21000.00", date(2025, 10, 6), None, "activo"),
            ("DM-E008", "Luis Fernando", "Aguilar", "Asistente Administrativo", "Administracion", "15500.00", date(2023, 1, 16), date(2024, 8, 23), "retirado"),
            ("DM-E009", "Karla Patricia", "Mendoza", "Ejecutiva de Cuenta", "Ventas", "20500.00", date(2024, 2, 5), date(2025, 11, 14), "retirado"),
            ("DM-E010", "Ricardo", "Zelaya", "Desarrollador Junior", "Tecnologia", "24000.00", date(2026, 2, 2), None, "activo"),
        ]
        self.empleados = []
        salarios_base = {}
        for i, (codigo, nombres, apellidos, puesto, departamento, salario, ingreso, salida, estado) in enumerate(empleados):
            base = D(salario)
            aumentos = 2 if ingreso.year <= 2024 else (1 if ingreso.year == 2025 else 0)
            salario_actual = (base * (D("1.05") ** aumentos)).quantize(D("0.01"))
            empleado, _ = Empleado.objects.update_or_create(
                empresa=e,
                codigo=codigo,
                defaults={
                    "nombres": nombres,
                    "apellidos": apellidos,
                    "identidad": f"0801-199{i}-0100{i}",
                    "fecha_nacimiento": date(1988 + i, (i % 12) + 1, 10),
                    "fecha_ingreso": ingreso,
                    "fecha_salida": salida,
                    "puesto": puesto,
                    "departamento": departamento,
                    "correo": f"{codigo.lower()}@demo.local",
                    "telefono": f"9900100{i}",
                    "salario_mensual": salario_actual,
                    "cuenta_bancaria": f"DEMO-001-{i + 1:04d}",
                    "banco": "Banco Demo",
                    "estado": estado,
                    "observacion": "Historial laboral generado por SEED DEMO_1. Incluye aumentos anuales del 5% cuando aplica.",
                },
            )
            self.empleados.append(empleado)
            salarios_base[empleado.id] = base

        for anio in range(2024, self.hoy.year + 1):
            ultimo_mes = self.hoy.month if anio == self.hoy.year else 12
            for mes in range(1, ultimo_mes + 1):
                inicio = date(anio, mes, 1)
                fin = date(anio, mes, monthrange(anio, mes)[1])
                es_actual = anio == self.hoy.year and mes == self.hoy.month
                periodo, _ = PeriodoPlanilla.objects.update_or_create(
                    empresa=e,
                    nombre=f"Planilla mensual DEMO {anio}-{mes:02d}",
                    defaults={
                        "frecuencia": "mensual", "fecha_inicio": inicio, "fecha_fin": fin,
                        "fecha_pago": min(fin, self.hoy), "incluir_13avo": mes == 12,
                        "incluir_14avo": mes == 6, "estado": "borrador",
                        "metodo_pago": "transferencia", "creado_por": self.usuario,
                        "cuenta_financiera_pago": self.banco,
                    },
                )
                for empleado in self.empleados:
                    if empleado.fecha_ingreso > fin or (empleado.fecha_salida and empleado.fecha_salida < inicio):
                        continue
                    base = salarios_base[empleado.id]
                    incremento = max(0, anio - max(2024, empleado.fecha_ingreso.year))
                    empleado.salario_mensual = (base * (D("1.05") ** incremento)).quantize(D("0.01"))
                    dias = D(str((min(empleado.fecha_salida or fin, fin) - max(empleado.fecha_ingreso, inicio)).days + 1))
                    if empleado.fecha_ingreso <= inicio and (not empleado.fecha_salida or empleado.fecha_salida >= fin):
                        dias = D("30.00")

                    movimientos = []
                    if empleado.codigo in {"DM-E002", "DM-E009"} and mes % 2 == 0:
                        movimientos.append(("comision", "Comision comercial DEMO", D("1400.00") + D(str((mes % 4) * 350))))
                    if empleado.codigo in {"DM-E004", "DM-E010"} and mes in {3, 7, 11}:
                        movimientos.append(("bono", "Bono por proyecto DEMO", D("1250.00")))
                    if empleado.codigo == "DM-E006" and mes in {5, 10}:
                        movimientos.append(("deduccion", "Deduccion autorizada DEMO", D("650.00")))
                    if empleado.fecha_salida and inicio <= empleado.fecha_salida <= fin:
                        prestaciones = (empleado.salario_mensual * D("1.25")).quantize(D("0.01"))
                        movimientos.append(("bono", "Prestaciones laborales y liquidacion DEMO", prestaciones))
                    for tipo, descripcion, monto in movimientos:
                        MovimientoPlanilla.objects.update_or_create(
                            empleado=empleado, periodo=periodo, tipo=tipo, descripcion=descripcion,
                            defaults={"monto": monto, "fecha": min(fin, self.hoy), "aplicado": False},
                        )
                    MovimientoPlanilla.objects.filter(empleado=empleado, periodo=periodo).update(aplicado=False)
                    data = calcular_detalle_planilla(empleado, periodo, dias_pagados=dias)
                    movimientos_aplicados = data.pop("movimientos")
                    DetallePlanilla.objects.update_or_create(
                        periodo=periodo, empleado=empleado,
                        defaults={**data, "observacion": "Calculo historico integrado SEED DEMO_1."},
                    )
                    MovimientoPlanilla.objects.filter(id__in=[m.id for m in movimientos_aplicados]).update(aplicado=True)

                if es_actual:
                    periodo.estado = "calculada"
                    periodo.save(update_fields=["estado"])
                else:
                    periodo.estado = "cerrada"
                    periodo.save(update_fields=["estado"])
                    registrar_asiento_planilla_cerrada(periodo)
                    periodo.estado = "pagada"
                    periodo.save(update_fields=["estado"])
                    registrar_asiento_planilla_pagada(periodo, self.usuario)
                self._contar("planillas demo")

        for empleado in self.empleados:
            base = salarios_base[empleado.id]
            aumentos = 2 if empleado.fecha_ingreso.year <= 2024 else (1 if empleado.fecha_ingreso.year == 2025 else 0)
            empleado.salario_mensual = (base * (D("1.05") ** aumentos)).quantize(D("0.01"))
            empleado.save(update_fields=["salario_mensual"])

        VacacionEmpleado.objects.get_or_create(
            empleado=self.empleados[0],
            fecha_inicio=self.hoy + timedelta(days=20),
            defaults={"fecha_fin": self.hoy + timedelta(days=24), "dias": D("5.00"), "estado": "aprobada", "observacion": "Vacaciones programadas DEMO", "aprobado_por": self.usuario},
        )
        self._contar("empleados demo", len(self.empleados))

    def _crear_cobros_demo(self):
        abiertos_historicos = {(2024, 17), (2024, 48), (2025, 9), (2025, 64)}
        for factura, secuencia, cliente_indice in self.facturas_demo:
            if factura.estado != "emitida" or factura.total <= 0:
                factura.actualizar_estado_pago()
                continue
            clave = (factura.fecha_emision.year, secuencia)
            if clave in abiertos_historicos:
                porcentaje = D("0.00")
            elif cliente_indice in {2, 7} and secuencia % 4 == 0:
                porcentaje = D("0.00")
            elif secuencia % 10 in {3, 7} or (cliente_indice in {2, 7} and secuencia % 3 == 0):
                porcentaje = D("0.55")
            else:
                porcentaje = D("1.00")
            retraso = 8 + ((secuencia * 7 + cliente_indice * 11) % (72 if cliente_indice in {2, 7} else 32))
            fecha_pago = factura.fecha_emision + timedelta(days=retraso)
            if porcentaje > 0 and fecha_pago <= self.hoy:
                monto = (factura.total_documento_ajustado * porcentaje).quantize(D("0.01"))
                pago, _ = PagoFactura.objects.update_or_create(
                    factura=factura, referencia=f"DM-COBRO-{factura.numero_factura}",
                    defaults={
                        "fecha": fecha_pago, "monto": monto, "metodo": "transferencia",
                        "cuenta_financiera": self.banco, "cajero": self.usuario,
                    },
                )
                registrar_asiento_pago_cliente(pago)
                self._contar("cobros de clientes")
            factura.actualizar_estado_pago()

        for compra, indice in self.compras_demo:
            if compra.estado != "aplicada" or compra.total_documento <= 0:
                continue
            if indice in {7, 31}:
                porcentaje = D("0.00")
            elif indice % 9 == 0:
                porcentaje = D("0.60")
            else:
                porcentaje = D("1.00")
            fecha_pago = compra.fecha_documento + timedelta(days=5 if compra.condicion_pago == "contado" else 28 + (indice % 18))
            if porcentaje > 0 and fecha_pago <= self.hoy:
                monto = (compra.total_documento * porcentaje).quantize(D("0.01"))
                pago, _ = PagoCompra.objects.update_or_create(
                    compra=compra, referencia=f"DM-PAGO-{compra.numero_compra}",
                    defaults={
                        "fecha": fecha_pago, "monto": monto, "metodo": "transferencia",
                        "cuenta_financiera": self.banco, "observacion": "Pago integrado SEED DEMO_1.",
                    },
                )
                registrar_asiento_pago_proveedor(pago)
                self._contar("pagos a proveedores")

    def _crear_crm(self):
        e = self.empresa
        plantilla, _ = PlantillaMensaje.objects.update_or_create(
            empresa=e,
            nombre="Promocion clientes frecuentes DEMO",
            defaults={
                "tipo": "promocion",
                "canal": "ambos",
                "asunto": "Beneficio especial para nuestros clientes",
                "mensaje": "Hola {{cliente}}, {{empresa}} tiene una oferta especial en {{producto}}.",
                "activa": True,
            },
        )
        for anio in range(2024, self.hoy.year + 1):
            ultimo_mes = self.hoy.month if anio == self.hoy.year else 12
            for mes in range(1, ultimo_mes + 1):
                if mes in {1, 4, 7, 10}:
                    fecha_campania = date(anio, mes, 12)
                    campania, _ = CampaniaMarketing.objects.update_or_create(
                        empresa=e, nombre=f"Campania comercial DEMO {anio}-{mes:02d}",
                        defaults={
                            "plantilla": plantilla, "audiencia": "promociones",
                            "fecha_programada": timezone.make_aware(datetime.combine(fecha_campania, time(9, 0))),
                            "estado": "enviada" if fecha_campania < self.hoy else "programada",
                            "creado_por": self.usuario,
                        },
                    )
                    for cliente in self.clientes[:10]:
                        EnvioCampania.objects.update_or_create(
                            campania=campania, cliente=cliente, canal="whatsapp",
                            defaults={
                                "mensaje": plantilla.render(cliente=cliente, producto=self.productos[8]),
                                "estado": "enviado" if fecha_campania < self.hoy else "preparado",
                                "fecha_envio": timezone.make_aware(datetime.combine(fecha_campania, time(10, 0))) if fecha_campania < self.hoy else None,
                            },
                        )
                    self._contar("campanias CRM demo")

                for seguimiento in range(2):
                    fecha = date(anio, mes, 8 + seguimiento * 12)
                    if fecha > self.hoy:
                        continue
                    indice = ((anio - 2024) * 24 + mes * 2 + seguimiento) % len(self.clientes)
                    cliente = self.clientes[indice]
                    etapa = ["prospeccion", "oportunidad", "negociacion", "ganado", "perdido"][(mes + seguimiento + anio) % 5]
                    estado = "cancelada" if etapa == "perdido" else "realizada"
                    CitaCliente.objects.update_or_create(
                        empresa=e, titulo=f"Seguimiento DEMO {anio}-{mes:02d}-{seguimiento + 1} · {etapa}",
                        defaults={
                            "cliente": cliente, "producto": self.productos[8 + (indice % 4)],
                            "fecha_hora": timezone.make_aware(datetime.combine(fecha, time(9 + seguimiento * 2, 30))),
                            "duracion_minutos": 45, "responsable": "Equipo Comercial Demo",
                            "estado": estado,
                            "observacion": f"Etapa CRM: {etapa}. Seguimiento comercial historico integrado SEED DEMO_1.",
                        },
                    )
                    self._contar("seguimientos CRM demo")

                if mes % 2 == 0:
                    indice_cot = ((anio - 2024) * 6 + mes // 2) % len(self.clientes)
                    cliente = self.clientes[indice_cot]
                    estados = ["enviada", "aprobada", "rechazada", "convertida", "borrador"]
                    estado = estados[(anio + mes) % len(estados)]
                    fecha_cot = date(anio, mes, 10)
                    cotizacion, creada = Cotizacion.objects.update_or_create(
                        empresa=e, numero=f"DM1-COT-{anio}-{mes:02d}",
                        defaults={
                            "cliente": cliente, "vendedor": self.usuario, "fecha": fecha_cot,
                            "fecha_vencimiento": fecha_cot + timedelta(days=20),
                            "asunto": "Oportunidad comercial ERP y equipamiento",
                            "condiciones": "50% de anticipo y saldo contra entrega. Validez 20 dias.",
                            "notas": f"Resultado del embudo CRM: {estado}. SEED DEMO_1.", "estado": estado,
                        },
                    )
                    if creada or not cotizacion.lineas.exists():
                        producto = self.productos[8 + (indice_cot % 4)]
                        LineaCotizacion.objects.create(
                            cotizacion=cotizacion, producto=producto, cantidad=D("1.00"),
                            precio_unitario=producto.precio, impuesto=producto.impuesto_predeterminado,
                        )
                        cotizacion.calcular_totales()
                        cotizacion.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
                    if estado == "convertida":
                        factura_convertida = Factura.objects.filter(
                            empresa=e, cliente=cliente, fecha_emision__gte=fecha_cot,
                            cotizacion_origen__isnull=True,
                        ).order_by("fecha_emision").first()
                        if factura_convertida:
                            Factura.objects.filter(pk=factura_convertida.pk).update(cotizacion_origen=cotizacion)
                    self._contar("oportunidades cotizadas")

    def _crear_gastos_y_bancos(self):
        e = self.empresa
        gastos = [
            ("610103", "Alquiler de oficina", D("28000.00")),
            ("610104", "Energia y agua", D("9200.00")),
            ("610105", "Internet y telefonia", D("4800.00")),
            ("610106", "Papeleria y suministros", D("3500.00")),
            ("610301", "Publicidad y mercadeo", D("7500.00")),
        ]
        for anio in range(2024, self.hoy.year + 1):
            ultimo_mes = self.hoy.month if anio == self.hoy.year else 12
            for mes in range(1, ultimo_mes + 1):
                fecha = date(anio, mes, min(18, monthrange(anio, mes)[1]))
                if fecha > self.hoy:
                    continue
                for indice, (codigo, descripcion, base) in enumerate(gastos, start=1):
                    estacional = D("1.00")
                    if codigo == "610301" and mes in {2, 5, 9}:
                        estacional = D("2.40")
                    if codigo == "610104" and mes in {4, 5, 6}:
                        estacional = D("1.35")
                    monto = (base * estacional * (D("1.00") + D("0.04") * D(str(anio - 2024)))).quantize(D("0.01"))
                    asiento, creado = AsientoContable.objects.get_or_create(
                        empresa=e, numero=f"DM-GTO-{anio}{mes:02d}-{indice:02d}",
                        defaults={
                            "fecha": fecha, "descripcion": f"{descripcion} - SEED DEMO_1",
                            "referencia": f"DEMO-GTO-{anio}{mes:02d}-{indice:02d}",
                            "origen_modulo": "gastos", "estado": "contabilizado", "creado_por": self.usuario,
                        },
                    )
                    if creado:
                        LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas[codigo], detalle=descripcion, debe=monto)
                        LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.cuentas["110201"], detalle="Pago desde banco", haber=monto)
                    self._contar("gastos administrativos")

        MovimientoBancario.objects.filter(empresa=e, origen_importacion="SEED DEMO_1").delete()
        eventos = [(date(2024, 1, 1), "DEMO-BANCO-APERTURA", "Aporte inicial de socios", D("0.00"), D("3500000.00"), None, None)]
        for pago in PagoFactura.objects.filter(factura__empresa=e, referencia__startswith="DM-COBRO-").select_related("factura"):
            eventos.append((pago.fecha, f"DEMO-BANCO-{pago.referencia}", f"Cobro {pago.factura.numero_factura}", D("0.00"), pago.monto, pago, None))
        for pago in PagoCompra.objects.filter(compra__empresa=e, referencia__startswith="DM-PAGO-").select_related("compra"):
            eventos.append((pago.fecha, f"DEMO-BANCO-{pago.referencia}", f"Pago proveedor {pago.compra.numero_compra}", pago.monto, D("0.00"), None, pago))
        for periodo in PeriodoPlanilla.objects.filter(empresa=e, nombre__contains="DEMO", estado="pagada"):
            eventos.append((periodo.fecha_pago, f"DEMO-BANCO-NOM-{periodo.fecha_inicio:%Y%m}", f"Pago {periodo.nombre}", periodo.total_neto, D("0.00"), None, None))
        for asiento in AsientoContable.objects.filter(empresa=e, numero__startswith="DM-GTO-").prefetch_related("lineas"):
            monto = sum((linea.debe for linea in asiento.lineas.all()), D("0.00"))
            eventos.append((asiento.fecha, f"DEMO-BANCO-{asiento.numero}", asiento.descripcion, monto, D("0.00"), None, None))

        saldo = D("0.00")
        for fecha, referencia, descripcion, debito, credito, pago_factura, pago_compra in sorted(eventos, key=lambda item: (item[0], item[1])):
            saldo += credito - debito
            reciente = fecha >= self.hoy - timedelta(days=20)
            MovimientoBancario.objects.create(
                empresa=e, cuenta_financiera=self.banco, fecha=fecha, descripcion=descripcion,
                referencia=referencia, debito=debito, credito=credito, saldo=saldo,
                pago_factura=pago_factura, pago_compra=pago_compra,
                estado="pendiente" if reciente else "clasificado", conciliado=not reciente,
                fecha_conciliacion=timezone.now() if not reciente else None,
                conciliado_por=self.usuario if not reciente else None,
                origen_importacion="SEED DEMO_1",
            )
        self._contar("movimientos bancarios demo", len(eventos))

    def _cerrar_periodos_contables(self):
        completos = PeriodoContable.objects.filter(empresa=self.empresa, anio__gte=2024).exclude(
            anio=self.hoy.year, mes=self.hoy.month
        )
        completos.update(
            estado="cerrado", cerrado_por=self.usuario, fecha_cierre=timezone.now(),
            observacion="Cierre mensual historico generado por SEED DEMO_1.",
        )
        PeriodoContable.objects.filter(
            empresa=self.empresa, anio=self.hoy.year, mes=self.hoy.month
        ).update(estado="abierto", cerrado_por=None, fecha_cierre=None)
        self._contar("cierres contables", completos.count())

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
