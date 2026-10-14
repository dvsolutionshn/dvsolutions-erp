from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models import QuerySet, Sum
from django.test import TestCase

from core.models import Empresa
from facturacion.models import NotaCredito, PagoFactura

from . import tests as pruebas_base
from . import test_moneda_contable as moneda_base
from .models import AsientoContable, CuentaContable, CuentaFinanciera, PeriodoContable, RevisionConversionAsiento
from .regularizacion_moneda import regularizar_factura_historica
from .revision_conversion import registrar_revision_conversion, revision_conversion_vigente, vista_previa_revision
from .services import asegurar_cuenta_contable_cliente, obtener_cuenta_operativa, registrar_asiento_documento


class RevisionConversionTests(TestCase):
    setUp = pruebas_base.ContabilidadTests.setUp

    def legado(self, *, costo=False):
        self.cuenta_cliente = asegurar_cuenta_contable_cliente(self.cliente)
        lineas = [
            {"cuenta": self.cuenta_cliente, "debe": 115, "detalle": "Cobro a cliente legado"},
            {"cuenta": "ventas_15", "haber": 100, "detalle": "Venta gravada"},
            {"cuenta": "isv_15_por_pagar", "haber": 15, "detalle": "ISV"},
        ]
        if costo:
            lineas += [{"cuenta": "costo_ventas", "debe": 40}, {"cuenta": "inventario", "haber": 40}]
        return registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="importacion_historica", documento_id=1,
            evento="emision", fecha=date(2026, 4, 10), descripcion="Documento histórico USD",
            referencia="HIST-1", origen_modulo="contabilidad", moneda_origen="USD",
            tipo_cambio=25, conversion_verificada=False, lineas=lineas,
        )

    def partidas(self, asiento, *, tasa=25):
        return [{
            "linea_id": linea.pk,
            "moneda": "USD",
            "importe_original": linea.debe or linea.haber,
            "tipo_cambio": tasa,
        } for linea in asiento.lineas.select_related("cuenta").order_by("pk")]

    def registrar(self, asiento, partidas=None, **kwargs):
        datos = {"asiento": asiento, "partidas": partidas or self.partidas(asiento),
                 "fecha": date(2026, 5, 5), "motivo": "Revisión y respaldo documental de todos los importes en dólares",
                 "usuario": self.usuario}
        datos.update(kwargs)
        return registrar_revision_conversion(**datos)

    def saldos(self, cuentas):
        resultado = {}
        for cuenta in cuentas:
            total = cuenta.lineas_asiento.filter(asiento__estado="contabilizado").aggregate(debe=Sum("debe"), haber=Sum("haber"))
            resultado[cuenta.pk] = (total["debe"] or Decimal("0")) - (total["haber"] or Decimal("0"))
        return resultado

    def test_vista_previa_es_completa_no_mutante_y_conserva_originales(self):
        asiento = self.legado()
        previa = vista_previa_revision(asiento, self.partidas(asiento))
        self.assertEqual(len(previa["partidas"]), 3)
        self.assertEqual(previa["diferencia_debe"], Decimal("2760.00"))
        self.assertEqual(previa["diferencia_haber"], Decimal("2760.00"))
        self.assertFalse(previa["requiere_contrapartida"])
        self.assertEqual(len(previa["firma_fuente"]), 64)
        self.assertFalse(RevisionConversionAsiento.objects.exists())
        self.assertEqual(asiento.total_debe, Decimal("115"))

    def test_revisa_ventas_impuesto_y_cliente_sin_reescribir_mes_cerrado(self):
        asiento = self.legado()
        originales = list(asiento.lineas.order_by("pk").values())
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado="cerrado")
        revision = self.registrar(asiento)
        self.assertEqual(revision.ajuste.total_debe, Decimal("2760.00"))
        self.assertEqual(revision.ajuste.documento_tipo, "revision_conversion")
        self.assertEqual(list(asiento.lineas.order_by("pk").values()), originales)
        asiento.refresh_from_db()
        self.assertFalse(asiento.conversion_verificada)
        cuentas = list(CuentaContable.objects.filter(pk__in=asiento.lineas.values("cuenta_id")))
        saldos = self.saldos(cuentas)
        self.assertEqual(saldos[self.cuenta_cliente.pk], Decimal("2875"))
        self.assertEqual(saldos[obtener_cuenta_operativa(self.empresa, "ventas_15").pk], Decimal("-2500"))
        self.assertEqual(saldos[obtener_cuenta_operativa(self.empresa, "isv_15_por_pagar").pk], Decimal("-375"))

    def test_costo_y_stock_documentados_hnl_no_se_convierten_otra_vez(self):
        asiento = self.legado(costo=True)
        partidas = self.partidas(asiento)
        cuentas_costo = {obtener_cuenta_operativa(self.empresa, "costo_ventas").pk, obtener_cuenta_operativa(self.empresa, "inventario").pk}
        for partida in partidas:
            if asiento.lineas.get(pk=partida["linea_id"]).cuenta_id in cuentas_costo:
                partida.update(moneda="HNL", tipo_cambio=1)
        revision = self.registrar(asiento, partidas)
        self.assertFalse(revision.ajuste.lineas.filter(cuenta_id__in=cuentas_costo).exists())
        self.assertEqual(revision.ajuste.total_debe, Decimal("2760"))

    def test_hnl_correcto_se_documenta_sin_asiento_de_ajuste(self):
        asiento = self.legado()
        partidas = self.partidas(asiento)
        for partida in partidas:
            partida.update(moneda="HNL", tipo_cambio=1)
        revision = self.registrar(asiento, partidas)
        self.assertIsNone(revision.ajuste)
        self.assertEqual(revision_conversion_vigente(asiento).pk, revision.pk)

    def test_exige_cada_partida_una_vez_y_no_admite_ajenas(self):
        asiento = self.legado()
        partidas = self.partidas(asiento)
        for invalida in [partidas[:-1], [partidas[0], partidas[0], partidas[2]], [dict(partidas[0], linea_id=999999), *partidas[1:]]]:
            with self.subTest(partidas=invalida), self.assertRaises(ValidationError):
                vista_previa_revision(asiento, invalida)

    def test_rechaza_importes_tasas_y_moneda_sin_evidencia_valida(self):
        asiento = self.legado()
        for cambios in [{"importe_original": 0}, {"importe_original": "NaN"}, {"importe_original": ".001"},
                        {"tipo_cambio": 0}, {"tipo_cambio": "Infinity"}, {"tipo_cambio": "25.0000001"},
                        {"moneda": "EUR"}, {"moneda": "HNL", "tipo_cambio": 25}]:
            partidas = self.partidas(asiento)
            partidas[0].update(cambios)
            with self.subTest(cambios=cambios), self.assertRaises(ValidationError):
                vista_previa_revision(asiento, partidas)

    def test_conversion_que_redondea_a_cero_no_se_declara_revisada(self):
        asiento = self.legado()
        partidas = self.partidas(asiento, tasa=Decimal("0.000001"))
        with self.assertRaisesMessage(ValidationError, "importe convertido debe ser mayor que cero"):
            self.registrar(asiento, partidas)
        self.assertFalse(RevisionConversionAsiento.objects.exists())
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="revision_conversion").exists())

    def test_desbalance_muestra_y_exige_contrapartida_explicita(self):
        asiento = self.legado()
        partidas = self.partidas(asiento)
        partidas[0]["tipo_cambio"] = 26
        previa = vista_previa_revision(asiento, partidas)
        self.assertTrue(previa["requiere_contrapartida"])
        self.assertEqual(previa["total_debe_ajuste"] - previa["total_haber_ajuste"], Decimal("115"))
        with self.assertRaisesMessage(ValidationError, "contrapartida autorizada"):
            self.registrar(asiento, partidas)
        ganancia = obtener_cuenta_operativa(self.empresa, "ganancia_cambiaria")
        previa = vista_previa_revision(asiento, partidas, ganancia)
        self.assertEqual(previa["lineas_ajuste"][-1]["haber"], "115.00")
        self.assertEqual(previa["total_debe_ajuste"], previa["total_haber_ajuste"])
        revision = self.registrar(asiento, partidas, cuenta_contrapartida=ganancia)
        self.assertTrue(revision.ajuste.esta_balanceado)

    def test_contrapartida_propia_activa_ajena_a_saldos_revisados(self):
        asiento = self.legado()
        otra = Empresa.objects.create(nombre="Empresa independiente USD", slug="empresa-independiente-usd", rtn="08011999233111")
        ajena = CuentaContable.objects.create(empresa=otra, codigo="4199", nombre="Ganancias ajenas", tipo="ingreso")
        inactiva = CuentaContable.objects.create(empresa=self.empresa, codigo="4198", nombre="Cuenta inactiva", tipo="ingreso", activa=False)
        for cuenta in [ajena, inactiva, self.cuenta_cliente]:
            with self.subTest(cuenta=cuenta.pk), self.assertRaises(ValidationError):
                vista_previa_revision(asiento, self.partidas(asiento), cuenta)

    def test_fecha_abierta_posterior_origen_y_corte_respetado(self):
        asiento = self.legado()
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=5, estado="cerrado")
        with self.assertRaisesMessage(ValidationError, "período contable está cerrado"):
            self.registrar(asiento)
        with self.assertRaisesMessage(ValidationError, "anticipar"):
            self.registrar(asiento, fecha=date(2026, 3, 31))
        revision = self.registrar(asiento, fecha=date(2026, 6, 1))
        self.assertIsNone(revision_conversion_vigente(asiento, date(2026, 5, 31)))
        self.assertEqual(revision_conversion_vigente(asiento, "2026-06-01").pk, revision.pk)

    def test_cambios_fuente_incluso_sin_cambio_total_exigen_revisar_de_nuevo(self):
        asiento = self.legado()
        partidas = self.partidas(asiento)
        previa = vista_previa_revision(asiento, partidas)
        asiento.descripcion = "Descripción cambiada después de la propuesta"
        asiento.save(update_fields=["descripcion"])
        with self.assertRaisesMessage(ValidationError, "partidas cambiaron"):
            self.registrar(asiento, partidas, vista_previa_esperada=previa)
        self.assertFalse(RevisionConversionAsiento.objects.exists())
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="revision_conversion").exists())

    def test_duplicados_no_publican_dos_ajustes_y_evidencia_es_inmutable(self):
        asiento = self.legado()
        revision = self.registrar(asiento)
        self.assertEqual(self.registrar(asiento).pk, revision.pk)
        self.assertEqual(AsientoContable.objects.filter(documento_tipo="revision_conversion").count(), 1)
        revision.motivo = "Intento de reemplazar el respaldo ya confirmado"
        with self.assertRaisesMessage(ValidationError, "inmutable"):
            revision.save()
        with self.assertRaisesMessage(ValidationError, "inmutable"):
            RevisionConversionAsiento.objects.filter(pk=revision.pk).update(motivo="Cambio")
        with self.assertRaisesMessage(ValidationError, "Conserve"):
            RevisionConversionAsiento.objects.filter(pk=revision.pk).delete()

    def test_cambio_posterior_de_tipo_de_cuenta_no_escape_a_la_revision(self):
        asiento = self.legado()
        self.registrar(asiento)
        QuerySet.update(CuentaContable.objects.filter(pk=self.cuenta_cliente.pk), tipo="pasivo")
        with self.assertRaisesMessage(ValidationError, "no coinciden con el respaldo"):
            revision_conversion_vigente(asiento)

    def test_cambio_posterior_de_tipo_de_contrapartida_no_escape_a_la_revision(self):
        asiento = self.legado()
        partidas = self.partidas(asiento)
        partidas[0]["tipo_cambio"] = 26
        cuenta = obtener_cuenta_operativa(self.empresa, "ganancia_cambiaria")
        self.registrar(asiento, partidas, cuenta_contrapartida=cuenta)
        QuerySet.update(CuentaContable.objects.filter(pk=cuenta.pk), tipo="activo")
        with self.assertRaisesMessage(ValidationError, "no coinciden con el respaldo"):
            revision_conversion_vigente(asiento)

    def test_fallo_de_registro_revierte_ajuste_completo(self):
        asiento = self.legado()
        with patch.object(RevisionConversionAsiento, "save", side_effect=ValidationError("Respaldo rechazado")):
            with self.assertRaisesMessage(ValidationError, "Respaldo rechazado"):
                self.registrar(asiento)
        self.assertFalse(RevisionConversionAsiento.objects.exists())
        self.assertFalse(AsientoContable.objects.filter(documento_tipo="revision_conversion").exists())


class RevisionConversionFacturaTests(TestCase):
    setUp = moneda_base.MonedaContableTests.setUp
    _factura = moneda_base.MonedaContableTests._factura
    _nota = moneda_base.MonedaContableTests._nota

    def legado(self):
        factura, asiento = self._factura()
        asiento.lineas.filter(cuenta__tipo="activo").update(debe=115, moneda_origen="HNL", tipo_cambio=1)
        asiento.lineas.filter(cuenta__tipo="ingreso").update(haber=100, moneda_origen="HNL", tipo_cambio=1)
        asiento.lineas.filter(cuenta__tipo="pasivo").update(haber=15, moneda_origen="HNL", tipo_cambio=1)
        asiento.conversion_verificada = False
        asiento.save(update_fields=["conversion_verificada"])
        partidas = [{"linea_id": linea.pk, "moneda": "USD", "importe_original": linea.debe or linea.haber,
                     "tipo_cambio": 25} for linea in asiento.lineas.order_by("pk")]
        return factura, asiento, partidas

    def registrar(self, asiento, partidas):
        return registrar_revision_conversion(
            asiento=asiento, partidas=partidas, fecha=date(2026, 6, 1),
            motivo="Importes y tasas respaldados para revisar íntegramente la factura histórica", usuario=self.usuario,
        )

    def test_cliente_conserva_nominal_usd_para_cobros_posteriores(self):
        _factura, asiento, partidas = self.legado()
        partidas[0].update(moneda="HNL", tipo_cambio=1)
        with self.assertRaisesMessage(ValidationError, "importe original de cuentas por cobrar en dólares"):
            vista_previa_revision(asiento, partidas)
        partidas[0].update(moneda="USD", tipo_cambio=25, importe_original=114)
        with self.assertRaisesMessage(ValidationError, "nominal en dólares del documento"):
            vista_previa_revision(asiento, partidas)

    def test_compensa_todas_las_partidas_del_ajuste_parcial_anterior(self):
        _factura, asiento, partidas = self.legado()
        cuenta = CuentaContable.objects.create(empresa=self.empresa, codigo="3199", nombre="Corrección anterior de patrimonio", tipo="patrimonio")
        parcial = regularizar_factura_historica(
            asiento=asiento, fecha=date(2026, 5, 1), tipo_cambio=25, cuenta_contrapartida=cuenta,
            motivo="Primera revisión parcial del saldo cliente histórico", usuario=self.usuario,
        )
        original_ajuste = list(parcial.asiento_ajuste.lineas.order_by("pk").values())
        previa = vista_previa_revision(asiento, partidas)
        self.assertEqual(previa["fecha_minima"], date(2026, 5, 1))
        self.assertEqual(previa["ajustes_previos"][0]["asiento_id"], parcial.asiento_ajuste_id)
        self.assertFalse(previa["requiere_contrapartida"])
        revision = self.registrar(asiento, partidas)
        self.assertFalse(revision.ajuste.lineas.filter(cuenta=parcial.cuenta_cliente).exists())
        self.assertEqual(revision.ajuste.lineas.get(cuenta=cuenta).debe, Decimal("2760"))
        self.assertEqual(list(parcial.asiento_ajuste.lineas.order_by("pk").values()), original_ajuste)
        saldo = cuenta.lineas_asiento.aggregate(debe=Sum("debe"), haber=Sum("haber"))
        self.assertEqual(saldo["debe"], saldo["haber"])

    def test_revision_parcial_no_permite_revisar_aplicacion_aislada_hasta_factura_integral(self):
        factura, asiento, partidas = self.legado()
        pago = PagoFactura.objects.create(factura=factura, monto=50, fecha=date(2026, 4, 15))
        cliente = asiento.lineas.get(cuenta__tipo="activo").cuenta
        cobro = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
            fecha=pago.fecha, descripcion="Cobro histórico", referencia="COBRO-HIST", origen_modulo="facturacion",
            moneda_origen="USD", tipo_cambio=25, conversion_verificada=False,
            lineas=[{"cuenta": "caja", "debe": 50}, {"cuenta": cliente, "haber": 50, "detalle": "Aplicacion a cliente histórico"}],
        )
        cuenta = CuentaContable.objects.create(empresa=self.empresa, codigo="3199", nombre="Corrección parcial", tipo="patrimonio")
        regularizar_factura_historica(
            asiento=asiento, fecha=date(2026, 5, 1), tipo_cambio=25, cuenta_contrapartida=cuenta,
            motivo="Revisión parcial del cliente con aplicaciones previas", usuario=self.usuario,
        )
        partidas_cobro = [{"linea_id": linea.pk, "moneda": "USD", "importe_original": linea.debe or linea.haber,
                           "tipo_cambio": 25} for linea in cobro.lineas.order_by("pk")]
        with self.assertRaisesMessage(ValidationError, "Revise primero todas las partidas de la factura"):
            vista_previa_revision(cobro, partidas_cobro)
        revision = self.registrar(asiento, partidas)
        self.assertEqual(len(vista_previa_revision(cobro, partidas_cobro)["partidas"]), 2)
        self.assertTrue(revision.ajuste.esta_balanceado)

    def test_nota_legada_hnl_de_factura_usd_exige_aclarar_documento_original(self):
        factura, _asiento = self._factura()
        nota, asiento = self._nota(factura)
        QuerySet.update(NotaCredito.objects.filter(pk=nota.pk), moneda="HNL")
        asiento.conversion_verificada = False
        asiento.save(update_fields=["conversion_verificada"])
        partidas = [{"linea_id": linea.pk, "moneda": "USD", "importe_original": linea.debe_origen or linea.haber_origen,
                     "tipo_cambio": 25} for linea in asiento.lineas.order_by("pk")]
        with self.assertRaisesMessage(ValidationError, "La moneda de la nota de crédito no coincide con su factura"):
            vista_previa_revision(asiento, partidas)

    def test_financiera_propia_con_cuenta_contable_ajena_no_se_regulariza(self):
        factura, fuente, _partidas = self.legado()
        banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco propio con vínculo legado incorrecto", tipo="banco",
            cuenta_contable=obtener_cuenta_operativa(self.empresa, "bancos"),
        )
        pago = PagoFactura.objects.create(factura=factura, monto=50, fecha=date(2026, 4, 15), cuenta_financiera=banco)
        cliente = fuente.lineas.get(cuenta__tipo="activo").cuenta
        cobro = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
            fecha=pago.fecha, descripcion="Cobro histórico", referencia="COBRO-HIST", origen_modulo="facturacion",
            moneda_origen="USD", tipo_cambio=25, conversion_verificada=False,
            lineas=[{"cuenta": banco.cuenta_contable, "debe": 50}, {"cuenta": cliente, "haber": 50}],
        )
        otra = Empresa.objects.create(nombre="Otra empresa financiera histórica", slug="otra-empresa-financiera-historica", rtn="08011999233112")
        ajena = CuentaContable.objects.create(empresa=otra, codigo="1101", nombre="Banco confidencial ajeno", tipo="activo")
        QuerySet.update(CuentaFinanciera.objects.filter(pk=banco.pk), cuenta_contable_id=ajena.pk)
        partidas = [{"linea_id": linea.pk, "moneda": "USD", "importe_original": linea.debe or linea.haber,
                     "tipo_cambio": 25} for linea in cobro.lineas.order_by("pk")]
        with self.assertRaisesMessage(ValidationError, "sus cuentas contables deben pertenecer a esta empresa"):
            vista_previa_revision(cobro, partidas)
