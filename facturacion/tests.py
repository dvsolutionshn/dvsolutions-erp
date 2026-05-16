from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from core.models import ConfiguracionAvanzadaEmpresa, ConfiguracionPowerBIEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema
from contabilidad.models import AsientoContable, ClasificacionCompraFiscal, CuentaContable, CuentaFinanciera
from .forms import ConfiguracionFacturacionEmpresaForm
from .models import CAI, Cliente, ComprobanteEgresoCompra, CompraInventario, ConfiguracionFacturacionEmpresa, EntradaInventarioDocumento, Factura, InventarioProducto, LineaCompraInventario, LineaFactura, LineaNotaCredito, MovimientoInventario, NotaCredito, PagoCompra, PagoFactura, Producto, Proveedor, ReciboPago, RegistroCompraFiscal, TipoImpuesto
from .views import _registrar_entrada_nota_credito


class FacturacionTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Empresa Demo",
            slug="demo",
            rtn="08011999123456",
        )
        self.modulo_facturacion = Modulo.objects.create(nombre="Facturacion", codigo="facturacion")
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo_facturacion, activo=True)
        self.rol_total = RolSistema.objects.create(
            nombre="Administrador Operativo",
            codigo="admin-operativo",
            activo=True,
            puede_facturas=True,
            puede_clientes=True,
            puede_productos=True,
            puede_proveedores=True,
            puede_inventario=True,
            puede_compras=True,
            puede_cai=True,
            puede_impuestos=True,
            puede_notas_credito=True,
            puede_recibos=True,
            puede_egresos=True,
            puede_reportes=True,
            puede_cxc=True,
            puede_cxp=True,
            puede_crear_facturas=True,
            puede_editar_facturas=True,
            puede_anular_facturas=True,
            puede_eliminar_borradores=True,
            puede_registrar_pagos_clientes=True,
            puede_crear_clientes=True,
            puede_editar_clientes=True,
            puede_crear_productos=True,
            puede_editar_productos=True,
            puede_crear_proveedores=True,
            puede_editar_proveedores=True,
            puede_ajustar_inventario=True,
            puede_crear_compras=True,
            puede_editar_compras=True,
            puede_aplicar_compras=True,
            puede_anular_compras=True,
            puede_registrar_pagos_proveedores=True,
            puede_crear_notas_credito=True,
            puede_editar_notas_credito=True,
            puede_anular_notas_credito=True,
            puede_exportar_reportes=True,
        )
        self.user = get_user_model().objects.create_user(
            username="admin",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.rol_total,
        )
        self.client.force_login(self.user)
        self.cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente Demo")
        self.producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Producto Demo",
            precio=Decimal("100.00"),
        )
        self.impuesto = TipoImpuesto.objects.create(nombre="ISV 15", porcentaje=Decimal("15.00"))
        self.cai = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-TEST",
            uso_documento="factura",
            establecimiento="001",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=1,
            rango_final=10,
            correlativo_actual=0,
            fecha_activacion=date(2026, 1, 1),
            fecha_limite=date.today() + timedelta(days=30),
        )
        self.cai_nota_credito = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-NC",
            uso_documento="nota_credito",
            establecimiento="001",
            punto_emision="002",
            tipo_documento="03",
            rango_inicial=1,
            rango_final=10,
            correlativo_actual=0,
            fecha_activacion=date(2026, 1, 1),
            fecha_limite=date.today() + timedelta(days=30),
        )

    def crear_factura_con_linea(self, estado="emitida", fecha_emision=None):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado=estado,
            fecha_emision=fecha_emision or date.today(),
        )
        LineaFactura.objects.create(
            factura=factura,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        factura.calcular_totales()
        factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        return factura

    def test_linea_factura_total_linea_conserva_centavos(self):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado="emitida",
            fecha_emision=date.today(),
        )
        linea = LineaFactura.objects.create(
            factura=factura,
            producto=self.producto,
            cantidad=Decimal("1.01"),
            precio_unitario=Decimal("46286.45"),
            impuesto=self.impuesto,
        )

        self.assertEqual(linea.subtotal, Decimal("46749.31"))
        self.assertEqual(linea.impuesto_monto, Decimal("7012.40"))
        self.assertEqual(linea.total_linea, Decimal("53761.71"))

    def test_ver_factura_muestra_total_linea_con_decimales_correctos(self):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado="emitida",
            fecha_emision=date.today(),
        )
        LineaFactura.objects.create(
            factura=factura,
            producto=self.producto,
            cantidad=Decimal("1.01"),
            precio_unitario=Decimal("46286.45"),
            impuesto=self.impuesto,
        )
        factura.calcular_totales()
        factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])

        response = self.client.get(reverse("ver_factura", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "53,761.71")

    def test_libro_compras_fiscal_evita_duplicados_entre_meses(self):
        RegistroCompraFiscal.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Fiscal",
            numero_factura="001-001-01-00000001",
            fecha_documento=date(2026, 1, 10),
            periodo_anio=2026,
            periodo_mes=1,
            subtotal=Decimal("100.00"),
            base_15=Decimal("100.00"),
            isv_15=Decimal("15.00"),
            total=Decimal("115.00"),
        )
        duplicada = RegistroCompraFiscal(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Fiscal",
            numero_factura="001-001-01-00000001",
            fecha_documento=date(2026, 3, 10),
            periodo_anio=2026,
            periodo_mes=3,
            subtotal=Decimal("100.00"),
            base_15=Decimal("100.00"),
            isv_15=Decimal("15.00"),
            total=Decimal("115.00"),
        )
        with self.assertRaises(ValidationError):
            duplicada.full_clean()

    def test_importar_libro_compras_fiscal_omite_duplicadas(self):
        RegistroCompraFiscal.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Fiscal",
            numero_factura="001-001-01-00000001",
            fecha_documento=date(2026, 1, 10),
            periodo_anio=2026,
            periodo_mes=1,
            subtotal=Decimal("100.00"),
            base_15=Decimal("100.00"),
            isv_15=Decimal("15.00"),
            total=Decimal("115.00"),
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "COMPRAS"
        sheet.append([])
        sheet.append(["Fecha", "beneficiario", "No. De factura", "Subtotal", "ISV 15%", "Total"])
        sheet.append([date(2026, 3, 10), "Proveedor Fiscal", "001-001-01-00000001", 100, 15, 115])
        sheet.append([date(2026, 3, 11), "Proveedor Nuevo", "001-001-01-00000002", 200, 30, 230])
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        archivo = SimpleUploadedFile(
            "libro_compras.xlsx",
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(
            reverse("importar_libro_compras_fiscal", args=[self.empresa.slug]),
            {"archivo": archivo, "periodo_anio": "2026", "periodo_mes": "3"},
        )
        self.assertRedirects(response, reverse("libro_compras_fiscal_detalle", args=[self.empresa.slug, 2026, 3]))
        self.assertTrue(RegistroCompraFiscal.objects.filter(numero_factura="001-001-01-00000002").exists())
        self.assertEqual(RegistroCompraFiscal.objects.filter(numero_factura="001-001-01-00000001").count(), 1)

    def test_libro_compras_fiscal_muestra_periodos_y_detalle(self):
        RegistroCompraFiscal.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Fiscal",
            numero_factura="001-001-01-00000003",
            fecha_documento=date(2026, 3, 10),
            periodo_anio=2026,
            periodo_mes=3,
            subtotal=Decimal("100.00"),
            base_15=Decimal("100.00"),
            isv_15=Decimal("15.00"),
            total=Decimal("115.00"),
        )
        response = self.client.get(reverse("libro_compras_fiscal", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Libros por mes")
        self.assertContains(response, "3/2026")

        response = self.client.get(reverse("libro_compras_fiscal_detalle", args=[self.empresa.slug, 2026, 3]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "001-001-01-00000003")

    def test_crear_libro_compras_guarda_clasificacion_contable(self):
        cuenta_gasto = CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="6101",
            nombre="Combustible",
            tipo="gasto",
        )
        clasificacion = ClasificacionCompraFiscal.objects.create(
            empresa=self.empresa,
            nombre="Combustible",
            cuenta_contable=cuenta_gasto,
        )
        response = self.client.post(
            reverse("crear_registro_compra_fiscal", args=[self.empresa.slug]),
            {
                "fecha_documento[]": ["2026-03-10"],
                "clasificacion_id[]": [str(clasificacion.id)],
                "proveedor_nombre[]": ["Proveedor Fiscal"],
                "numero_factura[]": ["001-001-01-00000004"],
                "exento[]": ["0.00"],
                "base_15[]": ["100.00"],
                "base_18[]": ["0.00"],
            },
        )
        self.assertRedirects(response, reverse("libro_compras_fiscal_detalle", args=[self.empresa.slug, 2026, 3]))
        registro = RegistroCompraFiscal.objects.get(numero_factura="001-001-01-00000004")
        self.assertEqual(registro.clasificacion_contable, clasificacion)

    def crear_compra_con_linea(self, estado="aplicada", condicion_pago="contado", dias_credito=0):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Demo",
            condicion_pago=condicion_pago,
            dias_credito=dias_credito,
        )
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor=proveedor,
            proveedor_nombre=proveedor.nombre,
            fecha_documento=date.today(),
            condicion_pago=condicion_pago,
            dias_credito=dias_credito,
            estado=estado,
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("3.00"),
            costo_unitario=Decimal("40.00"),
        )
        return compra

    def test_pago_no_puede_superar_saldo(self):
        factura = self.crear_factura_con_linea()

        with self.assertRaises(ValidationError):
            PagoFactura.objects.create(
                factura=factura,
                monto=factura.total + Decimal("1.00"),
                metodo="efectivo",
                fecha=date.today(),
            )

    def test_dashboard_facturacion_muestra_menu_modular(self):
        response = self.client.get(reverse("facturacion_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Facturación")
        self.assertContains(response, "Configuracion")
        self.assertContains(response, "Clientes")
        self.assertContains(response, "Productos")
        self.assertContains(response, "Inventario")
        self.assertContains(response, "Notas de Crédito")
        self.assertContains(response, "Recibos")
        self.assertContains(response, "Cuentas por Cobrar")

    def test_configuracion_facturacion_guarda_preferencias_por_empresa(self):
        response = self.client.post(
            reverse("configuracion_facturacion", args=[self.empresa.slug]),
            {
                "plantilla_factura_pdf": "alternativa",
                "nombre_comercial_documentos": "Marca Premium Demo",
                "color_primario": "#123456",
                "color_secundario": "#abcdef",
                "logo_ancho_pdf": "145",
                "logo_alto_pdf": "72",
                "mostrar_vendedor": "on",
                "mostrar_descuentos": "on",
                "leyenda_factura": "Gracias por su compra.",
                "pie_factura": "Documento generado por DV Solutions ERP.",
            },
        )

        self.assertRedirects(response, reverse("configuracion_facturacion", args=[self.empresa.slug]))
        configuracion = ConfiguracionFacturacionEmpresa.objects.get(empresa=self.empresa)
        self.assertEqual(configuracion.plantilla_factura_pdf, "alternativa")
        self.assertEqual(configuracion.nombre_comercial_documentos, "Marca Premium Demo")
        self.assertEqual(configuracion.color_primario, "#123456")
        self.assertEqual(configuracion.logo_ancho_pdf, 145)
        self.assertEqual(configuracion.logo_alto_pdf, 72)
        self.assertTrue(configuracion.mostrar_vendedor)
        self.assertFalse(configuracion.mostrar_notas_linea)

    def test_form_configuracion_oculta_plantilla_notas_extensas_si_empresa_no_aplica(self):
        configuracion = ConfiguracionFacturacionEmpresa.objects.create(empresa=self.empresa)
        form = ConfiguracionFacturacionEmpresaForm(
            instance=configuracion,
            permite_plantilla_notas_extensas=False,
        )
        self.assertNotIn(
            ("notas_extensas", "Factura notas extensas"),
            list(form.fields["plantilla_factura_pdf"].choices),
        )

    def test_form_configuracion_muestra_plantilla_notas_extensas_si_empresa_aplica(self):
        configuracion = ConfiguracionFacturacionEmpresa.objects.create(
            empresa=Empresa.objects.create(
                nombre="Digital Planning",
                slug="digital-planning",
                rtn="08011999123457",
            )
        )
        form = ConfiguracionFacturacionEmpresaForm(
            instance=configuracion,
            permite_plantilla_notas_extensas=True,
        )
        self.assertIn(
            ("notas_extensas", "Factura notas extensas"),
            list(form.fields["plantilla_factura_pdf"].choices),
        )

    def test_form_configuracion_oculta_plantilla_independiente_si_empresa_no_aplica(self):
        configuracion = ConfiguracionFacturacionEmpresa.objects.create(empresa=self.empresa)
        form = ConfiguracionFacturacionEmpresaForm(
            instance=configuracion,
            permite_plantilla_independiente=False,
        )
        self.assertNotIn(
            ("independiente", "Factura independiente"),
            list(form.fields["plantilla_factura_pdf"].choices),
        )

    def test_form_configuracion_muestra_plantilla_independiente_si_empresa_aplica(self):
        configuracion = ConfiguracionFacturacionEmpresa.objects.create(empresa=self.empresa)
        form = ConfiguracionFacturacionEmpresaForm(
            instance=configuracion,
            permite_plantilla_independiente=True,
        )
        self.assertIn(
            ("independiente", "Factura independiente"),
            list(form.fields["plantilla_factura_pdf"].choices),
        )

    def test_facturas_dashboard_muestra_listado_facturas(self):
        factura = self.crear_factura_con_linea()

        response = self.client.get(reverse("facturas_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, factura.cliente.nombre)

    def test_rol_limitado_bloquea_clientes(self):
        rol_limitado = RolSistema.objects.create(
            nombre="Solo Facturas",
            codigo="solo-facturas",
            puede_facturas=True,
        )
        self.user.rol_sistema = rol_limitado
        self.user.es_administrador_empresa = False
        self.user.save(update_fields=["rol_sistema", "es_administrador_empresa"])

        response = self.client.get(reverse("clientes_facturacion", args=[self.empresa.slug]))

        self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]))

    def test_rol_limitado_bloquea_crear_factura_sin_permiso_especifico(self):
        rol_limitado = RolSistema.objects.create(
            nombre="Consulta Facturas",
            codigo="consulta-facturas",
            puede_facturas=True,
        )
        self.user.rol_sistema = rol_limitado
        self.user.es_administrador_empresa = False
        self.user.save(update_fields=["rol_sistema", "es_administrador_empresa"])

        response = self.client.get(reverse("crear_factura", args=[self.empresa.slug]))

        self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]))

    def test_rol_limitado_bloquea_crear_clientes_sin_permiso_especifico(self):
        rol_limitado = RolSistema.objects.create(
            nombre="Solo Facturas Activas",
            codigo="solo-facturas-activo",
            puede_facturas=True,
            puede_crear_facturas=True,
        )
        self.user.rol_sistema = rol_limitado
        self.user.es_administrador_empresa = False
        self.user.save(update_fields=["rol_sistema", "es_administrador_empresa"])

        response = self.client.get(reverse("crear_cliente_facturacion", args=[self.empresa.slug]))

        self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]))

    def test_facturas_dashboard_filtra_por_cliente_con_sugerencias(self):
        factura = self.crear_factura_con_linea()
        otro_cliente = Cliente.objects.create(empresa=self.empresa, nombre="Zeta Retail")
        Factura.objects.create(
            empresa=self.empresa,
            cliente=otro_cliente,
            estado="borrador",
            fecha_emision=date.today(),
        )

        response = self.client.get(
            reverse("facturas_dashboard", args=[self.empresa.slug]),
            {"q": "Cliente Demo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="facturas-sugerencias"', html=False)
        self.assertContains(response, "Cliente Demo")
        self.assertEqual(len(response.context["facturas"]), 1)
        self.assertEqual(response.context["facturas"][0].cliente.nombre, "Cliente Demo")

    def test_vista_pago_rechaza_monto_mayor_al_saldo(self):
        factura = self.crear_factura_con_linea()

        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, factura.id]),
            {"monto": str(factura.total + Decimal("1.00")), "metodo": "efectivo", "fecha": date.today()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PagoFactura.objects.filter(factura=factura).count(), 0)

    def test_pago_genera_recibo_automaticamente(self):
        factura = self.crear_factura_con_linea()

        pago = PagoFactura.objects.create(
            factura=factura,
            monto=Decimal("50.00"),
            metodo="efectivo",
            fecha=date.today(),
        )

        self.assertTrue(ReciboPago.objects.filter(pago=pago).exists())
        self.assertEqual(pago.recibo.factura, factura)
        self.assertEqual(pago.recibo.cliente, factura.cliente)
        self.assertEqual(pago.recibo.monto, Decimal("50.00"))

    def test_pago_cliente_usa_cuenta_financiera_en_asiento(self):
        modulo_contabilidad, _ = Modulo.objects.get_or_create(
            codigo="contabilidad",
            defaults={"nombre": "Contabilidad"},
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo_contabilidad, activo=True)
        cuenta_banco = CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="1102",
            nombre="Banco Principal",
            tipo="activo",
        )
        cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=self.empresa,
            nombre="Banco Principal Lempiras",
            tipo="banco",
            cuenta_contable=cuenta_banco,
        )
        factura = self.crear_factura_con_linea()

        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, factura.id]),
            {
                "fecha": str(date.today()),
                "monto": "50.00",
                "metodo": "transferencia",
                "cuenta_financiera": str(cuenta_financiera.id),
                "referencia": "DEP-001",
            },
        )

        self.assertRedirects(response, reverse("ver_factura", args=[self.empresa.slug, factura.id]))
        pago = PagoFactura.objects.get(factura=factura, referencia="DEP-001")
        self.assertEqual(pago.cuenta_financiera, cuenta_financiera)
        asiento = AsientoContable.objects.get(documento_tipo="pago_factura", documento_id=pago.id, evento="cobro")
        self.assertTrue(asiento.lineas.filter(cuenta=cuenta_banco, debe=Decimal("50.00")).exists())

    def test_registrar_pago_prepara_cuentas_financieras_base(self):
        CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="1101",
            nombre="Caja General",
            tipo="activo",
            acepta_movimientos=True,
        )
        CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="110201",
            nombre="Banco Moneda Nacional",
            tipo="activo",
            acepta_movimientos=True,
        )
        factura = self.crear_factura_con_linea()

        response = self.client.get(reverse("registrar_pago", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Caja General - 1101 Caja General")
        self.assertContains(response, "Banco Principal HNL - 110201 Banco Moneda Nacional")
        self.assertTrue(CuentaFinanciera.objects.filter(empresa=self.empresa, nombre="Caja General", tipo="caja").exists())

    def test_recibos_dashboard_muestra_recibo(self):
        factura = self.crear_factura_con_linea()
        pago = PagoFactura.objects.create(
            factura=factura,
            monto=Decimal("50.00"),
            metodo="efectivo",
            fecha=date.today(),
        )

        response = self.client.get(reverse("recibos_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pago.recibo.numero_recibo)

    def test_anular_factura_cambia_estado(self):
        factura = self.crear_factura_con_linea()

        response = self.client.post(reverse("anular_factura", args=[self.empresa.slug, factura.id]))
        factura.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(factura.estado, "anulada")

    def test_borrador_sin_cai_se_puede_ver(self):
        factura = self.crear_factura_con_linea(estado="borrador")
        factura.cai = None
        factura.numero_factura = None
        factura.save(update_fields=["cai", "numero_factura"])

        response = self.client.get(reverse("ver_factura", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin CAI asignado")

    def test_eliminar_factura_borrador_sin_numero(self):
        factura = self.crear_factura_con_linea(estado="borrador")
        factura.cai = None
        factura.numero_factura = None
        factura.save(update_fields=["cai", "numero_factura"])

        response = self.client.post(reverse("eliminar_factura", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Factura.objects.filter(id=factura.id).exists())

    def test_no_elimina_factura_emitida(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.post(reverse("eliminar_factura", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Factura.objects.filter(id=factura.id).exists())

    def test_empresa_especial_puede_eliminar_factura_emitida_historica(self):
        self.empresa.nombre = "AMKT Digital"
        self.empresa.slug = "amkt-digital"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.post(reverse("eliminar_factura", args=[self.empresa.slug, factura.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Factura.objects.filter(id=factura.id).exists())

    def test_eliminar_factura_historica_recalcula_correlativo_cai(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])

        factura_1 = self.crear_factura_con_linea(estado="emitida")
        factura_2 = self.crear_factura_con_linea(estado="emitida")

        self.assertEqual(factura_1.numero_factura, "001-001-01-00000001")
        self.assertEqual(factura_2.numero_factura, "001-001-01-00000002")

        response = self.client.post(reverse("eliminar_factura", args=[self.empresa.slug, factura_2.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Factura.objects.filter(id=factura_2.id).exists())

        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 1)

        factura_3 = self.crear_factura_con_linea(estado="emitida")
        self.assertEqual(factura_3.numero_factura, "001-001-01-00000002")

    def test_recalculo_correlativo_ignora_borradores_y_anuladas(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])

        factura_emitida = self.crear_factura_con_linea(estado="emitida")
        self.assertEqual(factura_emitida.numero_factura, "001-001-01-00000001")

        factura_anulada = self.crear_factura_con_linea(estado="borrador")
        factura_anulada.fecha_emision = date.today()
        factura_anulada.numero_factura = "001-001-01-00000002"
        factura_anulada.estado = "anulada"
        factura_anulada.save(update_fields=["fecha_emision", "numero_factura", "estado"])

        factura_borrador = self.crear_factura_con_linea(estado="borrador")
        factura_borrador.fecha_emision = date.today()
        factura_borrador.numero_factura = "001-001-01-00000003"
        factura_borrador.save(update_fields=["fecha_emision", "numero_factura"])

        self.client.post(reverse("eliminar_factura", args=[self.empresa.slug, factura_emitida.id]))

        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)

        nueva = self.crear_factura_con_linea(estado="emitida")
        self.assertEqual(nueva.numero_factura, "001-001-01-00000001")

    def test_empresa_especial_puede_editar_cai_usado(self):
        self.empresa.nombre = "INTEGRATED SALES AND SERVICES S. DE R.L."
        self.empresa.slug = "integrated-sales-and-services-s-de-r-l"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        factura = self.crear_factura_con_linea(estado="emitida")

        self.cai.numero_cai = "CAI-AJUSTADO"
        self.cai.save()

        self.cai.refresh_from_db()
        factura.refresh_from_db()
        self.assertEqual(self.cai.numero_cai, "CAI-AJUSTADO")
        self.assertEqual(factura.cai_numero_historico, "CAI-TEST")

    def test_empresa_especial_puede_eliminar_cai_sin_documentos_asociados(self):
        self.empresa.nombre = "AMKT Digital"
        self.empresa.slug = "amkt-digital"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        cai = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-BORRAR",
            uso_documento="factura",
            establecimiento="009",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=91,
            rango_final=100,
            correlativo_actual=90,
            fecha_activacion=date(2026, 1, 1),
            fecha_limite=date(2026, 12, 31),
            activo=True,
        )

        response = self.client.post(reverse("eliminar_cai_facturacion", args=[self.empresa.slug, cai.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CAI.objects.filter(id=cai.id).exists())

    def test_duplicar_factura_crea_borrador_sin_numero(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.post(reverse("duplicar_factura", args=[self.empresa.slug, factura.id]))
        factura_nueva = Factura.objects.exclude(id=factura.id).get()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(factura_nueva.estado, "borrador")
        self.assertIsNone(factura_nueva.numero_factura)
        self.assertIsNone(factura_nueva.cai)
        self.assertEqual(factura_nueva.fecha_emision, timezone.now().date())
        self.assertEqual(factura_nueva.lineas.count(), factura.lineas.count())
        self.assertEqual(factura_nueva.total, factura.total)

    def test_crear_cliente_desde_facturacion(self):
        response = self.client.post(
            reverse("crear_cliente_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "Cliente Nuevo",
                "rtn": "08011999111111",
                "direccion": "Tegucigalpa",
                "ciudad": "Tegucigalpa",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Cliente.objects.filter(empresa=self.empresa, nombre="Cliente Nuevo").exists())

    def test_no_permite_cliente_duplicado_por_nombre(self):
        Cliente.objects.create(empresa=self.empresa, nombre="Cliente Unico")

        response = self.client.post(
            reverse("crear_cliente_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "cliente unico",
                "rtn": "",
                "direccion": "Tegucigalpa",
                "ciudad": "Tegucigalpa",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe un cliente con este nombre en la empresa.")

    def test_no_permite_cliente_duplicado_por_rtn(self):
        Cliente.objects.create(empresa=self.empresa, nombre="Cliente RTN", rtn="08011999111111")

        response = self.client.post(
            reverse("crear_cliente_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "Otro Cliente",
                "rtn": "08011999111111",
                "direccion": "Tegucigalpa",
                "ciudad": "Tegucigalpa",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe un cliente con este RTN en la empresa.")

    def test_listado_clientes_facturacion(self):
        response = self.client.get(reverse("clientes_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cliente.nombre)

    def test_listado_clientes_tiene_buscador_con_sugerencias(self):
        response = self.client.get(reverse("clientes_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="clientes-sugerencias"', html=False)

    def test_editar_cliente_facturacion(self):
        response = self.client.post(
            reverse("editar_cliente_facturacion", args=[self.empresa.slug, self.cliente.id]),
            {
                "nombre": "Cliente Editado",
                "rtn": self.cliente.rtn or "",
                "direccion": self.cliente.direccion or "",
                "ciudad": "San Pedro Sula",
                "activo": "on",
            },
        )
        self.cliente.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.cliente.nombre, "Cliente Editado")

    def test_puede_eliminar_cliente_sin_facturas(self):
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente Temporal")

        response = self.client.post(
            reverse("eliminar_cliente_facturacion", args=[self.empresa.slug, cliente.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cliente.objects.filter(id=cliente.id).exists())
        self.assertContains(response, "Cliente eliminado correctamente.")

    def test_no_elimina_cliente_con_facturas_registradas(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.post(
            reverse("eliminar_cliente_facturacion", args=[self.empresa.slug, factura.cliente.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Cliente.objects.filter(id=factura.cliente.id).exists())
        self.assertContains(response, "No se puede eliminar este cliente porque tiene facturas registradas.")

    def test_crear_producto_desde_facturacion(self):
        response = self.client.post(
            reverse("crear_producto_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "Producto Nuevo",
                "codigo": "PRD-002",
                "tipo_item": "producto",
                "unidad_medida": "unidad",
                "descripcion": "Servicio mensual",
                "precio": "250.00",
                "impuesto_predeterminado": str(self.impuesto.id),
                "activo": "on",
                "controla_inventario": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Producto.objects.filter(empresa=self.empresa, nombre="Producto Nuevo").exists())

    def test_crear_producto_oculta_perfil_farmaceutico_en_empresa_base(self):
        response = self.client.get(reverse("crear_producto_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Perfil farmaceutico")
        self.assertNotContains(response, "Principio activo")

    def test_crear_producto_muestra_perfil_farmaceutico_si_empresa_lo_activa(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.usa_inventario_farmaceutico = True
        configuracion.save(update_fields=["usa_inventario_farmaceutico"])

        response = self.client.get(reverse("crear_producto_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Perfil farmaceutico")
        self.assertContains(response, "Principio activo")

    def test_crear_proveedor_desde_facturacion(self):
        response = self.client.post(
            reverse("crear_proveedor_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "Proveedor Nuevo",
                "rtn": "08011999111111",
                "contacto": "Ana López",
                "telefono": "9999-1111",
                "correo": "proveedor@example.com",
                "direccion": "Tegucigalpa",
                "ciudad": "Tegucigalpa",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Proveedor.objects.filter(empresa=self.empresa, nombre="Proveedor Nuevo").exists())

    def test_listado_proveedores_facturacion(self):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Demo",
            activo=True,
        )

        response = self.client.get(reverse("proveedores_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, proveedor.nombre)

    def test_ver_proveedor_muestra_historial_compras(self):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Historial",
            activo=True,
        )
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor=proveedor,
            proveedor_nombre=proveedor.nombre,
            referencia_documento="REF-HIST-001",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("2.00"),
            costo_unitario=Decimal("90.00"),
        )

        response = self.client.get(
            reverse("ver_proveedor_facturacion", args=[self.empresa.slug, proveedor.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, proveedor.nombre)
        self.assertContains(response, compra.numero_compra)
        self.assertContains(response, "Compras y saldo abierto")

    def test_compra_guarda_proveedor_relacionado(self):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Compra",
            activo=True,
        )

        response = self.client.post(
            reverse("crear_compra", args=[self.empresa.slug]),
            {
                "proveedor": str(proveedor.id),
                "proveedor_nombre": "",
                "referencia_documento": "FAC-PROV-010",
                "fecha_documento": str(date.today()),
                "observacion": "Compra con proveedor",
                "estado": "borrador",
                "lineas_compra-TOTAL_FORMS": "1",
                "lineas_compra-INITIAL_FORMS": "0",
                "lineas_compra-MIN_NUM_FORMS": "0",
                "lineas_compra-MAX_NUM_FORMS": "1000",
                "lineas_compra-0-producto": str(self.producto.id),
                "lineas_compra-0-cantidad": "1.00",
                "lineas_compra-0-costo_unitario": "90.00",
                "lineas_compra-0-comentario": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        compra = CompraInventario.objects.get(referencia_documento="FAC-PROV-010")
        self.assertEqual(compra.proveedor, proveedor)
        self.assertEqual(compra.proveedor_nombre, proveedor.nombre)

    def test_listado_productos_facturacion(self):
        response = self.client.get(reverse("productos_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.producto.nombre)

    def test_listado_productos_tiene_buscador_con_sugerencias(self):
        response = self.client.get(reverse("productos_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="productos-sugerencias"', html=False)

    def test_inventario_dashboard_muestra_producto(self):
        response = self.client.get(reverse("inventario_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.producto.nombre)

    def test_kardex_inventario_muestra_movimientos(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("10.00"),
            stock_minimo=Decimal("2.00"),
        )
        MovimientoInventario.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            tipo="ajuste_entrada",
            cantidad=Decimal("10.00"),
            existencia_anterior=Decimal("0.00"),
            existencia_resultante=Decimal("10.00"),
            referencia="Carga inicial",
        )

        response = self.client.get(reverse("kardex_inventario", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.producto.nombre)
        self.assertContains(response, "Carga inicial")

    def test_ajuste_manual_inventario_actualiza_existencias(self):
        response = self.client.post(
            reverse("ajustar_inventario", args=[self.empresa.slug]),
            {
                "producto": str(self.producto.id),
                "tipo_ajuste": "ajuste_entrada",
                "cantidad": "10.00",
                "observacion": "Carga inicial",
                "stock_minimo": "2.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("10.00"))
        self.assertEqual(inventario.stock_minimo, Decimal("2.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.producto, tipo="ajuste_entrada").exists())

    def test_entrada_inventario_actualiza_existencias(self):
        response = self.client.post(
            reverse("entrada_inventario", args=[self.empresa.slug]),
            {
                "producto": str(self.producto.id),
                "cantidad": "15.00",
                "referencia": "Carga inicial abril",
                "observacion": "Ingreso formal de stock",
                "stock_minimo": "3.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("15.00"))
        self.assertEqual(inventario.stock_minimo, Decimal("3.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.producto, tipo="entrada", referencia="Carga inicial abril").exists())

    def test_crear_documento_entrada_inventario_aplicada(self):
        response = self.client.post(
            reverse("crear_entrada_inventario_documento", args=[self.empresa.slug]),
            {
                "referencia": "DOC-ENT-001",
                "fecha_documento": str(date.today()),
                "observacion": "Carga por documento",
                "estado": "aplicada",
                "lineas_entrada-TOTAL_FORMS": "1",
                "lineas_entrada-INITIAL_FORMS": "0",
                "lineas_entrada-MIN_NUM_FORMS": "0",
                "lineas_entrada-MAX_NUM_FORMS": "1000",
                "lineas_entrada-0-producto": str(self.producto.id),
                "lineas_entrada-0-cantidad": "7.00",
                "lineas_entrada-0-comentario": "Lote inicial",
            },
        )

        self.assertEqual(response.status_code, 302)
        entrada = EntradaInventarioDocumento.objects.get(referencia="DOC-ENT-001")
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(entrada.estado, "aplicada")
        self.assertEqual(inventario.existencias, Decimal("7.00"))
        self.assertTrue(MovimientoInventario.objects.filter(entrada_documento=entrada, tipo="entrada").exists())

    def test_compras_dashboard_muestra_modulo(self):
        response = self.client.get(reverse("compras_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compras")

    def test_crear_compra_tiene_buscador_con_sugerencias_de_proveedor(self):
        Proveedor.objects.create(empresa=self.empresa, nombre="Proveedor Sugerido")

        response = self.client.get(reverse("crear_compra", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="proveedores-sugerencias"', html=False)

    def test_crear_compra_aplicada_ingresa_inventario(self):
        response = self.client.post(
            reverse("crear_compra", args=[self.empresa.slug]),
            {
                "proveedor_nombre": "Distribuidora Central",
                "referencia_documento": "FAC-PROV-001",
                "fecha_documento": str(date.today()),
                "observacion": "Compra inicial",
                "estado": "aplicada",
                "lineas_compra-TOTAL_FORMS": "1",
                "lineas_compra-INITIAL_FORMS": "0",
                "lineas_compra-MIN_NUM_FORMS": "0",
                "lineas_compra-MAX_NUM_FORMS": "1000",
                "lineas_compra-0-producto": str(self.producto.id),
                "lineas_compra-0-cantidad": "5.00",
                "lineas_compra-0-costo_unitario": "80.00",
                "lineas_compra-0-comentario": "Lote abril",
            },
        )

        self.assertEqual(response.status_code, 302)
        compra = CompraInventario.objects.get(proveedor_nombre="Distribuidora Central")
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(compra.estado, "aplicada")
        self.assertEqual(compra.total_unidades, Decimal("5.00"))
        self.assertEqual(compra.total_documento, Decimal("400.00"))
        self.assertEqual(inventario.existencias, Decimal("5.00"))
        self.assertTrue(MovimientoInventario.objects.filter(compra_documento=compra, tipo="entrada_compra").exists())

    def test_compra_borrador_no_mueve_inventario(self):
        response = self.client.post(
            reverse("crear_compra", args=[self.empresa.slug]),
            {
                "proveedor_nombre": "Distribuidora Norte",
                "referencia_documento": "FAC-PROV-002",
                "fecha_documento": str(date.today()),
                "observacion": "",
                "estado": "borrador",
                "lineas_compra-TOTAL_FORMS": "1",
                "lineas_compra-INITIAL_FORMS": "0",
                "lineas_compra-MIN_NUM_FORMS": "0",
                "lineas_compra-MAX_NUM_FORMS": "1000",
                "lineas_compra-0-producto": str(self.producto.id),
                "lineas_compra-0-cantidad": "3.00",
                "lineas_compra-0-costo_unitario": "75.00",
                "lineas_compra-0-comentario": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        compra = CompraInventario.objects.get(proveedor_nombre="Distribuidora Norte")
        self.assertEqual(compra.estado, "borrador")
        self.assertFalse(InventarioProducto.objects.filter(producto=self.producto).exists())
        self.assertFalse(MovimientoInventario.objects.filter(compra_documento=compra).exists())

    def test_editar_compra_borrador_permite_corregir_cantidad(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Demo",
            referencia_documento="REF-001",
            fecha_documento=date.today(),
            estado="borrador",
        )
        linea = LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("3.00"),
            costo_unitario=Decimal("70.00"),
        )

        response = self.client.post(
            reverse("editar_compra", args=[self.empresa.slug, compra.id]),
            {
                "proveedor_nombre": "Proveedor Demo",
                "referencia_documento": "REF-001-A",
                "fecha_documento": str(date.today()),
                "observacion": "Compra corregida",
                "estado": "borrador",
                "lineas_compra-TOTAL_FORMS": "1",
                "lineas_compra-INITIAL_FORMS": "1",
                "lineas_compra-MIN_NUM_FORMS": "0",
                "lineas_compra-MAX_NUM_FORMS": "1000",
                "lineas_compra-0-id": str(linea.id),
                "lineas_compra-0-producto": str(self.producto.id),
                "lineas_compra-0-cantidad": "8.00",
                "lineas_compra-0-costo_unitario": "75.00",
                "lineas_compra-0-comentario": "Cantidad corregida",
            },
        )

        self.assertEqual(response.status_code, 302)
        compra.refresh_from_db()
        linea.refresh_from_db()
        self.assertEqual(compra.referencia_documento, "REF-001-A")
        self.assertEqual(linea.cantidad, Decimal("8.00"))
        self.assertEqual(linea.costo_unitario, Decimal("75.00"))
        self.assertFalse(MovimientoInventario.objects.filter(compra_documento=compra).exists())

    def test_no_permite_editar_compra_aplicada(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Aplicado",
            referencia_documento="REF-AP-001",
            fecha_documento=date.today(),
            estado="aplicada",
        )

        response = self.client.get(
            reverse("editar_compra", args=[self.empresa.slug, compra.id]),
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("ver_compra", args=[self.empresa.slug, compra.id]),
        )
        self.assertContains(response, "No se puede editar una compra aplicada")

    def test_puede_aplicar_compra_borrador_despues(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Aplicar",
            referencia_documento="REF-APL-001",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("4.00"),
            costo_unitario=Decimal("85.00"),
        )

        response = self.client.post(
            reverse("aplicar_compra", args=[self.empresa.slug, compra.id]),
        )

        self.assertEqual(response.status_code, 302)
        compra.refresh_from_db()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(compra.estado, "aplicada")
        self.assertEqual(inventario.existencias, Decimal("4.00"))
        self.assertTrue(MovimientoInventario.objects.filter(compra_documento=compra, tipo="entrada_compra").exists())

    def test_aplicar_compra_ya_aplicada_no_duplica_movimiento(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Ya Aplicado",
            referencia_documento="REF-APL-002",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("2.00"),
            costo_unitario=Decimal("90.00"),
        )

        self.client.post(reverse("aplicar_compra", args=[self.empresa.slug, compra.id]))
        response = self.client.post(
            reverse("aplicar_compra", args=[self.empresa.slug, compra.id]),
            follow=True,
        )

        compra.refresh_from_db()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertRedirects(
            response,
            reverse("ver_compra", args=[self.empresa.slug, compra.id]),
        )
        self.assertContains(response, "La compra ya estaba aplicada")
        self.assertEqual(compra.estado, "aplicada")
        self.assertEqual(inventario.existencias, Decimal("2.00"))
        self.assertEqual(MovimientoInventario.objects.filter(compra_documento=compra, tipo="entrada_compra").count(), 1)

    def test_anular_compra_borrador_cambia_estado_sin_mover_inventario(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Borrador",
            referencia_documento="REF-AN-001",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("3.00"),
            costo_unitario=Decimal("50.00"),
        )

        response = self.client.post(
            reverse("anular_compra", args=[self.empresa.slug, compra.id]),
        )

        self.assertEqual(response.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.estado, "anulada")
        self.assertFalse(InventarioProducto.objects.filter(producto=self.producto).exists())
        self.assertFalse(MovimientoInventario.objects.filter(compra_documento=compra).exists())

    def test_anular_compra_aplicada_revierte_inventario(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Revertir",
            referencia_documento="REF-AN-002",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("6.00"),
            costo_unitario=Decimal("65.00"),
        )
        self.client.post(reverse("aplicar_compra", args=[self.empresa.slug, compra.id]))

        response = self.client.post(
            reverse("anular_compra", args=[self.empresa.slug, compra.id]),
        )

        self.assertEqual(response.status_code, 302)
        compra.refresh_from_db()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(compra.estado, "anulada")
        self.assertEqual(inventario.existencias, Decimal("0.00"))
        self.assertEqual(MovimientoInventario.objects.filter(compra_documento=compra, tipo="entrada_compra").count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(compra_documento=compra, tipo="reversion_compra").count(), 1)

    def test_anular_compra_ya_anulada_no_duplica_reversion(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor_nombre="Proveedor Ya Anulado",
            referencia_documento="REF-AN-003",
            fecha_documento=date.today(),
            estado="borrador",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("2.00"),
            costo_unitario=Decimal("70.00"),
        )
        self.client.post(reverse("aplicar_compra", args=[self.empresa.slug, compra.id]))
        self.client.post(reverse("anular_compra", args=[self.empresa.slug, compra.id]))

        response = self.client.post(
            reverse("anular_compra", args=[self.empresa.slug, compra.id]),
            follow=True,
        )

        compra.refresh_from_db()
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertRedirects(
            response,
            reverse("ver_compra", args=[self.empresa.slug, compra.id]),
        )
        self.assertContains(response, "La compra ya estaba anulada")
        self.assertEqual(compra.estado, "anulada")
        self.assertEqual(inventario.existencias, Decimal("0.00"))
        self.assertEqual(MovimientoInventario.objects.filter(compra_documento=compra, tipo="reversion_compra").count(), 1)

    def test_inventario_dashboard_muestra_alerta_stock_minimo(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("2.00"),
            stock_minimo=Decimal("2.00"),
        )

        response = self.client.get(reverse("inventario_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alertas de stock minimo")
        self.assertContains(response, self.producto.nombre)

    def test_editar_producto_facturacion(self):
        response = self.client.post(
            reverse("editar_producto_facturacion", args=[self.empresa.slug, self.producto.id]),
            {
                "nombre": "Producto Editado",
                "codigo": "PRD-001",
                "tipo_item": "producto",
                "unidad_medida": "caja",
                "descripcion": self.producto.descripcion or "",
                "precio": "300.00",
                "impuesto_predeterminado": str(self.impuesto.id),
                "activo": "on",
                "controla_inventario": "on",
            },
        )
        self.producto.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.producto.nombre, "Producto Editado")
        self.assertEqual(self.producto.codigo, "PRD-001")
        self.assertEqual(self.producto.unidad_medida, "caja")
        self.assertEqual(self.producto.impuesto_predeterminado, self.impuesto)
        self.assertEqual(self.producto.precio, Decimal("300.00"))

    def test_producto_servicio_no_puede_controlar_inventario(self):
        producto = Producto(
            empresa=self.empresa,
            nombre="Consultoria",
            tipo_item="servicio",
            unidad_medida="hora",
            precio=Decimal("500.00"),
            controla_inventario=True,
        )

        with self.assertRaises(ValidationError):
            producto.save()

    def test_producto_no_repite_codigo_en_empresa(self):
        Producto.objects.create(
            empresa=self.empresa,
            nombre="Producto Codigo",
            codigo="COD-001",
            tipo_item="producto",
            unidad_medida="unidad",
            precio=Decimal("10.00"),
            controla_inventario=True,
        )
        producto = Producto(
            empresa=self.empresa,
            nombre="Producto Duplicado",
            codigo="cod-001",
            tipo_item="producto",
            unidad_medida="unidad",
            precio=Decimal("12.00"),
            controla_inventario=True,
        )

        with self.assertRaises(ValidationError):
            producto.save()

    def test_listado_cai_facturacion(self):
        response = self.client.get(reverse("cai_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cai.numero_cai)
        self.assertContains(response, self.cai_nota_credito.numero_cai)

    def test_listado_cai_facturacion_filtra_por_uso(self):
        response = self.client.get(
            reverse("cai_facturacion", args=[self.empresa.slug]),
            {"uso_documento": "nota_credito"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cai_nota_credito.numero_cai)
        self.assertNotContains(response, self.cai.numero_cai)

    def test_crear_cai_desde_facturacion(self):
        response = self.client.post(
            reverse("crear_cai_facturacion", args=[self.empresa.slug]),
            {
                "numero_cai": "CAI-NUEVO",
                "uso_documento": "factura",
                "establecimiento": "002",
                "punto_emision": "001",
                "tipo_documento": "01",
                "rango_inicial": "11",
                "rango_final": "20",
                "correlativo_actual": "10",
                "fecha_activacion": str(date.today()),
                "fecha_limite": str(date.today() + timedelta(days=60)),
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(CAI.objects.filter(empresa=self.empresa, numero_cai="CAI-NUEVO").exists())

    def test_crear_cai_desde_facturacion_acepta_formato_fecha_latino(self):
        response = self.client.post(
            reverse("crear_cai_facturacion", args=[self.empresa.slug]),
            {
                "numero_cai": "CAI-FECHA-LATAM",
                "uso_documento": "factura",
                "establecimiento": "007",
                "punto_emision": "001",
                "tipo_documento": "01",
                "rango_inicial": "71",
                "rango_final": "80",
                "correlativo_actual": "70",
                "fecha_activacion": "12/05/2022",
                "fecha_limite": "12/05/2023",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        cai = CAI.objects.get(empresa=self.empresa, numero_cai="CAI-FECHA-LATAM")
        self.assertEqual(cai.fecha_activacion, date(2022, 5, 12))
        self.assertEqual(cai.fecha_limite, date(2023, 5, 12))

    def test_factura_historica_sin_permiso_sigue_usando_cai_vigente_actual(self):
        cai_historico = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-2024",
            uso_documento="factura",
            establecimiento="003",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=30,
            rango_final=40,
            correlativo_actual=29,
            fecha_activacion=date(2024, 1, 1),
            fecha_limite=date(2024, 12, 31),
            activo=True,
        )

        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado="borrador",
            fecha_emision=date(2024, 1, 15),
        )
        LineaFactura.objects.create(
            factura=factura,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        factura.calcular_totales()
        factura.estado = "emitida"

        factura.save()

        self.assertEqual(factura.cai, self.cai)
        self.assertEqual(factura.cai_numero_historico, self.cai.numero_cai)
        cai_historico.refresh_from_db()
        self.assertEqual(cai_historico.correlativo_actual, 29)

    def test_factura_historica_puede_usarse_con_cai_vencido_si_empresa_tiene_permiso(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])

        cai_historico = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-2024",
            uso_documento="factura",
            establecimiento="003",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=30,
            rango_final=40,
            correlativo_actual=29,
            fecha_activacion=date(2024, 1, 1),
            fecha_limite=date(2024, 12, 31),
            activo=True,
        )

        cai_2025 = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-2025",
            uso_documento="factura",
            establecimiento="004",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=41,
            rango_final=50,
            correlativo_actual=40,
            fecha_activacion=date(2025, 1, 1),
            fecha_limite=date(2025, 12, 31),
            activo=True,
        )

        factura = self.crear_factura_con_linea(
            estado="borrador",
            fecha_emision=date(2024, 1, 15),
        )
        factura.estado = "emitida"
        factura.save()

        self.assertEqual(factura.cai, cai_historico)
        self.assertEqual(factura.numero_factura, "003-001-01-00000030")
        self.assertEqual(factura.cai_fecha_limite_historico, date(2024, 12, 31))

        factura_2025 = self.crear_factura_con_linea(
            estado="borrador",
            fecha_emision=date(2025, 7, 10),
        )
        factura_2025.estado = "emitida"
        factura_2025.save()

        self.assertEqual(factura_2025.cai, cai_2025)
        self.assertEqual(factura_2025.numero_factura, "004-001-01-00000041")

    def test_factura_historica_no_usa_cai_antes_de_su_fecha_activacion(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])

        cai_2025_vigente = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-2025-VIG",
            uso_documento="factura",
            establecimiento="006",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=61,
            rango_final=70,
            correlativo_actual=60,
            fecha_activacion=date(2025, 1, 1),
            fecha_limite=date(2025, 5, 31),
            activo=True,
        )

        cai_programado = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-PROG-2025",
            uso_documento="factura",
            establecimiento="005",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=51,
            rango_final=60,
            correlativo_actual=50,
            fecha_activacion=date(2025, 6, 1),
            fecha_limite=date(2025, 12, 31),
            activo=True,
        )

        factura = self.crear_factura_con_linea(
            estado="borrador",
            fecha_emision=date(2025, 3, 10),
        )
        factura.estado = "emitida"
        factura.save()

        self.assertNotEqual(factura.cai, cai_programado)
        self.assertEqual(factura.cai, cai_2025_vigente)

    def test_factura_conserva_snapshot_del_cai_historico(self):
        factura_antigua = self.crear_factura_con_linea(estado="emitida")
        self.cai.activo = False
        self.cai.save(update_fields=["activo"])

        cai_nuevo = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-2026",
            uso_documento="factura",
            establecimiento="002",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=11,
            rango_final=20,
            correlativo_actual=10,
            fecha_limite=date.today() + timedelta(days=90),
            activo=True,
        )

        factura_nueva = self.crear_factura_con_linea(estado="emitida")

        self.assertEqual(factura_antigua.cai_numero_historico, "CAI-TEST")
        self.assertEqual(factura_antigua.cai_establecimiento_historico, "001")
        self.assertEqual(factura_nueva.cai_numero_historico, cai_nuevo.numero_cai)
        self.assertEqual(factura_antigua.cai_numero_historico, factura_antigua.cai_numero)

    def test_no_permite_editar_datos_sensibles_de_cai_usado(self):
        self.crear_factura_con_linea(estado="emitida")
        self.cai.numero_cai = "CAI-CAMBIADO"

        with self.assertRaises(ValidationError):
            self.cai.save()

    def test_no_permite_editar_cai_usado_en_nota_credito_emitida(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras", "estado"])
        self.cai_nota_credito.numero_cai = "CAI-NC-CAMBIADO"

        with self.assertRaises(ValidationError):
            self.cai_nota_credito.save()

    def test_nota_credito_emitida_usa_cai_propio(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras", "estado"])

        self.assertEqual(nota.cai, self.cai_nota_credito)
        self.assertEqual(nota.cai_numero_historico, "CAI-NC")

    def test_listado_impuestos_facturacion(self):
        response = self.client.get(reverse("impuestos_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.impuesto.nombre)

    def test_crear_impuesto_desde_facturacion(self):
        response = self.client.post(
            reverse("crear_impuesto_facturacion", args=[self.empresa.slug]),
            {
                "nombre": "ISV 18",
                "porcentaje": "18.00",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(TipoImpuesto.objects.filter(nombre="ISV 18").exists())

    def test_generar_nota_credito_desde_factura_copia_lineas(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.post(
            reverse("generar_nota_credito_desde_factura", args=[self.empresa.slug, factura.id])
        )

        nota = NotaCredito.objects.get(factura_origen=factura)

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("editar_nota_credito", args=[self.empresa.slug, nota.id]))
        self.assertEqual(nota.estado, "borrador")
        self.assertEqual(nota.lineas.count(), factura.lineas.count())
        self.assertEqual(nota.total, factura.total)

    def test_detalle_factura_para_nota_credito_devuelve_lineas(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.get(
            reverse("detalle_factura_para_nota_credito", args=[self.empresa.slug, factura.id])
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["factura_id"], factura.id)
        self.assertEqual(data["cliente"], factura.cliente.nombre)
        self.assertEqual(len(data["lineas"]), 1)
        self.assertEqual(data["lineas"][0]["producto_id"], self.producto.id)

    def test_nota_credito_emitida_reduce_saldo_y_limita_pago(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("0.50"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras", "estado"])
        factura.refresh_from_db()
        factura.actualizar_estado_pago()
        factura.refresh_from_db()

        self.assertEqual(factura.total_notas_credito, Decimal("57.50"))
        self.assertEqual(factura.total_documento_ajustado, Decimal("57.50"))
        self.assertEqual(factura.saldo_pendiente, Decimal("57.50"))

        with self.assertRaises(ValidationError):
            PagoFactura.objects.create(
                factura=factura,
                monto=Decimal("58.00"),
                metodo="efectivo",
                fecha=date.today(),
            )

    def test_crear_factura_emitida_descuenta_inventario(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("10.00"),
            stock_minimo=Decimal("1.00"),
        )

        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": str(date.today()),
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "2.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("8.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.producto, tipo="salida_factura").exists())

    def test_crear_factura_muestra_buscadores_de_cliente_y_producto(self):
        response = self.client.get(reverse("crear_factura", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscar cliente por nombre")
        self.assertContains(response, 'id="clientes-sugerencias"', html=False)
        self.assertContains(response, "Buscar producto")

    def test_crear_factura_usd_acepta_tipo_cambio_con_cuatro_decimales(self):
        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": str(date.today()),
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "26.7348",
                "moneda": "USD",
                "estado": "borrador",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "1.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        factura = Factura.objects.latest("id")
        self.assertEqual(factura.moneda, "USD")
        self.assertEqual(factura.tipo_cambio, Decimal("26.7348"))

    def test_total_en_letras_conserva_centavos_en_usd(self):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            moneda="USD",
            tipo_cambio=Decimal("26.7348"),
            estado="borrador",
        )
        factura.total = Decimal("4004.80")

        self.assertEqual(
            factura.total_en_letras(),
            "SON: CUATRO MIL CUATRO DOLARES CON 80/100",
        )

    def test_crear_factura_normal_no_muestra_numero_manual(self):
        response = self.client.get(reverse("crear_factura", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("numero_factura", response.context["form"].fields)

    def test_empresa_historica_muestra_y_acepta_numero_factura_manual(self):
        self.empresa.nombre = "AMKT Digital"
        self.empresa.slug = "amkt-digital"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("10.00"),
            stock_minimo=Decimal("1.00"),
        )
        self.cai.fecha_activacion = date(2026, 1, 1)
        self.cai.fecha_limite = date(2026, 12, 31)
        self.cai.correlativo_actual = 0
        self.cai.save(update_fields=["fecha_activacion", "fecha_limite", "correlativo_actual"])

        response = self.client.get(reverse("crear_factura", args=[self.empresa.slug]))
        self.assertIn("numero_factura", response.context["form"].fields)
        self.assertIn("numero_factura_sufijo", response.context["form"].fields)
        self.assertContains(response, "001-001-01-00000")

        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": "22/04/2026",
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "numero_factura_sufijo": "005",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "2.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        factura = Factura.objects.latest("id")
        self.cai.refresh_from_db()
        self.assertEqual(factura.numero_factura, "001-001-01-00000005")
        self.assertEqual(factura.cai_id, self.cai.id)
        self.assertEqual(self.cai.correlativo_actual, 0)

        factura_auto = self.crear_factura_con_linea(estado="emitida", fecha_emision=date(2026, 4, 23))
        self.assertEqual(factura_auto.numero_factura, "001-001-01-00000001")

    def test_factura_historica_manual_no_empuja_correlativo_actual(self):
        self.empresa.nombre = "Digital Planning"
        self.empresa.slug = "digital_planning"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        self.cai.rango_inicial = 276
        self.cai.rango_final = 1275
        self.cai.correlativo_actual = 330
        self.cai.save(update_fields=["rango_inicial", "rango_final", "correlativo_actual"])

        factura = self.crear_factura_con_linea(estado="borrador", fecha_emision=date(2026, 4, 22))
        factura.estado = "emitida"
        factura.numero_factura = "001-001-01-00000380"
        factura.save()

        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 330)

        factura_nueva = self.crear_factura_con_linea(estado="emitida", fecha_emision=date(2026, 5, 16))
        self.assertEqual(factura_nueva.numero_factura, "001-001-01-00000331")

    def test_factura_automatica_salta_numeros_historicos_ya_usados(self):
        self.empresa.nombre = "Digital Planning"
        self.empresa.slug = "digital_planning"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        self.cai.rango_inicial = 276
        self.cai.rango_final = 1275
        self.cai.correlativo_actual = 379
        self.cai.save(update_fields=["rango_inicial", "rango_final", "correlativo_actual"])

        factura_historica = self.crear_factura_con_linea(estado="borrador", fecha_emision=date(2026, 4, 22))
        factura_historica.estado = "emitida"
        factura_historica.numero_factura = "001-001-01-00000381"
        factura_historica.save()

        factura_380 = self.crear_factura_con_linea(estado="emitida", fecha_emision=date(2026, 5, 16))
        self.assertEqual(factura_380.numero_factura, "001-001-01-00000380")

        factura_382 = self.crear_factura_con_linea(estado="emitida", fecha_emision=date(2026, 5, 17))
        self.assertEqual(factura_382.numero_factura, "001-001-01-00000382")

    def test_empresa_historica_bloquea_numero_manual_fuera_de_cai(self):
        self.empresa.nombre = "INTEGRATED SALES AND SERVICES S. DE R.L."
        self.empresa.slug = "integrated-sales-and-services"
        self.empresa.save(update_fields=["nombre", "slug"])
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("10.00"),
            stock_minimo=Decimal("1.00"),
        )
        self.cai.fecha_activacion = date(2026, 1, 1)
        self.cai.fecha_limite = date(2026, 12, 31)
        self.cai.rango_final = 10
        self.cai.save(update_fields=["fecha_activacion", "fecha_limite", "rango_final"])

        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": "22/04/2026",
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "numero_factura_sufijo": "025",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "1.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No existe un CAI que cubra este numero para la fecha de la factura.")
        self.assertFalse(Factura.objects.exists())

    def test_puede_crear_factura_con_descripcion_manual_sin_producto(self):
        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": "22/04/2026",
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": "",
                "lineas-0-descripcion_manual": "4 reels, 6 stories, 7 carrusel, 7 post estaticos",
                "lineas-0-cantidad": "1.00",
                "lineas-0-precio_unitario": "5000.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        factura = Factura.objects.latest("id")
        linea = factura.lineas.get()
        self.assertIsNone(linea.producto)
        self.assertEqual(linea.descripcion_manual, "4 reels, 6 stories, 7 carrusel, 7 post estaticos")
        self.assertEqual(linea.descripcion_visual, "4 reels, 6 stories, 7 carrusel, 7 post estaticos")

    def test_no_permite_emitir_factura_si_no_hay_stock_suficiente(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("1.00"),
            stock_minimo=Decimal("1.00"),
        )

        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": str(date.today()),
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "2.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuficiente para emitir la factura")
        self.assertEqual(Factura.objects.count(), 0)
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("1.00"))
        self.assertFalse(MovimientoInventario.objects.filter(producto=self.producto, tipo="salida_factura").exists())

    def test_si_permite_guardar_factura_borrador_aunque_no_haya_stock(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("1.00"),
            stock_minimo=Decimal("1.00"),
        )

        response = self.client.post(
            reverse("crear_factura", args=[self.empresa.slug]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": str(date.today()),
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "borrador",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "2.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        factura = Factura.objects.get()
        self.assertEqual(factura.estado, "borrador")
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("1.00"))
        self.assertFalse(MovimientoInventario.objects.filter(producto=self.producto, tipo="salida_factura").exists())

    def test_no_permite_cambiar_borrador_a_emitida_si_no_hay_stock(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("1.00"),
            stock_minimo=Decimal("1.00"),
        )
        factura = self.crear_factura_con_linea(estado="borrador")
        factura.cai = None
        factura.numero_factura = None
        factura.save(update_fields=["cai", "numero_factura"])

        response = self.client.post(
            reverse("editar_factura", args=[self.empresa.slug, factura.id]),
            {
                "cliente": str(self.cliente.id),
                "fecha_emision": str(factura.fecha_emision),
                "fecha_vencimiento": "",
                "vendedor": "",
                "tipo_cambio": "1.0000",
                "moneda": "HNL",
                "estado": "emitida",
                "orden_compra_exenta": "",
                "registro_exonerado": "",
                "registro_sag": "",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "1",
                "lineas-MIN_NUM_FORMS": "0",
                "lineas-MAX_NUM_FORMS": "1000",
                "lineas-0-id": str(factura.lineas.first().id),
                "lineas-0-producto": str(self.producto.id),
                "lineas-0-cantidad": "2.00",
                "lineas-0-precio_unitario": "100.00",
                "lineas-0-descuento_porcentaje": "0",
                "lineas-0-comentario": "",
                "lineas-0-impuesto": str(self.impuesto.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuficiente para emitir la factura")
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")
        self.assertIsNone(factura.numero_factura)
        inventario = InventarioProducto.objects.get(producto=self.producto)
        self.assertEqual(inventario.existencias, Decimal("1.00"))
        self.assertFalse(MovimientoInventario.objects.filter(producto=self.producto, tipo="salida_factura").exists())

    def test_no_permite_editar_factura_emitida_con_pagos(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        PagoFactura.objects.create(
            factura=factura,
            monto=Decimal("25.00"),
            metodo="efectivo",
            fecha=date.today(),
        )

        response = self.client.get(
            reverse("editar_factura", args=[self.empresa.slug, factura.id]),
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("ver_factura", args=[self.empresa.slug, factura.id]),
        )
        self.assertContains(response, "No se puede editar esta factura emitida")
        self.assertContains(response, "pagos registrados")

    def test_no_permite_editar_factura_emitida_con_nota_credito(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )

        response = self.client.get(
            reverse("editar_factura", args=[self.empresa.slug, factura.id]),
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("ver_factura", args=[self.empresa.slug, factura.id]),
        )
        self.assertContains(response, "No se puede editar esta factura emitida")
        self.assertContains(response, "notas de crédito")

    def test_nota_credito_emitida_devuelve_inventario(self):
        InventarioProducto.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            existencias=Decimal("10.00"),
            stock_minimo=Decimal("1.00"),
        )
        factura = self.crear_factura_con_linea(estado="emitida")
        inventario = InventarioProducto.objects.get(producto=self.producto)
        inventario.existencias = Decimal("9.00")
        inventario.save(update_fields=["existencias", "fecha_actualizacion"])
        MovimientoInventario.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            tipo="salida_factura",
            cantidad=Decimal("1.00"),
            existencia_anterior=Decimal("10.00"),
            existencia_resultante=Decimal("9.00"),
            referencia=factura.numero_factura or f"Factura {factura.id}",
            factura=factura,
        )
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"
        nota.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras", "estado"])
        _registrar_entrada_nota_credito(nota)

        inventario.refresh_from_db()
        self.assertEqual(inventario.existencias, Decimal("10.00"))
        self.assertTrue(MovimientoInventario.objects.filter(producto=self.producto, tipo="devolucion_nota_credito").exists())

    def test_crear_nota_credito_tiene_buscador_con_sugerencias_de_factura(self):
        factura = self.crear_factura_con_linea(estado="emitida")

        response = self.client.get(reverse("crear_nota_credito", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="facturas-origen-sugeridas"', html=False)
        self.assertContains(response, factura.numero_factura or str(factura.id))

    def test_nota_credito_emitida_no_puede_exceder_total_factura(self):
        factura = self.crear_factura_con_linea(estado="emitida")
        nota = NotaCredito.objects.create(
            empresa=self.empresa,
            factura_origen=factura,
            cliente=factura.cliente,
            moneda=factura.moneda,
            tipo_cambio=factura.tipo_cambio,
            fecha_emision=date.today(),
            estado="borrador",
        )
        LineaNotaCredito.objects.create(
            nota_credito=nota,
            producto=self.producto,
            cantidad=Decimal("2.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        nota.calcular_totales()
        nota.estado = "emitida"

        with self.assertRaises(ValidationError):
            nota.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras", "estado"])

    def test_crear_cliente_respeta_next_seguro(self):
        next_url = reverse("crear_factura", args=[self.empresa.slug])
        response = self.client.post(
            f"{reverse('crear_cliente_facturacion', args=[self.empresa.slug])}?next={next_url}",
            {
                "nombre": "Cliente Con Next",
                "rtn": "08011999222222",
                "direccion": "Tegucigalpa",
                "ciudad": "Tegucigalpa",
                "activo": "on",
            },
        )

        self.assertRedirects(response, next_url)

    def test_pago_compra_no_puede_superar_saldo(self):
        compra = self.crear_compra_con_linea()

        with self.assertRaises(ValidationError):
            PagoCompra.objects.create(
                compra=compra,
                fecha=date.today(),
                monto=compra.total_documento + Decimal("1.00"),
                metodo="transferencia",
            )

    def test_no_permite_pago_en_compra_borrador(self):
        compra = self.crear_compra_con_linea(estado="borrador")

        response = self.client.get(
            reverse("registrar_pago_compra", args=[self.empresa.slug, compra.id]),
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse("ver_compra", args=[self.empresa.slug, compra.id]),
        )
        self.assertContains(response, "aun esta en borrador")

    def test_pago_compra_reduce_saldo_pendiente(self):
        compra = self.crear_compra_con_linea()
        cuenta_banco = CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="110299",
            nombre="Banco Pagos Proveedores",
            tipo="activo",
        )
        cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=self.empresa,
            nombre="Banco Pagos Proveedores",
            tipo="banco",
            cuenta_contable=cuenta_banco,
        )

        response = self.client.post(
            reverse("registrar_pago_compra", args=[self.empresa.slug, compra.id]),
            {
                "fecha": str(date.today()),
                "monto": "60.00",
                "metodo": "transferencia",
                "cuenta_financiera": str(cuenta_financiera.id),
                "referencia": "TRX-001",
                "observacion": "Primer abono",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        compra.refresh_from_db()
        self.assertEqual(compra.total_pagado, Decimal("60.00"))
        self.assertEqual(compra.saldo_pendiente, compra.total_documento - Decimal("60.00"))
        self.assertContains(response, "Pago de compra registrado correctamente")
        self.assertTrue(ComprobanteEgresoCompra.objects.filter(pago__compra=compra).exists())

    def test_reporte_cxp_muestra_proveedor_y_compras_pendientes(self):
        compra = self.crear_compra_con_linea()

        response = self.client.get(reverse("reporte_cxp", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, compra.proveedor_nombre)
        self.assertContains(response, "Cuentas por Pagar")

        detalle = self.client.get(
            reverse("reporte_cxp", args=[self.empresa.slug]) + f"?proveedor={compra.proveedor.id}"
        )
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, compra.numero_compra)
        self.assertContains(detalle, "Registrar Pago")

    def test_reporte_cxp_permita_detalle_por_nombre_si_no_hay_fk_proveedor(self):
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor=None,
            proveedor_nombre="Proveedor Legacy",
            fecha_documento=date.today(),
            estado="aplicada",
        )
        LineaCompraInventario.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=Decimal("2.00"),
            costo_unitario=Decimal("25.00"),
        )

        response = self.client.get(
            reverse("reporte_cxp", args=[self.empresa.slug]) + "?proveedor_key=manual-proveedor-legacy"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proveedor Legacy")
        self.assertContains(response, compra.numero_compra)

    def test_pago_compra_genera_comprobante_egreso(self):
        compra = self.crear_compra_con_linea()
        pago = PagoCompra.objects.create(
            compra=compra,
            fecha=date.today(),
            monto=Decimal("40.00"),
            metodo="efectivo",
            referencia="CAJA-01",
            observacion="Pago parcial",
        )

        self.assertTrue(ComprobanteEgresoCompra.objects.filter(pago=pago).exists())
        comprobante = pago.comprobante
        self.assertEqual(comprobante.compra, compra)
        self.assertEqual(comprobante.proveedor_nombre, compra.proveedor_nombre)
        self.assertEqual(comprobante.monto, Decimal("40.00"))

    def test_proveedor_contado_normaliza_dias_credito(self):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Contado",
            condicion_pago="contado",
            dias_credito=30,
        )

        self.assertEqual(proveedor.dias_credito, 0)

    def test_compra_credito_calcula_fecha_vencimiento(self):
        compra = self.crear_compra_con_linea(condicion_pago="credito", dias_credito=15)

        self.assertEqual(compra.fecha_vencimiento, date.today() + timedelta(days=15))

    def test_reporte_cxp_usa_fecha_vencimiento_para_antiguedad(self):
        compra = self.crear_compra_con_linea(condicion_pago="credito", dias_credito=15)
        compra.fecha_documento = date.today() - timedelta(days=45)
        compra.fecha_vencimiento = date.today() + timedelta(days=2)
        compra.save(update_fields=["fecha_documento", "fecha_vencimiento"])

        response = self.client.get(reverse("reporte_cxp", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "L. 120.00")

    def test_egresos_dashboard_muestra_comprobante(self):
        compra = self.crear_compra_con_linea()
        pago = PagoCompra.objects.create(
            compra=compra,
            fecha=date.today(),
            monto=Decimal("30.00"),
            metodo="transferencia",
            referencia="TRX-EG-01",
        )

        response = self.client.get(reverse("egresos_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pago.comprobante.numero_comprobante)
        self.assertContains(response, compra.proveedor_nombre)

    def test_ver_compra_muestra_boton_pagar_cuando_hay_saldo(self):
        compra = self.crear_compra_con_linea()

        response = self.client.get(reverse("ver_compra", args=[self.empresa.slug, compra.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar Pago")

    def test_revertir_pago_compra_elimina_comprobante_y_restaurar_saldo(self):
        compra = self.crear_compra_con_linea()
        pago = PagoCompra.objects.create(
            compra=compra,
            fecha=date.today(),
            monto=Decimal("30.00"),
            metodo="efectivo",
        )

        response = self.client.post(
            reverse("revertir_pago_compra", args=[self.empresa.slug, compra.id, pago.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PagoCompra.objects.filter(id=pago.id).exists())
        self.assertFalse(ComprobanteEgresoCompra.objects.filter(compra=compra).exists())
        compra.refresh_from_db()
        self.assertEqual(compra.total_pagado, Decimal("0.00"))
        self.assertContains(response, "Pago de compra revertido correctamente")

    def test_filtro_compras_por_busqueda_estado_y_pago(self):
        compra = self.crear_compra_con_linea()
        PagoCompra.objects.create(
            compra=compra,
            fecha=date.today(),
            monto=Decimal("10.00"),
            metodo="transferencia",
        )

        response = self.client.get(
            reverse("compras_dashboard", args=[self.empresa.slug]),
            {"q": compra.numero_compra, "estado": "aplicada", "pago": "parcial"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, compra.numero_compra)

    def test_filtro_proveedores_por_busqueda_y_estado(self):
        proveedor = Proveedor.objects.create(
            empresa=self.empresa,
            nombre="Proveedor Busqueda",
            activo=False,
        )

        response = self.client.get(
            reverse("proveedores_facturacion", args=[self.empresa.slug]),
            {"q": "Busqueda", "estado": "inactivos"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, proveedor.nombre)

    def test_filtro_egresos_por_busqueda_y_metodo(self):
        compra = self.crear_compra_con_linea()
        pago = PagoCompra.objects.create(
            compra=compra,
            fecha=date.today(),
            monto=Decimal("35.00"),
            metodo="transferencia",
            referencia="TRX-FILTRO",
        )

        response = self.client.get(
            reverse("egresos_dashboard", args=[self.empresa.slug]),
            {"q": "TRX-FILTRO", "metodo": "transferencia"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pago.comprobante.numero_comprobante)

    def test_reportes_muestra_iframe_power_bi_cuando_esta_configurado(self):
        ConfiguracionPowerBIEmpresa.objects.create(
            empresa=self.empresa,
            activo=True,
            mostrar_en_reportes=True,
            titulo_panel="Panel de Gerencia",
            descripcion_panel="Resumen ejecutivo conectado a Power BI.",
            url_embed="https://app.powerbi.com/reportEmbed?reportId=demo-report",
            alto_iframe=640,
        )

        response = self.client.get(reverse("reportes_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel de Gerencia")
        self.assertContains(response, "reportEmbed?reportId=demo-report")
        self.assertContains(response, "<iframe", html=False)

    def test_administrador_empresa_puede_guardar_configuracion_power_bi(self):
        self.user.es_administrador_empresa = True
        self.user.save(update_fields=["es_administrador_empresa"])

        response = self.client.post(
            reverse("configuracion_power_bi_reportes", args=[self.empresa.slug]),
            {
                "activo": "on",
                "mostrar_en_reportes": "on",
                "titulo_panel": "Indicadores ejecutivos",
                "descripcion_panel": "Vista consolidada para direccion.",
                "url_embed": "https://app.powerbi.com/reportEmbed?reportId=panel-seguro",
                "alto_iframe": "700",
                "workspace_id": "workspace-demo",
                "report_id": "report-demo",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Configuracion Power BI actualizada correctamente.")

        configuracion = ConfiguracionPowerBIEmpresa.objects.get(empresa=self.empresa)
        self.assertTrue(configuracion.activo)
        self.assertEqual(configuracion.titulo_panel, "Indicadores ejecutivos")
        self.assertEqual(configuracion.url_embed, "https://app.powerbi.com/reportEmbed?reportId=panel-seguro")

    def test_reportes_muestra_bi_interno_con_clientes_y_productos(self):
        self.crear_factura_con_linea(estado="emitida")

        response = self.client.get(reverse("reportes_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BI interno de facturacion")
        self.assertContains(response, "Clientes con mayor facturacion")
        self.assertContains(response, self.cliente.nombre)
        self.assertContains(response, self.producto.nombre)

    def test_dashboard_bi_facturacion_renderiza_panel_ejecutivo(self):
        self.crear_factura_con_linea(estado="emitida")

        response = self.client.get(reverse("dashboard_bi_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard BI de Facturación")
        self.assertContains(response, "Lectura inteligente de la facturación en una sola vista.")
        self.assertContains(response, "Productos más vendidos")
        self.assertContains(response, self.cliente.nombre)

    def test_dashboard_bi_facturacion_muestra_bancos_y_cartera_vencida(self):
        cuenta_banco = CuentaContable.objects.create(
            empresa=self.empresa,
            codigo="110210",
            nombre="Banco Comercial",
            tipo="activo",
            acepta_movimientos=True,
        )
        cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=self.empresa,
            nombre="Banco Atlántida HNL",
            tipo="banco",
            cuenta_contable=cuenta_banco,
        )
        factura_vencida = self.crear_factura_con_linea(
            estado="emitida",
            fecha_emision=date.today() - timedelta(days=45),
        )
        factura_vencida.fecha_vencimiento = date.today() - timedelta(days=15)
        factura_vencida.save(update_fields=["fecha_vencimiento"])
        PagoFactura.objects.create(
            factura=factura_vencida,
            monto=Decimal("25.00"),
            metodo="transferencia",
            fecha=date.today() - timedelta(days=3),
            cuenta_financiera=cuenta_financiera,
        )

        response = self.client.get(reverse("dashboard_bi_facturacion", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cobrado vs facturado")
        self.assertContains(response, "Ingresos por banco y caja")
        self.assertContains(response, "Clientes con saldo vencido")
        self.assertContains(response, "Banco Atlántida HNL")
        self.assertContains(response, self.cliente.nombre)
