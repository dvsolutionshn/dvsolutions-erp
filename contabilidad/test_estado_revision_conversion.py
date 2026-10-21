from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.access import permiso_contabilidad_accion
from facturacion.models import PagoFactura
from . import test_regularizacion_moneda as datos_revision
from .cierre import revisar_cierre_periodo
from .models import CuentaContable, CuentaFinanciera, LineaAsientoContable, PeriodoContable, RegularizacionMonedaFactura
from .reportes import datos_flujo_efectivo
from .regularizacion_moneda import regularizar_factura_historica
from .revision_conversion import registrar_revision_conversion
from .services import obtener_cuenta_operativa, registrar_asiento_documento
from .views import _conversiones_pendientes_reporte


class EstadoRevisionConversionTests(TestCase):
    setUp = datos_revision.RegularizacionMonedaInterfazTests.setUp
    datos = datos_revision.RegularizacionMonedaInterfazTests.datos
    revisar = datos_revision.RegularizacionMonedaInterfazTests.revisar

    def revision_integral(self):
        return registrar_revision_conversion(
            asiento=self.asiento, fecha=date(2026, 5, 5), usuario=self.usuario,
            motivo="Documento y tasa históricos revisados con respaldo contable",
            cuenta_contrapartida=None,
            partidas=[{
                "linea_id": linea.pk, "moneda": "USD", "tipo_cambio": Decimal("25"),
                "importe_original": linea.debe or linea.haber,
            } for linea in self.asiento.lineas.order_by("pk")],
        )

    def test_cierre_y_reportes_respetan_fecha_efectiva_de_revision_completa(self):
        self.revision_integral()
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4)
        self.assertEqual(revisar_cierre_periodo(self.empresa, periodo)["control"]["conversiones_pendientes"], 1)
        self.assertEqual(_conversiones_pendientes_reporte(self.empresa, {"fecha_fin": "2026-04-30"}), 1)
        self.assertEqual(_conversiones_pendientes_reporte(self.empresa, {"fecha_fin": "2026-05-05"}), 0)
        self.assertEqual(_conversiones_pendientes_reporte(self.otra, {}), 0)

    def test_revision_solo_cxc_no_libera_conversion_integral(self):
        regularizar_factura_historica(
            asiento=self.asiento, fecha=date(2026, 5, 5), tipo_cambio=25,
            cuenta_contrapartida=self.contrapartida,
            motivo="Revisión documentada únicamente del saldo del cliente", usuario=self.usuario,
        )
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4)
        self.assertEqual(revisar_cierre_periodo(self.empresa, periodo)["control"]["conversiones_pendientes"], 1)
        self.assertEqual(_conversiones_pendientes_reporte(self.empresa, {}), 1)

    def test_asiento_y_partidas_revisados_no_se_editan_ni_se_eliminan(self):
        revision = self.revision_integral()
        self.asiento.referencia = "Referencia alterada después de revisar"
        with self.assertRaisesMessage(ValidationError, "revisión integral"):
            self.asiento.save(update_fields=["referencia"])
        with self.assertRaises(ValidationError):
            self.asiento.lineas.update(debe=Decimal("1"))
        with self.assertRaises(ValidationError):
            self.asiento.lineas.all().delete()
        with self.assertRaises(ValidationError):
            LineaAsientoContable.objects.create(asiento=self.asiento, cuenta=self.contrapartida, debe=1)
        with self.assertRaises(ValidationError):
            revision.ajuste.lineas.all().delete()

    def test_documento_fuente_no_cambia_despues_de_revision(self):
        self.revision_integral()
        self.factura.estado = "anulada"
        with self.assertRaisesMessage(ValidationError, "revisión integral"):
            self.factura.save(update_fields=["estado"])
        with self.assertRaises(ValidationError):
            self.factura.lineas.all().delete()

    def test_cuenta_revisada_conserva_empresa_y_tipo(self):
        self.revision_integral()
        cuenta = self.asiento.lineas.first().cuenta
        cuenta.tipo = "pasivo" if cuenta.tipo != "pasivo" else "activo"
        with self.assertRaisesMessage(ValidationError, "revisión integral"):
            cuenta.save(update_fields=["tipo"])

    def test_revision_parcial_no_puede_crearse_despues_de_integral(self):
        self.revision_integral()
        with self.assertRaisesMessage(ValidationError, "revisión integral"):
            regularizar_factura_historica(
                asiento=self.asiento, fecha=date(2026, 5, 6), tipo_cambio=25,
                cuenta_contrapartida=self.contrapartida, motivo="Una revisión parcial posterior sería duplicada", usuario=self.usuario,
            )

    def test_firma_parcial_rechaza_cuenta_cambiada_con_mismos_importes(self):
        token = self.revisar()
        cuenta = CuentaContable.objects.create(empresa=self.empresa, codigo="110999", nombre="Otro cliente histórico", tipo="activo")
        partida = self.asiento.lineas.get(detalle__startswith="Cobro a cliente")
        partida.cuenta = cuenta
        partida.save(update_fields=["cuenta"])
        respuesta = self.client.post(self.url, self.datos(accion="registrar", confirmar="1", revision_token=token))
        self.assertContains(respuesta, "Los datos del ajuste cambiaron")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_ruta_integral_exige_permiso_para_contabilizar(self):
        self.assertEqual(permiso_contabilidad_accion("asientos/12/revisar-conversion/"), "puede_contabilizar_asientos")

    def test_ajuste_historico_de_banco_se_concilia_sin_generar_cobro_nuevo(self):
        self.revision_integral()
        banco = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco histórico HNL", tipo="banco",
            cuenta_contable=obtener_cuenta_operativa(self.empresa, "bancos"),
        )
        pago = PagoFactura.objects.create(factura=self.factura, monto=50, fecha=date(2026, 4, 15), cuenta_financiera=banco)
        cuenta_cliente = self.asiento.lineas.get(detalle__startswith="Cobro a cliente").cuenta
        cobro = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="pago_factura", documento_id=pago.pk, evento="cobro",
            fecha=pago.fecha, descripcion="Cobro histórico nominal en dólares", referencia="Cobro para revisión",
            origen_modulo="facturacion",
            moneda_origen="USD", tipo_cambio=25, conversion_verificada=False,
            lineas=[{"cuenta": banco.cuenta_contable, "debe": 50}, {"cuenta": cuenta_cliente, "haber": 50}],
        )
        registrar_revision_conversion(
            asiento=cobro, fecha=date(2026, 5, 6), usuario=self.usuario,
            cuenta_contrapartida=self.contrapartida, motivo="Respaldo del banco y de la aplicación histórica del cliente",
            partidas=[{
                "linea_id": linea.pk, "moneda": "USD", "importe_original": Decimal("50"),
                "tipo_cambio": Decimal("26") if linea.cuenta_id == banco.cuenta_contable_id else Decimal("25"),
            } for linea in cobro.lineas.all()],
        )
        datos = datos_flujo_efectivo(self.empresa, "2026-05-01", "2026-05-31")
        self.assertEqual(datos["resumen"]["total_neto"], Decimal("0"))
        self.assertEqual(datos["resumen"]["ajustes_historicos"], Decimal("1250"))
        self.assertEqual(datos["resumen"]["saldo_final"] - datos["resumen"]["saldo_inicial"], Decimal("1250"))
        self.assertEqual(datos["resumen"]["diferencia"], Decimal("0"))
        self.assertEqual(len(datos["ajustes_historicos"]), 1)
