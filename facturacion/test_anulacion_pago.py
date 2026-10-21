"""Anulación de cobros: reversión contable, auxiliares y evidencia conservada."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import (
    AsientoContable,
    CuentaContable,
    CuentaFinanciera,
    LineaAsientoContable,
    MovimientoBancario,
    PeriodoContable,
    RegularizacionMonedaFactura,
)
from contabilidad.services import registrar_asiento_pago_cliente, registrar_asiento_pago_comision_proveedor
from core.models import Empresa, RegistroAuditoria

from . import tests as pruebas_base
from .models import CierreCaja, Factura, LineaFactura, PagoComisionProveedor, PagoFactura, Proveedor, ReciboPago


class AnulacionPagoFacturaTests(TestCase):
    setUp = pruebas_base.FacturacionTests.setUp

    MOTIVO = "Cobro registrado por error; se conserva la evidencia original"

    def escenario(self, *, moneda="HNL", tasa="1", fecha=None, digital=False):
        self.fecha = fecha or timezone.localdate()
        if digital:
            self.empresa.slug = "digital_planning"
            self.empresa.save(update_fields=["slug"])
        banco_contable = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110299", nombre="Banco cobros a anular", tipo="activo",
        )
        self.banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco cobros a anular", tipo="banco", cuenta_contable=banco_contable,
        )
        self.proveedor = Proveedor.objects.create(empresa=self.empresa, nombre="Comisionista de prueba")
        self.factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, vendedor=self.user, estado="emitida",
            fecha_emision=self.fecha, moneda=moneda, tipo_cambio=Decimal(tasa),
        )
        LineaFactura.objects.create(
            factura=self.factura, producto=self.producto, cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"), impuesto=self.impuesto,
        )
        self.factura.calcular_totales()
        self.factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        self.assertEqual(self.factura.total, Decimal("115.00"))
        return self.factura

    def cobro(self, monto="115.00", *, contabilizar=True, **datos):
        valores = {
            "factura": self.factura,
            "fecha": self.fecha,
            "monto": Decimal(monto),
            "metodo": "transferencia",
            "cuenta_financiera": self.banco,
            "referencia": "PAGO-ORIGINAL",
            "cajero": self.user,
        }
        valores.update(datos)
        pago = PagoFactura.objects.create(**valores)
        if contabilizar:
            registrar_asiento_pago_cliente(pago)
        return pago

    def anular(self, pago, **datos):
        from .anulacion_pagos import anular_pago_factura

        parametros = {"usuario": self.user, "motivo": self.MOTIVO, "fecha": timezone.localdate()}
        parametros.update(datos)
        return anular_pago_factura(pago, **parametros)

    def url_anular(self, pago):
        return reverse("anular_pago_factura", args=[self.empresa.slug, self.factura.pk, pago.pk])

    def post_anular(self, pago, **datos):
        campos = {"motivo": self.MOTIVO, "fecha_anulacion": timezone.localdate().isoformat()}
        campos.update(datos)
        return self.client.post(self.url_anular(pago), campos)

    def asientos(self, pago):
        return AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk,
        )

    def assert_reversion_exacta(self, pago, original, partidas_originales):
        original.refresh_from_db()
        self.assertEqual(original.estado, "contabilizado")
        self.assertEqual(list(original.lineas.order_by("pk").values()), partidas_originales)
        reversion = self.asientos(pago).get(evento="anulacion")
        columnas = ("cuenta_id", "moneda_origen", "tipo_cambio", "debe", "haber")
        esperadas = Counter(
            (fila["cuenta_id"], fila["moneda_origen"], fila["tipo_cambio"], fila["haber"], fila["debe"])
            for fila in partidas_originales
        )
        self.assertEqual(Counter(reversion.lineas.values_list(*columnas)), esperadas)
        self.assertEqual(reversion.total_debe, original.total_haber)
        self.assertEqual(reversion.total_haber, original.total_debe)
        self.assertEqual(reversion.moneda_origen, original.moneda_origen)
        self.assertEqual(reversion.tipo_cambio, original.tipo_cambio)
        for cuenta_id in original.lineas.values_list("cuenta_id", flat=True).distinct():
            lineas = LineaAsientoContable.objects.filter(asiento__in=[original, reversion], cuenta_id=cuenta_id)
            suma = lineas.aggregate(debe=Sum("debe"), haber=Sum("haber"))
            self.assertEqual(suma["debe"], suma["haber"])
        return reversion

    def assert_no_anulado(self, pago):
        pago.refresh_from_db()
        self.assertFalse(pago.anulado)
        self.assertIsNone(pago.anulado_el)
        self.assertIsNone(pago.anulado_por_id)
        self.assertFalse(self.asientos(pago).filter(evento="anulacion").exists())

    def test_anular_cobro_completo_reabre_factura_y_conserva_evidencia(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        partidas = list(original.lineas.order_by("pk").values())
        recibo = pago.recibo
        recibo_anterior = ReciboPago.objects.filter(pk=recibo.pk).values().get()
        resultado = self.anular(pago)
        self.assertEqual(resultado.pk, pago.pk)
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.anulado_por_id, self.user.pk)
        self.assertIsNotNone(pago.anulado_el)
        self.assertEqual(pago.fecha_anulacion, timezone.localdate())
        self.assertEqual(pago.motivo_anulacion, self.MOTIVO)
        self.assertEqual(pago.monto, Decimal("115.00"))
        self.assertEqual(pago.referencia, "PAGO-ORIGINAL")
        self.assertEqual(self.factura.pagos_facturacion.count(), 1)
        self.assertEqual(self.factura.total_pagado, Decimal("0.00"))
        self.assertEqual(self.factura.saldo_pendiente, Decimal("115.00"))
        self.assertEqual(self.factura.estado_pago, "pendiente")
        self.assertEqual(ReciboPago.objects.filter(pk=recibo.pk).values().get(), recibo_anterior)
        self.assert_reversion_exacta(pago, original, partidas)
        self.assertTrue(RegistroAuditoria.objects.filter(
            empresa=self.empresa, usuario=self.user, app_label="facturacion", modelo="factura",
            objeto_id=str(self.factura.pk), motivo=self.MOTIVO, cambios__pago_id__nuevo=pago.pk,
        ).exists())

    def test_anulacion_repetida_es_idempotente_y_conserva_primer_motivo(self):
        self.escenario()
        pago = self.cobro()
        self.anular(pago)
        pago.refresh_from_db()
        marca_original = pago.anulado_el
        primera_reversion = self.asientos(pago).get(evento="anulacion")
        self.anular(pago, motivo="Segundo intento de anulación que no debe duplicar nada")
        pago.refresh_from_db()
        self.assertEqual(pago.motivo_anulacion, self.MOTIVO)
        self.assertEqual(pago.anulado_el, marca_original)
        self.assertEqual(self.asientos(pago).filter(evento="anulacion").count(), 1)
        self.assertEqual(self.asientos(pago).get(evento="anulacion").pk, primera_reversion.pk)
        self.assertEqual(ReciboPago.objects.filter(pago=pago).count(), 1)

    def test_anular_un_abono_conserva_otro_abono_activo(self):
        self.escenario()
        primero = self.cobro("50.00")
        segundo = self.cobro("65.00", referencia="ABONO-A-CONSERVAR")
        self.anular(primero)
        segundo.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertFalse(segundo.anulado)
        self.assertEqual(self.factura.total_pagado, Decimal("65.00"))
        self.assertEqual(self.factura.saldo_pendiente, Decimal("50.00"))
        self.assertEqual(self.factura.estado_pago, "parcial")
        self.assertEqual(self.asientos(segundo).filter(evento="cobro").count(), 1)
        self.assertEqual(self.factura.pagos_facturacion.count(), 2)

    def test_puede_registrar_nuevo_pago_completo_tras_anular_el_anterior(self):
        self.escenario()
        anterior = self.cobro()
        self.anular(anterior)
        nuevo = self.cobro(referencia="COBRO-CORRECTO")
        self.factura.refresh_from_db()
        self.assertFalse(nuevo.anulado)
        self.assertEqual(self.factura.total_pagado, Decimal("115.00"))
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(self.factura.estado_pago, "pagado")
        self.assertEqual(self.factura.pagos_facturacion.count(), 2)

    def test_anulacion_usd_revierte_historico_y_diferencia_y_permite_recobrar(self):
        self.escenario(moneda="USD", tasa="25")
        anterior = self.cobro(tipo_cambio=Decimal("26"))
        original = self.asientos(anterior).get(evento="cobro")
        partidas = list(original.lineas.order_by("pk").values())
        self.anular(anterior)
        self.assert_reversion_exacta(anterior, original, partidas)
        nuevo = self.cobro(tipo_cambio=Decimal("27"), referencia="COBRO-USD-CORRECTO")
        self.cliente.refresh_from_db()
        asiento_nuevo = self.asientos(nuevo).get(evento="cobro")
        self.assertEqual(asiento_nuevo.lineas.get(cuenta=self.cliente.cuenta_contable).haber, Decimal("2875.00"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(asiento_nuevo.total_debe, asiento_nuevo.total_haber)

    def test_anular_retenciones_e_isv_separado_invierte_cada_cuenta(self):
        self.escenario()
        cuenta_isv = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110298", nombre="Banco ISV", tipo="activo",
        )
        banco_isv = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco ISV", tipo="banco", cuenta_contable=cuenta_isv,
        )
        pago = self.cobro(
            "100.00", retencion_isr=Decimal("10.00"), retencion_isv=Decimal("5.00"),
            separar_isv=True, cuenta_financiera_impuesto=banco_isv,
        )
        original = self.asientos(pago).get(evento="cobro")
        partidas = list(original.lineas.order_by("pk").values())
        self.assertTrue(original.lineas.filter(cuenta=cuenta_isv, debe=Decimal("10.00")).exists())
        self.anular(pago)
        reversion = self.assert_reversion_exacta(pago, original, partidas)
        self.assertTrue(reversion.lineas.filter(cuenta=cuenta_isv, haber=Decimal("10.00")).exists())
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.subtotal_pagado, Decimal("0.00"))
        self.assertEqual(self.factura.impuesto_pagado, Decimal("0.00"))
        self.assertEqual(self.factura.saldo_pendiente, Decimal("115.00"))

    def test_anular_compensacion_conserva_documento_y_revierte_solo_banco_neto(self):
        self.escenario(digital=True)
        pago = self.cobro(
            "86.25", proveedor_comision=self.proveedor, porcentaje_comision=Decimal("25.00"),
            monto_comision=Decimal("28.75"), comision_retenida_cliente=True,
        )
        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        documento_original = PagoComisionProveedor.objects.filter(pk=compensacion.pk).values().get()
        original = self.asientos(pago).get(evento="cobro")
        partidas = list(original.lineas.order_by("pk").values())
        self.anular(pago)
        reversion = self.assert_reversion_exacta(pago, original, partidas)
        self.assertEqual(PagoComisionProveedor.objects.filter(pk=compensacion.pk).values().get(), documento_original)
        self.assertTrue(reversion.lineas.filter(cuenta=self.banco.cuenta_contable, haber=Decimal("86.25")).exists())
        self.assertFalse(reversion.lineas.filter(cuenta=self.banco.cuenta_contable, haber=Decimal("115.00")).exists())
        self.assertFalse(AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="pago_comision_proveedor", documento_id=compensacion.pk,
        ).exists())
        pago.refresh_from_db()
        self.assertEqual(pago.saldo_comision_pendiente, Decimal("0.00"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.saldo_pendiente, Decimal("115.00"))

    def test_comision_pagada_por_banco_impide_anular_el_cobro(self):
        self.escenario()
        pago = self.cobro(
            proveedor_comision=self.proveedor, porcentaje_comision=Decimal("25.00"), monto_comision=Decimal("28.75"),
        )
        egreso = PagoComisionProveedor.objects.create(
            comision_origen=pago, fecha=self.fecha, monto=Decimal("5.00"),
            cuenta_financiera=self.banco, metodo="transferencia", registrado_por=self.user,
        )
        registrar_asiento_pago_comision_proveedor(egreso)
        with self.assertRaises(ValidationError):
            self.anular(pago)
        self.assert_no_anulado(pago)
        self.assertTrue(PagoComisionProveedor.objects.filter(pk=egreso.pk).exists())
        self.assertEqual(pago.total_comision_pagada, Decimal("5.00"))

    def test_cobro_conciliado_impide_anulacion_y_conserva_vinculo(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        movimiento = MovimientoBancario.objects.create(
            empresa=self.empresa, cuenta_financiera=self.banco, fecha=self.fecha,
            descripcion="Cobro conciliado", credito=Decimal("115.00"), estado="contabilizado",
            conciliado=True, pago_factura=pago, asiento=original,
        )
        with self.assertRaises(ValidationError):
            self.anular(pago)
        self.assert_no_anulado(pago)
        movimiento.refresh_from_db()
        self.assertTrue(movimiento.conciliado)
        self.assertEqual(movimiento.pago_factura_id, pago.pk)
        self.assertEqual(movimiento.asiento_id, original.pk)

    def test_cierre_de_caja_cerrado_o_revisado_impide_anular(self):
        self.escenario()
        pago = self.cobro(metodo="efectivo")
        for estado in ("cerrado", "revisado"):
            with self.subTest(estado=estado):
                cierre = CierreCaja.objects.create(
                    empresa=self.empresa, cajero=self.user, fecha=self.fecha, turno="general", estado=estado,
                )
                with self.assertRaises(ValidationError):
                    self.anular(pago)
                self.assert_no_anulado(pago)
                cierre.delete()

    def test_periodo_original_cerrado_bloquea_aunque_fecha_anulacion_este_abierta(self):
        hoy = timezone.localdate()
        fecha_anterior = hoy.replace(day=1) - timedelta(days=1)
        self.escenario(fecha=fecha_anterior)
        pago = self.cobro()
        PeriodoContable.objects.create(
            empresa=self.empresa, anio=fecha_anterior.year, mes=fecha_anterior.month, estado="cerrado",
        )
        with self.assertRaises(ValidationError):
            self.anular(pago, fecha=hoy)
        self.assert_no_anulado(pago)

    def test_fecha_de_anulacion_en_periodo_cerrado_no_marca_pago(self):
        hoy = timezone.localdate()
        fecha_anterior = hoy.replace(day=1) - timedelta(days=1)
        self.escenario(fecha=fecha_anterior)
        pago = self.cobro()
        PeriodoContable.objects.create(empresa=self.empresa, anio=hoy.year, mes=hoy.month, estado="cerrado")
        with self.assertRaises(ValidationError):
            self.anular(pago, fecha=hoy)
        self.assert_no_anulado(pago)

    def test_motivo_vacio_o_fecha_fuera_de_rango_no_anulan(self):
        self.escenario()
        pago = self.cobro()
        for datos in (
            {"motivo": ""}, {"motivo": "   "},
            {"fecha": self.fecha - timedelta(days=1)}, {"fecha": self.fecha + timedelta(days=1)},
        ):
            with self.subTest(datos=datos), self.assertRaises(ValidationError):
                self.anular(pago, **datos)
            self.assert_no_anulado(pago)

    def test_cobro_sin_asiento_se_anula_sin_inventar_reversion(self):
        self.escenario()
        pago = self.cobro(contabilizar=False)
        recibo_id = pago.recibo.pk
        self.anular(pago)
        pago.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.recibo.pk, recibo_id)
        self.assertEqual(pago.recibo.monto, Decimal("115.00"))
        self.assertFalse(self.asientos(pago).exists())
        self.assertEqual(self.factura.saldo_pendiente, Decimal("115.00"))

    def test_asiento_en_borrador_se_invalida_sin_reversion_publicada(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        original.estado = "borrador"
        original.save(update_fields=["estado"])
        self.anular(pago)
        pago.refresh_from_db()
        original.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(original.estado, "anulado")
        self.assertFalse(self.asientos(pago).filter(evento="anulacion").exists())

    def test_pago_anulado_no_se_edita_ni_se_reactiva(self):
        self.escenario()
        pago = self.cobro()
        self.anular(pago)
        pago.refresh_from_db()
        pago.monto = Decimal("50.00")
        with self.assertRaises(ValidationError):
            pago.save()
        pago.refresh_from_db()
        pago.anulado = False
        with self.assertRaises(ValidationError):
            pago.save()
        pago.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.monto, Decimal("115.00"))

    def test_no_contabiliza_de_nuevo_cobro_o_compensacion_anulados(self):
        self.escenario(digital=True)
        pago = self.cobro(
            "86.25", proveedor_comision=self.proveedor, porcentaje_comision=25,
            monto_comision=Decimal("28.75"), comision_retenida_cliente=True,
        )
        compensacion = PagoComisionProveedor.objects.get(comision_origen=pago)
        self.anular(pago)
        pago.refresh_from_db()
        compensacion.refresh_from_db()
        with self.assertRaises(ValidationError):
            registrar_asiento_pago_cliente(pago)
        with self.assertRaises(ValidationError):
            registrar_asiento_pago_comision_proveedor(compensacion)
        with self.assertRaises(ValidationError):
            compensacion.save()
        self.assertEqual(self.asientos(pago).filter(evento="anulacion").count(), 1)

    def test_get_no_ejecuta_anulacion_y_post_autorizado_si(self):
        self.escenario()
        pago = self.cobro()
        self.assertEqual(self.client.get(self.url_anular(pago)).status_code, 405)
        self.assert_no_anulado(pago)
        self.assertEqual(self.post_anular(pago).status_code, 302)
        pago.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.anulado_por_id, self.user.pk)

    def test_post_sin_motivo_o_fecha_invalida_preserva_pago(self):
        self.escenario()
        pago = self.cobro()
        for campos in ({"motivo": ""}, {"fecha_anulacion": "no-es-fecha"}):
            with self.subTest(campos=campos):
                response = self.post_anular(pago, **campos)
                self.assertIn(response.status_code, (200, 302, 400))
                self.assert_no_anulado(pago)

    def test_usuario_sin_permiso_no_anula_por_post_ni_servicio(self):
        self.escenario()
        pago = self.cobro()
        self.rol_total.puede_anular_facturas = False
        self.rol_total.save(update_fields=["puede_anular_facturas"])
        self.assertRedirects(
            self.post_anular(pago), reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self.anular(pago)
        self.assert_no_anulado(pago)

    def test_no_puede_anular_pago_de_otra_empresa_por_servicio_ni_url(self):
        self.escenario()
        pago = self.cobro()
        otra = Empresa.objects.create(nombre="Empresa ajena", slug="ajena-anulacion")
        ajeno = get_user_model().objects.create_user(
            username="ajeno-anulacion", password="pass", empresa=otra, rol_sistema=self.rol_total,
        )
        with self.assertRaises(PermissionDenied):
            self.anular(pago, usuario=ajeno)
        self.client.force_login(ajeno)
        self.assertRedirects(
            self.post_anular(pago), reverse("empresa_login", args=[self.empresa.slug]), fetch_redirect_response=False,
        )
        self.assert_no_anulado(pago)

    def test_servicio_rechaza_usuario_anonimo(self):
        self.escenario()
        pago = self.cobro()
        with self.assertRaises(PermissionDenied):
            self.anular(pago, usuario=AnonymousUser())
        self.assert_no_anulado(pago)

    def test_regularizacion_monetaria_bloquea_anulacion(self):
        self.escenario(moneda="USD", tasa="25")
        pago = self.cobro(tipo_cambio=Decimal("26"))
        self.cliente.refresh_from_db()
        emision = AsientoContable.objects.get(
            empresa=self.empresa, documento_tipo="factura", documento_id=self.factura.pk, evento="emision",
        )
        RegularizacionMonedaFactura.objects.create(
            empresa=self.empresa, factura=self.factura, asiento_origen=emision,
            cuenta_cliente=self.cliente.cuenta_contable, fecha=timezone.localdate(), tipo_cambio=25,
            total_original=115, total_hnl=2875, saldo_original=0, saldo_hnl_anterior=0, saldo_hnl_corregido=0,
            aplicado_original=115, aplicado_hnl_anterior=2875, aplicado_hnl_corregido=2875,
            notas_original=0, notas_hnl_anterior=0, notas_hnl_corregido=0,
            motivo="Regularización previa de la factura de prueba", creado_por=self.user,
        )
        with self.assertRaises(ValidationError):
            self.anular(pago)
        self.assert_no_anulado(pago)

    def test_revision_integral_usd_bloquea_anulacion(self):
        from contabilidad.revision_conversion import registrar_revision_conversion

        self.escenario(moneda="USD", tasa="25")
        pago = self.cobro(tipo_cambio=Decimal("26"))
        original = self.asientos(pago).get(evento="cobro")
        original.conversion_verificada = False
        original.save(update_fields=["conversion_verificada"])
        partidas = [{
            "linea_id": linea.pk,
            "moneda": linea.moneda_origen,
            "importe_original": linea.debe_origen or linea.haber_origen or linea.debe or linea.haber,
            "tipo_cambio": linea.tipo_cambio,
        } for linea in original.lineas.select_related("cuenta").order_by("pk")]
        revision = registrar_revision_conversion(
            asiento=original, partidas=partidas, fecha=timezone.localdate(),
            motivo="Revisión documentada de todos los importes originales del cobro USD", usuario=self.user,
        )
        self.assertEqual(revision.origen_id, original.pk)
        with self.assertRaises(ValidationError):
            self.anular(pago)
        self.assert_no_anulado(pago)

    def test_error_de_auditoria_revierte_marca_asiento_y_desvinculacion_bancaria(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        movimiento = MovimientoBancario.objects.create(
            empresa=self.empresa, cuenta_financiera=self.banco, fecha=self.fecha,
            descripcion="Extracto aplicado al cobro erróneo", credito=Decimal("115.00"),
            estado="contabilizado", conciliado=False, pago_factura=pago, asiento=original,
        )
        crear_original = RegistroAuditoria.objects.create

        def fallar_auditoria_final(**datos):
            accion = datos.get("cambios", {}).get("accion_factura", {}).get("nuevo")
            if accion == "anular_pago":
                raise RuntimeError("No se pudo persistir la evidencia de la anulación")
            return crear_original(**datos)

        with patch("facturacion.anulacion_pagos.RegistroAuditoria.objects.create", side_effect=fallar_auditoria_final):
            with self.assertRaises(RuntimeError):
                self.anular(pago)
        self.assert_no_anulado(pago)
        self.factura.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(self.factura.estado_pago, "pagado")
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(movimiento.asiento_id, original.pk)
        self.assertEqual(movimiento.pago_factura_id, pago.pk)
        self.assertEqual(movimiento.estado, "contabilizado")
        self.assertEqual(movimiento.credito, Decimal("115.00"))
        self.assertEqual(pago.recibo.monto, Decimal("115.00"))

    def test_cobro_usd_anulado_solo_neutraliza_alerta_desde_fecha_de_reversion(self):
        from contabilidad.estado_conversion import asientos_cobros_anulados_neutralizados, asientos_conversion_pendiente
        from contabilidad.historicos_usd import consulta_asientos_historicos

        hoy = timezone.localdate()
        antes = hoy - timedelta(days=1)
        self.escenario(moneda="USD", tasa="25", fecha=antes)
        pago = self.cobro(tipo_cambio=Decimal("26"))
        original = self.asientos(pago).get(evento="cobro")
        original.conversion_verificada = False
        original.save(update_fields=["conversion_verificada"])
        self.anular(pago, fecha=hoy)
        reversion = self.asientos(pago).get(evento="anulacion")
        self.assertNotIn(original.pk, asientos_cobros_anulados_neutralizados(self.empresa, antes))
        self.assertIn(original.pk, asientos_conversion_pendiente(self.empresa, antes).values_list("pk", flat=True))
        anteriores = consulta_asientos_historicos(self.empresa, {"fuente": "pago_factura", "hasta": antes})
        self.assertIn(original.pk, anteriores.values_list("pk", flat=True))
        self.assertNotIn(reversion.pk, anteriores.values_list("pk", flat=True))

        self.assertEqual(set(asientos_cobros_anulados_neutralizados(self.empresa, hoy)), {original.pk, reversion.pk})
        self.assertFalse(asientos_conversion_pendiente(self.empresa, hoy).filter(pk__in=[original.pk, reversion.pk]).exists())
        self.assertFalse(consulta_asientos_historicos(
            self.empresa, {"fuente": "pago_factura", "hasta": hoy},
        ).filter(pk__in=[original.pk, reversion.pk]).exists())
        self.assertEqual(self.asientos(pago).count(), 2)

    def test_nuevo_abono_usd_tras_anular_primero_con_otro_cobro_posterior(self):
        self.escenario(moneda="USD", tasa="25")
        primero = self.cobro("50.00", tipo_cambio=Decimal("26"))
        segundo = self.cobro("65.00", tipo_cambio=Decimal("27"), referencia="SEGUNDO-CONSERVADO")
        asiento_segundo = self.asientos(segundo).get(evento="cobro")
        partidas_segundo = list(asiento_segundo.lineas.order_by("pk").values())
        self.anular(primero)
        nuevo = self.cobro("50.00", tipo_cambio=Decimal("28"), referencia="REEMPLAZO-PRIMERO")
        self.cliente.refresh_from_db()
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.total_pagado, Decimal("115.00"))
        self.assertEqual(self.factura.estado_pago, "pagado")
        self.assertEqual(self.factura.saldo_pendiente, Decimal("0.00"))
        asiento_nuevo = self.asientos(nuevo).get(evento="cobro")
        self.assertEqual(asiento_nuevo.lineas.get(cuenta=self.cliente.cuenta_contable).haber, Decimal("1250.00"))
        self.assertEqual(asiento_nuevo.total_debe, asiento_nuevo.total_haber)
        self.assertEqual(list(asiento_segundo.lineas.order_by("pk").values()), partidas_segundo)

    def test_reversion_manual_previa_impide_anulacion_duplicada_del_cobro(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse(
            "reversar_asiento_contable", args=[self.empresa.slug, original.pk],
        )).status_code, 302)
        manual = AsientoContable.objects.get(
            empresa=self.empresa, documento_tipo="asiento_contable", documento_id=original.pk, evento="reversion",
        )
        self.assertEqual(manual.estado, "contabilizado")
        with self.assertRaises(ValidationError):
            self.anular(pago)
        self.assert_no_anulado(pago)
        self.assertEqual(AsientoContable.objects.filter(
            empresa=self.empresa, documento_tipo="asiento_contable", documento_id=original.pk, evento="reversion",
        ).count(), 1)

    def test_reversion_generica_no_desarma_original_ni_reversion_de_cobro_anulado(self):
        self.escenario()
        pago = self.cobro()
        original = self.asientos(pago).get(evento="cobro")
        partidas = list(original.lineas.order_by("pk").values())
        self.anular(pago)
        reversion = self.asientos(pago).get(evento="anulacion")
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        for asiento in (original, reversion):
            with self.subTest(evento=asiento.evento):
                self.assertEqual(self.client.post(reverse(
                    "reversar_asiento_contable", args=[self.empresa.slug, asiento.pk],
                )).status_code, 302)
                self.assertFalse(AsientoContable.objects.filter(
                    empresa=self.empresa, documento_tipo="asiento_contable", documento_id=asiento.pk,
                ).exists())
        pago.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assert_reversion_exacta(pago, original, partidas)

    def test_asientos_y_partidas_de_cobro_anulado_no_se_modifican_ni_eliminan(self):
        self.escenario()
        pago = self.cobro()
        self.anular(pago)
        for asiento in self.asientos(pago).order_by("pk"):
            with self.subTest(evento=asiento.evento):
                original = AsientoContable.objects.filter(pk=asiento.pk).values().get()
                partidas = list(asiento.lineas.order_by("pk").values())
                asiento.descripcion = "Intento de editar evidencia anulada"
                with self.assertRaises(ValidationError), transaction.atomic():
                    asiento.save(update_fields=["descripcion"])
                with self.assertRaises(ValidationError), transaction.atomic():
                    AsientoContable.objects.filter(pk=asiento.pk).update(descripcion="Cambio en lote")
                linea = asiento.lineas.first()
                linea.detalle = "Intento de editar partida anulada"
                with self.assertRaises(ValidationError), transaction.atomic():
                    linea.save(update_fields=["detalle"])
                with self.assertRaises(ValidationError), transaction.atomic():
                    LineaAsientoContable.objects.filter(pk=linea.pk).delete()
                with self.assertRaises(ValidationError), transaction.atomic():
                    AsientoContable.objects.filter(pk=asiento.pk).delete()
                self.assertEqual(AsientoContable.objects.filter(pk=asiento.pk).values().get(), original)
                self.assertEqual(list(asiento.lineas.order_by("pk").values()), partidas)

    def test_bulk_create_no_agrega_asientos_o_partidas_a_cobro_anulado(self):
        self.escenario()
        pago = self.cobro()
        self.anular(pago)
        for asiento in self.asientos(pago).order_by("pk"):
            with self.subTest(evento=asiento.evento):
                cantidad_original = asiento.lineas.count()
                nueva_linea = LineaAsientoContable(
                    asiento=asiento, cuenta=self.banco.cuenta_contable,
                    detalle="Intento de insertar partida a evidencia conservada", debe=Decimal("1.00"),
                )
                with self.assertRaisesMessage(ValidationError, "anulado"), transaction.atomic():
                    LineaAsientoContable.objects.bulk_create([nueva_linea])
                self.assertEqual(asiento.lineas.count(), cantidad_original)
        nuevo = AsientoContable(
            empresa=self.empresa, fecha=self.fecha, descripcion="Intento de agregar otro asiento al cobro anulado",
            documento_tipo="pago_factura", documento_id=pago.pk, evento="otro_asiento",
            estado="borrador", creado_por=self.user,
        )
        with self.assertRaisesMessage(ValidationError, "anulado"), transaction.atomic():
            AsientoContable.objects.bulk_create([nuevo])
        self.assertEqual(self.asientos(pago).count(), 2)
