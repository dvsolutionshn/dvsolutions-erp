"""Cobros netos cuya comisión queda liquidada por compensación con el cliente."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import (
    AsientoContable,
    CuentaContable,
    CuentaFinanciera,
    LineaAsientoContable,
    MovimientoBancario,
    PeriodoContable,
)
from contabilidad.services import registrar_asiento_pago_cliente, registrar_asiento_pago_comision_proveedor
from core.models import Empresa

from .models import Factura, LineaFactura, PagoComisionProveedor, PagoFactura, Proveedor, ReciboPago
from . import tests as pruebas_base


class ComisionRetenidaClienteTests(TestCase):
    # Reutiliza las relaciones/permisos de facturación sin heredar sus pruebas.
    setUp = pruebas_base.FacturacionTests.setUp

    TOTAL = Decimal("306503.75")
    COMISION = Decimal("76625.94")
    NETO = Decimal("229877.81")

    def escenario(self):
        self.empresa.slug = "digital_planning"
        self.empresa.save(update_fields=["slug"])
        banco_contable = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110298", nombre="Banco cobro neto", tipo="activo",
        )
        self.banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco cobro neto", tipo="banco", cuenta_contable=banco_contable,
        )
        self.proveedor = Proveedor.objects.create(
            empresa=self.empresa, nombre="Proveedor de la comisión retenida", rtn="08011999555555",
        )
        self.factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, estado="emitida", fecha_emision=date.today(),
        )
        LineaFactura.objects.create(
            factura=self.factura, producto=self.producto, cantidad=Decimal("1.00"),
            precio_unitario=Decimal("266525.00"), impuesto=self.impuesto,
        )
        self.factura.calcular_totales()
        self.factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        self.assertEqual(self.factura.total, self.TOTAL)
        return self.factura

    def datos(self, **cambios):
        datos = {
            "fecha": str(date.today()),
            "monto": str(self.NETO),
            "metodo": "transferencia",
            "cuenta_financiera": str(self.banco.pk),
            "referencia": "DP-COMISION-RETENIDA",
            "proveedor_comision": str(self.proveedor.pk),
            "porcentaje_comision": "25.00",
            "comision_retenida_cliente": "on",
        }
        datos.update(cambios)
        return datos

    def registrar(self, **cambios):
        return self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), self.datos(**cambios),
        )

    def editar(self, pago, **cambios):
        return self.client.post(
            reverse("editar_pago_factura", args=[self.empresa.slug, self.factura.pk, pago.pk]),
            self.datos(**cambios),
        )

    def lineas_cobro(self, pago):
        compensaciones = PagoComisionProveedor.objects.filter(comision_origen=pago).values("pk")
        return LineaAsientoContable.objects.filter(asiento__empresa=self.empresa).filter(
            Q(asiento__documento_tipo="pago_factura", asiento__documento_id=pago.pk)
            | Q(asiento__documento_tipo="pago_comision_proveedor", asiento__documento_id__in=compensaciones)
        )

    def importe(self, lineas, campo):
        return lineas.aggregate(total=Sum(campo))["total"] or Decimal("0.00")

    def assert_liquidacion(self, pago):
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.cliente.refresh_from_db()
        self.proveedor.refresh_from_db()
        self.assertTrue(pago.comision_retenida_cliente)
        self.assertEqual(pago.monto, self.NETO)
        self.assertEqual(pago.total_aplicado, self.TOTAL)
        self.assertEqual(pago.recibo.monto, self.NETO)
        self.assertEqual(pago.monto_comision, self.COMISION)
        self.assertEqual(pago.total_comision_pagada, self.COMISION)
        self.assertEqual(pago.saldo_comision_pendiente, Decimal("0.00"))
        self.assertEqual(self.factura.total_pagado, self.TOTAL)
        self.assertEqual(self.factura.estado_pago, "pagado")
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))

        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        self.assertEqual(compensacion.metodo, "compensacion")
        self.assertIsNone(compensacion.cuenta_financiera_id)
        self.assertEqual(compensacion.monto, self.COMISION)
        self.assertEqual(compensacion.proveedor, self.proveedor)
        self.assertEqual(compensacion.fecha, pago.fecha)
        lineas = self.lineas_cobro(pago)
        banco = lineas.filter(cuenta=self.banco.cuenta_contable)
        self.assertEqual(self.importe(banco, "debe"), self.NETO)
        self.assertEqual(self.importe(banco, "haber"), Decimal("0.00"))
        cliente = lineas.filter(cuenta=self.cliente.cuenta_contable)
        self.assertEqual(self.importe(cliente, "haber") - self.importe(cliente, "debe"), self.TOTAL)
        proveedor = lineas.filter(cuenta=self.proveedor.cuenta_contable)
        self.assertEqual(self.importe(proveedor, "debe"), self.COMISION)
        self.assertEqual(self.importe(proveedor, "haber"), self.COMISION)
        gastos = lineas.filter(cuenta__codigo="610302")
        self.assertEqual(self.importe(gastos, "debe"), self.COMISION)
        self.assertEqual(self.importe(lineas, "debe"), self.importe(lineas, "haber"))
        for asiento_id in lineas.values_list("asiento_id", flat=True).distinct():
            asiento = AsientoContable.objects.get(pk=asiento_id)
            self.assertEqual(asiento.total_debe, asiento.total_haber)
        return compensacion

    def test_cobro_neto_cancela_factura_y_proveedor_sin_egreso_bancario(self):
        self.escenario()
        response = self.registrar()
        self.assertEqual(response.status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = self.assert_liquidacion(pago)
        self.assertEqual(compensacion.registrado_por, self.user)
        self.assertEqual(PagoFactura.objects.filter(factura=self.factura).count(), 1)
        self.assertEqual(ReciboPago.objects.filter(pago=pago).count(), 1)

    def test_editar_y_recontabilizar_no_duplican_liquidacion(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        recibo_id = pago.recibo.pk
        for referencia in ("AJUSTE-REFERENCIA", "AJUSTE-REFERENCIA"):
            response = self.editar(pago, referencia=referencia)
            self.assertEqual(response.status_code, 302)
            self.assert_liquidacion(pago)
            self.assertEqual(pago.recibo.pk, recibo_id)
            self.assertEqual(pago.referencia, referencia)
        registrar_asiento_pago_cliente(pago)
        self.assert_liquidacion(pago)
        self.assertEqual(self.factura.pagos_facturacion.count(), 1)
        self.assertEqual(ReciboPago.objects.filter(pago=pago).count(), 1)

    def test_activar_compensacion_sobre_cobro_neto_previo_cierra_saldo(self):
        self.escenario()
        datos = self.datos()
        datos.pop("comision_retenida_cliente")
        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, self.COMISION)
        self.assertEqual(pago.saldo_comision_pendiente, self.COMISION)
        self.assertEqual(self.editar(pago).status_code, 302)
        self.assert_liquidacion(pago)

    def test_desactivar_compensacion_con_cobro_bruto_reabre_solo_comision_proveedor(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        recibo_id = pago.recibo.pk
        datos = self.datos(monto=str(self.TOTAL))
        datos.pop("comision_retenida_cliente")
        response = self.client.post(
            reverse("editar_pago_factura", args=[self.empresa.slug, self.factura.pk, pago.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertFalse(pago.comision_retenida_cliente)
        self.assertEqual(pago.monto, self.TOTAL)
        self.assertEqual(pago.total_aplicado, self.TOTAL)
        self.assertEqual(pago.recibo.pk, recibo_id)
        self.assertEqual(pago.recibo.monto, self.TOTAL)
        self.assertFalse(PagoComisionProveedor.objects.filter(comision_origen=pago).exists())
        self.assertEqual(pago.saldo_comision_pendiente, self.COMISION)
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        banco = self.lineas_cobro(pago).filter(cuenta=self.banco.cuenta_contable)
        self.assertEqual(self.importe(banco, "debe"), self.TOTAL)
        self.assertEqual(self.importe(banco, "haber"), Decimal("0.00"))

    def test_no_compensa_comision_que_ya_tiene_pagos_bancarios(self):
        self.escenario()
        datos = self.datos()
        datos.pop("comision_retenida_cliente")
        self.assertEqual(self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        ).status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        egreso = PagoComisionProveedor.objects.create(
            comision_origen=pago, monto=Decimal("1.00"), fecha=date.today(),
            cuenta_financiera=self.banco, metodo="transferencia", registrado_por=self.user,
        )
        registrar_asiento_pago_comision_proveedor(egreso)
        response = self.editar(pago)
        self.assertEqual(response.status_code, 200)
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertFalse(pago.comision_retenida_cliente)
        self.assertEqual(pago.total_comision_pagada, Decimal("1.00"))
        self.assertEqual(pago.saldo_comision_pendiente, self.COMISION - Decimal("1.00"))
        self.assertEqual(self.factura.saldo_pendiente, self.COMISION)
        self.assertEqual(PagoComisionProveedor.objects.get(comision_origen=pago).pk, egreso.pk)

    def test_comision_ordinaria_sigue_pendiente_y_exige_pago_financiero(self):
        self.escenario()
        datos = self.datos(monto=str(self.TOTAL))
        datos.pop("comision_retenida_cliente")
        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.assertFalse(pago.comision_retenida_cliente)
        self.assertEqual(pago.total_aplicado, self.TOTAL)
        self.assertEqual(pago.saldo_comision_pendiente, self.COMISION)
        self.assertFalse(PagoComisionProveedor.objects.filter(comision_origen=pago).exists())
        pago_proveedor = PagoComisionProveedor(
            comision_origen=pago, monto=self.COMISION, fecha=date.today(), metodo="transferencia",
        )
        with self.assertRaises(ValidationError):
            pago_proveedor.full_clean()

    def test_monto_bruto_con_retencion_de_comision_rechaza_exceso_sin_residuos(self):
        self.escenario()
        response = self.registrar(monto=str(self.TOTAL))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form_data"]["monto"], str(self.TOTAL))
        self.assertFalse(PagoFactura.objects.filter(factura=self.factura).exists())
        self.assertFalse(ReciboPago.objects.filter(factura=self.factura).exists())
        self.assertFalse(PagoComisionProveedor.objects.exists())
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, self.TOTAL)

    def test_pago_nuevo_posterior_respeta_aplicacion_previa_por_compensacion(self):
        self.escenario()
        self.assertEqual(self.registrar(monto="100000.00").status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.assertEqual(pago.total_aplicado, Decimal("176625.94"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("129877.81"))
        datos = self.datos(monto="129877.82", porcentaje_comision="0.00", proveedor_comision="")
        datos.pop("comision_retenida_cliente")
        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.factura.pagos_facturacion.count(), 1)
        datos["monto"] = "129877.81"
        self.assertEqual(self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        ).status_code, 302)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(self.factura.estado_pago, "pagado")

    def test_otras_empresas_rechazan_el_campo_en_post_y_modelo(self):
        self.escenario()
        self.empresa.slug = "demo"
        self.empresa.save(update_fields=["slug"])
        response = self.registrar()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PagoFactura.objects.filter(factura=self.factura).exists())
        pago = PagoFactura(
            factura=self.factura, monto=self.NETO, proveedor_comision=self.proveedor,
            monto_comision=self.COMISION, porcentaje_comision=25, comision_retenida_cliente=True,
            cuenta_financiera=self.banco, metodo="transferencia",
        )
        with self.assertRaises(ValidationError):
            pago.full_clean()

    def test_proveedor_invalido_o_comision_cero_no_crean_cobro(self):
        self.escenario()
        otra = Empresa.objects.create(nombre="Empresa ajena", slug="ajena-comision")
        proveedor_ajeno = Proveedor.objects.create(empresa=otra, nombre="Proveedor ajeno")
        proveedor_inactivo = Proveedor.objects.create(
            empresa=self.empresa, nombre="Proveedor inactivo", activo=False,
        )
        for cambios in (
            {"proveedor_comision": ""},
            {"proveedor_comision": str(proveedor_ajeno.pk)},
            {"proveedor_comision": str(proveedor_inactivo.pk)},
            {"porcentaje_comision": "0.00"},
        ):
            with self.subTest(cambios=cambios):
                self.assertEqual(self.registrar(**cambios).status_code, 200)
                self.assertFalse(PagoFactura.objects.filter(factura=self.factura).exists())
                self.assertFalse(PagoComisionProveedor.objects.exists())

    def test_pago_invalido_en_edicion_conserva_cobro_y_compensacion(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion_id = self.assert_liquidacion(pago).pk
        response = self.editar(pago, monto="229877.82")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.assert_liquidacion(pago).pk, compensacion_id)
        self.assertEqual(pago.recibo.monto, self.NETO)

    def test_compensacion_automatica_no_se_revierte_como_egreso_independiente(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = self.assert_liquidacion(pago)
        response = self.client.post(reverse(
            "revertir_pago_comision_proveedor", args=[self.empresa.slug, pago.pk, compensacion.pk],
        ))
        self.assertIn(response.status_code, (302, 400, 403))
        self.assertEqual(self.assert_liquidacion(pago).pk, compensacion.pk)

    def test_periodo_cerrado_impide_cobro_y_liquidacion(self):
        self.escenario()
        fecha = date.today()
        PeriodoContable.objects.create(
            empresa=self.empresa, anio=fecha.year, mes=fecha.month, estado="cerrado",
        )
        response = self.registrar()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PagoFactura.objects.filter(factura=self.factura).exists())
        self.assertFalse(PagoComisionProveedor.objects.exists())
        self.assertFalse(ReciboPago.objects.filter(factura=self.factura).exists())

    def test_cobro_mixto_compensa_una_sola_comision_y_aplica_total(self):
        self.escenario()
        datos = self.datos()
        datos.pop("monto")
        datos.update({
            "monto_efectivo": "100000.00",
            "cuenta_efectivo": str(self.banco.pk),
            "monto_transferencia": "129877.81",
            "cuenta_transferencia": str(self.banco.pk),
            "referencia_efectivo": "EFECTIVO-COMPENSADO",
            "referencia_transferencia": "TRANSFERENCIA-COMPENSADA",
        })
        response = self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        )
        self.assertEqual(response.status_code, 302)
        pagos = self.factura.pagos_facturacion.all()
        self.assertEqual(pagos.count(), 2)
        self.assertEqual(sum((p.monto for p in pagos), Decimal("0.00")), self.NETO)
        self.assertEqual(sum((p.total_aplicado for p in pagos), Decimal("0.00")), self.TOTAL)
        self.assertEqual(PagoComisionProveedor.objects.filter(comision_origen__in=pagos).count(), 1)
        compensado = pagos.get(comision_retenida_cliente=True)
        self.assertEqual(compensado.saldo_comision_pendiente, Decimal("0.00"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        lineas = LineaAsientoContable.objects.filter(
            asiento__empresa=self.empresa, asiento__documento_tipo="pago_factura",
            asiento__documento_id__in=pagos.values("pk"), cuenta=self.banco.cuenta_contable,
        )
        self.assertEqual(self.importe(lineas, "debe"), self.NETO)
        self.assertEqual(self.importe(lineas, "haber"), Decimal("0.00"))

    def test_isv_separado_distribuye_solo_el_dinero_recibido(self):
        self.escenario()
        banco_isv_contable = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110297", nombre="Banco ISV cobro neto", tipo="activo",
        )
        banco_isv = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco ISV cobro neto", tipo="banco", cuenta_contable=banco_isv_contable,
        )
        self.assertEqual(self.registrar(
            separar_isv="on", cuenta_financiera_impuesto=str(banco_isv.pk),
        ).status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        bancos = self.lineas_cobro(pago).filter(cuenta__in=[self.banco.cuenta_contable, banco_isv_contable])
        self.assertEqual(self.importe(bancos, "debe"), self.NETO)
        self.assertEqual(self.importe(bancos, "haber"), Decimal("0.00"))
        self.assertTrue(bancos.filter(cuenta=banco_isv_contable, debe__gt=0).exists())
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(pago.saldo_comision_pendiente, Decimal("0.00"))

    def test_compensacion_usd_conserva_origen_y_diferencia_cambiaria_real(self):
        self.escenario()
        self.factura.moneda = "USD"
        self.factura.tipo_cambio = Decimal("25")
        self.factura.total_lempiras = self.TOTAL * Decimal("25")
        self.factura.save(update_fields=["moneda", "tipo_cambio", "total_lempiras"])
        self.assertEqual(self.registrar(tipo_cambio="26").status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.factura.refresh_from_db()
        self.cliente.refresh_from_db()
        self.proveedor.refresh_from_db()
        self.assertEqual(pago.total_aplicado, self.TOTAL)
        self.assertEqual(pago.recibo.monto, self.NETO)
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(pago.saldo_comision_pendiente, Decimal("0.00"))
        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        self.assertEqual(compensacion.monto, self.COMISION)
        self.assertEqual(compensacion.tipo_cambio, Decimal("26"))
        self.assertIsNone(compensacion.cuenta_financiera_id)
        lineas = self.lineas_cobro(pago)
        bancos = lineas.filter(cuenta=self.banco.cuenta_contable)
        self.assertEqual(self.importe(bancos, "debe"), self.NETO * Decimal("26"))
        self.assertEqual(self.importe(bancos, "debe_origen"), self.NETO)
        self.assertEqual(self.importe(bancos, "haber"), Decimal("0.00"))
        cliente = lineas.filter(cuenta=self.cliente.cuenta_contable)
        self.assertEqual(self.importe(cliente, "haber"), self.TOTAL * Decimal("25"))
        self.assertEqual(self.importe(cliente, "haber_origen"), self.TOTAL)
        proveedor = lineas.filter(cuenta=self.proveedor.cuenta_contable)
        self.assertEqual(self.importe(proveedor, "debe"), self.COMISION * Decimal("26"))
        self.assertEqual(self.importe(proveedor, "haber"), self.COMISION * Decimal("26"))
        ganancia = lineas.filter(cuenta__codigo="420101")
        self.assertEqual(self.importe(ganancia, "haber"), self.TOTAL)
        self.assertEqual(self.importe(lineas, "debe"), self.importe(lineas, "haber"))
        asiento_cobro = registrar_asiento_pago_cliente(pago)
        self.assertEqual(registrar_asiento_pago_comision_proveedor(compensacion), asiento_cobro)

    def test_anular_factura_revierte_cobro_compensado_sin_egreso_ficticio(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.assertEqual(self.client.post(
            reverse("anular_factura", args=[self.empresa.slug, self.factura.pk]),
            {"motivo": "Anulación de prueba de cobro compensado"},
        ).status_code, 302)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "anulada")
        lineas = self.lineas_cobro(pago)
        banco = lineas.filter(cuenta=self.banco.cuenta_contable)
        self.assertEqual(self.importe(banco, "debe"), self.NETO)
        self.assertEqual(self.importe(banco, "haber"), self.NETO)
        self.assertEqual(self.importe(lineas, "debe"), self.importe(lineas, "haber"))

    def test_cierre_acepta_asiento_del_cobro_como_respaldo_de_compensacion(self):
        from contabilidad.cierre import _documentos_sin_asiento

        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        periodo = PeriodoContable.objects.create(
            empresa=self.empresa, anio=pago.fecha.year, mes=pago.fecha.month,
        )
        pendientes = _documentos_sin_asiento(self.empresa, periodo)
        self.assertFalse([fila for fila in pendientes if fila["tipo"] in {"Cobro", "Pago de comisión"}])
        self.assertFalse(AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="pago_comision_proveedor",
        ).exists())

        AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
        ).delete()
        pendientes = _documentos_sin_asiento(self.empresa, periodo)
        self.assertEqual({fila["tipo"] for fila in pendientes if fila["tipo"] in {"Cobro", "Pago de comisión"}},
                         {"Cobro", "Pago de comisión"})
        for fila in pendientes:
            if fila["tipo"] in {"Cobro", "Pago de comisión"}:
                self.assertEqual(fila["url"], reverse("ver_factura", args=[self.empresa.slug, self.factura.pk]))

    def test_cierre_detecta_compensacion_incoherente_aunque_cobro_tenga_asiento(self):
        from contabilidad.cierre import _documentos_sin_asiento

        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        periodo = PeriodoContable.objects.create(
            empresa=self.empresa, anio=pago.fecha.year, mes=pago.fecha.month,
        )
        # Simula una fuente histórica inconsistente que la lectura debe detectar.
        PagoComisionProveedor.objects.filter(comision_origen=pago).update(monto=self.COMISION - Decimal("0.01"))
        pendientes = _documentos_sin_asiento(self.empresa, periodo)
        comisiones = [fila for fila in pendientes if fila["tipo"] == "Pago de comisión"]
        self.assertEqual(len(comisiones), 1)
        self.assertIn("Compensación sin respaldo coherente", comisiones[0]["descripcion"])

    def test_diagnostico_usd_respalda_compensacion_con_cobro_y_detecta_incoherencia(self):
        from contabilidad.historicos_usd import consulta_documentos_sin_asiento

        self.escenario()
        self.factura.moneda = "USD"
        self.factura.tipo_cambio = Decimal("25")
        self.factura.total_lempiras = self.TOTAL * Decimal("25")
        self.factura.save(update_fields=["moneda", "tipo_cambio", "total_lempiras"])
        self.assertEqual(self.registrar(tipo_cambio="25").status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        filtros = {"fuente": "pago_comision_proveedor"}
        self.assertEqual(list(consulta_documentos_sin_asiento(self.empresa, filtros)), [])
        PagoComisionProveedor.objects.filter(pk=compensacion.pk).update(monto=self.COMISION - Decimal("0.01"))
        pendientes = list(consulta_documentos_sin_asiento(self.empresa, filtros))
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["_diag_fuente"], "pago_comision_proveedor")
        self.assertEqual(pendientes[0]["_diag_objeto"], compensacion.pk)

    def test_queryset_delete_no_elimina_compensacion_que_liquida_el_cobro(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = self.assert_liquidacion(pago)
        with self.assertRaises(ValidationError), transaction.atomic():
            PagoComisionProveedor.objects.filter(pk=compensacion.pk).delete()
        self.assertEqual(self.assert_liquidacion(pago).pk, compensacion.pk)

    def test_snapshot_revision_incluye_documento_de_compensacion(self):
        from contabilidad.revision_conversion import _datos_documento

        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        asiento = AsientoContable.objects.get(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
        )
        snapshot = _datos_documento(asiento, pago)
        self.assertEqual(len(snapshot["compensaciones"]), 1)
        self.assertEqual(snapshot["compensaciones"][0]["id"], compensacion.pk)
        self.assertEqual(snapshot["compensaciones"][0]["monto"], str(self.COMISION))
        self.assertEqual(snapshot["compensaciones"][0]["metodo"], "compensacion")
        compensacion.observacion = "Soporte actualizado del acuerdo de compensación"
        compensacion.save(update_fields=["observacion"])
        self.assertNotEqual(_datos_documento(asiento, pago), snapshot)

    def test_edicion_de_cobro_con_movimiento_bancario_conserva_enlaces_y_documentos(self):
        self.escenario()
        self.assertEqual(self.registrar().status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        compensacion = self.assert_liquidacion(pago)
        asiento = AsientoContable.objects.get(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
        )
        referencia_original = pago.referencia
        for enlace in ({"asiento": asiento}, {"pago_factura": pago}):
            with self.subTest(enlace=next(iter(enlace))):
                movimiento = MovimientoBancario.objects.create(
                    empresa=self.empresa, cuenta_financiera=self.banco, fecha=pago.fecha,
                    descripcion="Cobro neto conciliado", credito=self.NETO,
                    estado="contabilizado", conciliado=True, **enlace,
                )
                response = self.editar(pago, referencia="CAMBIO-CONCILIADO")
                self.assertEqual(response.status_code, 200)
                movimiento.refresh_from_db()
                self.assertEqual(movimiento.asiento_id, asiento.pk if "asiento" in enlace else None)
                self.assertEqual(movimiento.pago_factura_id, pago.pk if "pago_factura" in enlace else None)
                self.assertTrue(movimiento.conciliado)
                self.assertEqual(self.assert_liquidacion(pago).pk, compensacion.pk)
                self.assertEqual(pago.referencia, referencia_original)
                self.assertEqual(pago.recibo.referencia, referencia_original)
                self.assertEqual(AsientoContable.objects.get(
                    empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
                ).pk, asiento.pk)
                movimiento.delete()

    def test_update_fields_no_liquida_comision_con_flag_excluido_del_guardado(self):
        self.escenario()
        datos = self.datos()
        datos.pop("comision_retenida_cliente")
        self.assertEqual(self.client.post(
            reverse("registrar_pago", args=[self.empresa.slug, self.factura.pk]), datos,
        ).status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        referencia_original = pago.referencia
        pago.comision_retenida_cliente = True
        pago.referencia = "REFERENCIA-PARCIAL"
        with self.assertRaises(ValidationError):
            pago.save(update_fields=["referencia"])
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertFalse(pago.comision_retenida_cliente)
        self.assertEqual(pago.referencia, referencia_original)
        self.assertEqual(pago.recibo.referencia, referencia_original)
        self.assertFalse(PagoComisionProveedor.objects.filter(comision_origen=pago).exists())
        self.assertEqual(self.factura.saldo_pendiente, self.COMISION)

    def test_conversion_fraccionaria_no_desplaza_centavo_de_compensacion_al_banco(self):
        self.escenario()
        linea = self.factura.lineas.get()
        linea.precio_unitario = Decimal("100.00")
        linea.save()
        self.factura.moneda = "USD"
        self.factura.tipo_cambio = Decimal("1.005")
        self.factura.calcular_totales()
        self.factura.save(update_fields=["moneda", "tipo_cambio", "subtotal", "impuesto", "total", "total_lempiras"])
        self.assertEqual(self.factura.total, Decimal("115.00"))
        self.assertEqual(self.registrar(monto="86.25", tipo_cambio="1.005").status_code, 302)
        pago = PagoFactura.objects.get(factura=self.factura)
        self.cliente.refresh_from_db()
        self.proveedor.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertEqual(pago.monto_comision, Decimal("28.75"))
        self.assertEqual(pago.total_aplicado, Decimal("115.00"))
        self.assertEqual(pago.saldo_comision_pendiente, Decimal("0.00"))
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        lineas = self.lineas_cobro(pago)
        banco = lineas.filter(cuenta=self.banco.cuenta_contable)
        proveedor = lineas.filter(cuenta=self.proveedor.cuenta_contable)
        self.assertEqual(self.importe(banco, "debe"), Decimal("86.68"))
        self.assertEqual(self.importe(banco, "debe_origen"), Decimal("86.25"))
        self.assertEqual(self.importe(banco, "haber"), Decimal("0.00"))
        self.assertEqual(self.importe(proveedor, "debe"), Decimal("28.89"))
        self.assertEqual(self.importe(proveedor, "haber"), Decimal("28.89"))
        self.assertEqual(self.importe(lineas, "debe"), self.importe(lineas, "haber"))
