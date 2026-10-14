from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Empresa, Usuario
from facturacion.models import Cliente, Factura, LineaFactura, PagoFactura, TipoImpuesto
from rrhh.models import PeriodoPlanilla

from .models import (
    AsientoContable, CuentaContable, CuentaFinanciera, LineaAsientoContable,
    MovimientoBancario, PeriodoContable,
)
from .services import registrar_asiento_documento, registrar_asiento_pago_cliente


class ProteccionPeriodoContableTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="Empresa protegida", slug="empresa-protegida", rtn="08011999100001")
        self.otra_empresa = Empresa.objects.create(nombre="Empresa independiente", slug="empresa-independiente", rtn="08011999100002")
        self.cuenta = CuentaContable.objects.create(
            empresa=self.empresa, codigo="1101", nombre="Caja", tipo="activo"
        )
        self.contrapartida = CuentaContable.objects.create(
            empresa=self.empresa, codigo="4101", nombre="Ingresos", tipo="ingreso"
        )
        self.otra_cuenta = CuentaContable.objects.create(
            empresa=self.otra_empresa, codigo="1101", nombre="Caja independiente", tipo="activo"
        )
        self.asiento = self.crear_asiento(date(2026, 4, 10))
        self.linea = LineaAsientoContable.objects.create(
            asiento=self.asiento, cuenta=self.cuenta, debe=Decimal("100.00")
        )
        LineaAsientoContable.objects.create(
            asiento=self.asiento, cuenta=self.contrapartida, haber=Decimal("100.00")
        )
        self.asiento_abierto = self.crear_asiento(date(2026, 5, 10))
        self.periodo = PeriodoContable.objects.create(
            empresa=self.empresa, anio=2026, mes=4, estado="cerrado"
        )

    def crear_asiento(self, fecha, **datos):
        return AsientoContable.objects.create(
            empresa=self.empresa, fecha=fecha, descripcion="Asiento de prueba",
            estado="contabilizado", **datos
        )

    def test_no_se_puede_mover_asiento_cerrado_a_mes_abierto(self):
        self.asiento.fecha = date(2026, 5, 1)
        with self.assertRaises(ValidationError):
            self.asiento.save()
        self.asiento.refresh_from_db()
        self.assertEqual(self.asiento.fecha, date(2026, 4, 10))

    def test_no_se_puede_mover_asiento_abierto_a_mes_cerrado(self):
        self.asiento_abierto.fecha = date(2026, 4, 1)
        with self.assertRaises(ValidationError):
            self.asiento_abierto.save(update_fields=["fecha"])

    def test_no_se_puede_editar_asiento_cerrado(self):
        self.asiento.descripcion = "Alteración del historial"
        with self.assertRaises(ValidationError):
            self.asiento.save(update_fields=["descripcion"])

    def test_borrado_por_consulta_de_documento_conserva_asiento_y_lineas(self):
        with self.assertRaises(ValidationError):
            AsientoContable.objects.filter(empresa=self.empresa, pk=self.asiento.pk).delete()
        self.assertTrue(AsientoContable.objects.filter(pk=self.asiento.pk).exists())
        self.assertEqual(self.asiento.lineas.count(), 2)

    def test_borrado_individual_verifica_fecha_guardada(self):
        self.asiento.fecha = date(2026, 5, 1)
        with self.assertRaises(ValidationError):
            self.asiento.delete()

    def test_actualizacion_masiva_no_puede_saltar_mes_cerrado(self):
        with self.assertRaises(ValidationError):
            AsientoContable.objects.filter(pk=self.asiento.pk).update(fecha=date(2026, 5, 1))
        self.asiento.fecha = date(2026, 5, 1)
        with self.assertRaises(ValidationError):
            AsientoContable.objects.bulk_update([self.asiento], ["fecha"])

    def test_actualizacion_masiva_rechaza_expresiones_sin_validacion(self):
        with self.assertRaises(ValidationError):
            AsientoContable.objects.filter(pk=self.asiento_abierto.pk).update(descripcion=models.F("descripcion"))

    def test_cambio_en_mes_abierto_se_conserva(self):
        cantidad = AsientoContable.objects.filter(pk=self.asiento_abierto.pk).update(descripcion="Corrección permitida")
        self.assertEqual(cantidad, 1)
        self.asiento_abierto.refresh_from_db()
        self.assertEqual(self.asiento_abierto.descripcion, "Corrección permitida")

    def test_cierre_de_una_empresa_no_bloquea_la_otra(self):
        asiento = AsientoContable.objects.create(
            empresa=self.otra_empresa, fecha=date(2026, 4, 10), descripcion="Empresa independiente"
        )
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.otra_cuenta, debe=100)
        self.assertEqual(asiento.lineas.count(), 1)

    def test_actualizacion_en_lote_respeta_la_empresa_filtrada(self):
        ajeno = AsientoContable.objects.create(
            empresa=self.otra_empresa, fecha=date(2026, 5, 10), descripcion="Asiento independiente"
        )
        ajeno.descripcion = "Cambio desde una consulta de otra empresa"
        cantidad = AsientoContable.objects.filter(empresa=self.empresa).bulk_update([ajeno], ["descripcion"])
        self.assertEqual(cantidad, 0)
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.descripcion, "Asiento independiente")

    def test_no_se_puede_cambiar_empresa_de_asiento_abierto(self):
        self.asiento_abierto.empresa = self.otra_empresa
        with self.assertRaises(ValidationError):
            self.asiento_abierto.save(update_fields=["empresa"])

    def test_cuenta_ajena_no_se_guarda_en_linea(self):
        with self.assertRaises(ValidationError):
            LineaAsientoContable.objects.create(
                asiento=self.asiento_abierto, cuenta=self.otra_cuenta, debe=100
            )

    def test_no_se_puede_agregar_linea_a_asiento_cerrado(self):
        with self.assertRaises(ValidationError):
            LineaAsientoContable.objects.create(asiento=self.asiento, cuenta=self.cuenta, debe=20)

    def test_no_se_puede_editar_linea_cerrada_ni_moverla_a_asiento_abierto(self):
        self.linea.asiento = self.asiento_abierto
        self.linea.debe = 200
        with self.assertRaises(ValidationError):
            self.linea.save()
        self.linea.refresh_from_db()
        self.assertEqual(self.linea.asiento_id, self.asiento.pk)
        self.assertEqual(self.linea.debe, 100)

    def test_actualizacion_y_borrado_de_lineas_por_consulta_respetan_cierre(self):
        with self.assertRaises(ValidationError):
            self.asiento.lineas.update(asiento=self.asiento_abierto)
        with self.assertRaises(ValidationError):
            self.asiento.lineas.all().delete()
        with self.assertRaises(ValidationError):
            self.linea.delete()

    def test_insercion_masiva_de_lineas_respeta_cierre(self):
        with self.assertRaises(ValidationError):
            LineaAsientoContable.objects.bulk_create([
                LineaAsientoContable(asiento=self.asiento, cuenta=self.cuenta, debe=30)
            ])

    def test_insercion_masiva_de_asientos_respeta_cierre(self):
        with self.assertRaises(ValidationError):
            AsientoContable.objects.bulk_create([
                AsientoContable(empresa=self.empresa, fecha=date(2026, 4, 5), descripcion="Inserción en lote")
            ])

    def test_periodo_no_puede_cambiar_identidad_ni_estado_por_edicion(self):
        for cambios in ({"mes": 5}, {"anio": 2027}, {"empresa": self.otra_empresa}, {"estado": "abierto"}):
            with self.subTest(cambios=cambios):
                periodo = PeriodoContable.objects.get(pk=self.periodo.pk)
                for campo, valor in cambios.items():
                    setattr(periodo, campo, valor)
                with self.assertRaises(ValidationError):
                    periodo.save()

    def test_periodo_no_se_puede_reabrir_por_actualizacion_ni_eliminar(self):
        with self.assertRaises(ValidationError):
            PeriodoContable.objects.filter(pk=self.periodo.pk).update(estado="abierto")
        with self.assertRaises(ValidationError):
            PeriodoContable.objects.filter(pk=self.periodo.pk).delete()

    def test_periodo_rechaza_anios_fuera_del_calendario(self):
        for anio in (0, 10000):
            with self.subTest(anio=anio), self.assertRaises(ValidationError):
                PeriodoContable.objects.create(empresa=self.empresa, anio=anio, mes=12)

    def test_periodo_admite_extremos_validos_del_calendario(self):
        for anio in (1, 9999):
            with self.subTest(anio=anio):
                periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=anio, mes=12)
                self.assertEqual(periodo.fecha_inicio, date(anio, 12, 1))
                self.assertEqual(periodo.fecha_fin, date(anio, 12, 31))

    def test_reapertura_explicita_habilita_correccion_y_cierre_es_idempotente(self):
        self.periodo.cambiar_estado("abierto")
        self.asiento.descripcion = "Corrección después de reapertura"
        self.asiento.save(update_fields=["descripcion"])
        usuario = Usuario.objects.create_user(username="responsable", password="prueba", empresa=self.empresa)
        fecha_cierre = timezone.now()
        self.periodo.cambiar_estado("cerrado", cerrado_por=usuario, fecha_cierre=fecha_cierre)
        self.periodo.cambiar_estado("cerrado")
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.cerrado_por, usuario)
        self.assertEqual(self.periodo.fecha_cierre, fecha_cierre)

    def test_ajuste_abierto_conserva_asiento_cerrado_original(self):
        ajuste = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="asiento_contable", documento_id=self.asiento.pk,
            evento="reversion", fecha=date(2026, 5, 1), descripcion="Ajuste posterior",
            referencia=str(self.asiento.pk), origen_modulo="contabilidad", lineas=[
                {"cuenta": self.cuenta, "haber": 100},
                {"cuenta": self.contrapartida, "debe": 100},
            ],
        )
        self.assertEqual(ajuste.estado, "contabilizado")
        self.asiento.refresh_from_db()
        self.assertEqual(self.asiento.fecha, date(2026, 4, 10))
        self.assertEqual(self.asiento.lineas.count(), 2)

    def test_movimiento_bancario_no_se_puede_mover_ni_eliminar_desde_mes_cerrado(self):
        self.periodo.cambiar_estado("abierto")
        cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco", cuenta_contable=self.cuenta
        )
        movimiento = MovimientoBancario.objects.create(
            empresa=self.empresa, cuenta_financiera=cuenta_financiera, fecha=date(2026, 4, 10),
            descripcion="Depósito histórico", credito=100,
        )
        self.periodo.cambiar_estado("cerrado")
        movimiento.fecha = date(2026, 5, 1)
        with self.assertRaises(ValidationError):
            movimiento.save()
        with self.assertRaises(ValidationError):
            MovimientoBancario.objects.filter(pk=movimiento.pk).update(fecha=date(2026, 5, 1))
        with self.assertRaises(ValidationError):
            MovimientoBancario.objects.filter(pk=movimiento.pk).delete()

    def crear_factura_historica(self):
        self.periodo.cambiar_estado("abierto")
        cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente histórico")
        factura = Factura.objects.create(empresa=self.empresa, cliente=cliente, fecha_emision=date(2026, 4, 10))
        impuesto = TipoImpuesto.objects.create(nombre="Exento", porcentaje=0)
        linea = LineaFactura.objects.create(
            factura=factura, descripcion_manual="Servicio", cantidad=1, precio_unitario=100, impuesto=impuesto
        )
        Factura.objects.filter(pk=factura.pk).update(
            estado="emitida", subtotal=100, total=100, total_lempiras=100
        )
        factura.refresh_from_db()
        asiento = self.crear_asiento(date(2026, 4, 10), documento_tipo="factura", documento_id=factura.pk, evento="emision")
        self.periodo.cambiar_estado("cerrado")
        return factura, linea, asiento

    def test_factura_historica_no_permite_mover_fecha_ni_cambiar_importe(self):
        factura, _linea, _asiento = self.crear_factura_historica()
        for campo, valor in (("fecha_emision", date(2026, 5, 1)), ("total", Decimal("200.00"))):
            with self.subTest(campo=campo):
                factura.refresh_from_db()
                setattr(factura, campo, valor)
                with self.assertRaises(ValidationError):
                    factura.save(update_fields=[campo])

    def test_factura_historica_no_permite_borrado_por_consulta(self):
        factura, _linea, asiento = self.crear_factura_historica()
        with self.assertRaises(ValidationError), transaction.atomic():
            Factura.objects.filter(pk=factura.pk).delete()
        self.assertTrue(Factura.objects.filter(pk=factura.pk).exists())
        self.assertTrue(AsientoContable.objects.filter(pk=asiento.pk).exists())

    def test_linea_de_factura_historica_no_permite_edicion_borrado_ni_movimiento(self):
        factura, linea, _asiento = self.crear_factura_historica()
        linea.precio_unitario = 200
        with self.assertRaises(ValidationError):
            linea.save()
        with self.assertRaises(ValidationError), transaction.atomic():
            factura.lineas.all().delete()
        nueva_factura = Factura.objects.create(
            empresa=self.empresa, cliente=factura.cliente, fecha_emision=date(2026, 5, 1)
        )
        linea.factura = nueva_factura
        with self.assertRaises(ValidationError):
            linea.save()

    def test_cobro_posterior_actualiza_estado_pago_sin_modificar_mes_cerrado(self):
        factura, _linea, _asiento = self.crear_factura_historica()
        pago = PagoFactura.objects.create(factura=factura, fecha=date(2026, 5, 5), monto=25)
        asiento_pago = registrar_asiento_pago_cliente(pago)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, "parcial")
        self.assertEqual(asiento_pago.fecha, date(2026, 5, 5))
        self.assertEqual(asiento_pago.estado, "contabilizado")

    def test_pago_con_asiento_cerrado_no_se_puede_eliminar_por_consulta(self):
        factura, _linea, _asiento = self.crear_factura_historica()
        self.periodo.cambiar_estado("abierto")
        pago = PagoFactura.objects.create(factura=factura, fecha=date(2026, 4, 11), monto=25)
        registrar_asiento_pago_cliente(pago)
        self.periodo.cambiar_estado("cerrado")
        with self.assertRaises(ValidationError), transaction.atomic():
            PagoFactura.objects.filter(pk=pago.pk).delete()
        self.assertTrue(PagoFactura.objects.filter(pk=pago.pk).exists())

    def test_planilla_con_asiento_cerrado_no_se_puede_eliminar_por_consulta(self):
        self.periodo.cambiar_estado("abierto")
        planilla = PeriodoPlanilla.objects.create(
            empresa=self.empresa, nombre="Planilla abril", fecha_inicio=date(2026, 4, 1),
            fecha_fin=date(2026, 4, 30), fecha_pago=date(2026, 5, 1), estado="pagada",
        )
        self.crear_asiento(date(2026, 4, 30), documento_tipo="planilla", documento_id=planilla.pk, evento="cierre")
        self.periodo.cambiar_estado("cerrado")
        with self.assertRaises(ValidationError), transaction.atomic():
            PeriodoPlanilla.objects.filter(pk=planilla.pk).delete()
        self.assertTrue(PeriodoPlanilla.objects.filter(pk=planilla.pk).exists())
