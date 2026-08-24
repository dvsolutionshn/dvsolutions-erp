from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import Empresa
from .models import (
    BodegaInventario,
    Cliente,
    ExistenciaLoteBodega,
    Factura,
    LineaFactura,
    LoteInventario,
    MovimientoLoteBodega,
    Producto,
    TipoImpuesto,
)
from .views import _registrar_ajuste_salida_fefo, _registrar_salida_fefo_factura


class SalidaFefoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia FEFO",
            slug="hospital_mia",
            rtn="08011999111111",
        )
        self.bodega = BodegaInventario.objects.create(
            empresa=self.empresa,
            nombre="Bodega General",
            tipo="principal",
        )
        self.producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Gomitas Capilares",
            precio=Decimal("100.00"),
        )
        self.cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente FEFO",
            rtn="08011999121212",
        )
        self.impuesto = TipoImpuesto.objects.create(
            nombre="Exento FEFO",
            porcentaje=Decimal("0.00"),
        )

    def crear_existencia(self, numero, vencimiento, cantidad):
        lote = LoteInventario.objects.create(
            empresa=self.empresa,
            producto=self.producto,
            numero_lote=numero,
            fecha_vencimiento=vencimiento,
        )
        existencia = ExistenciaLoteBodega.objects.create(
            empresa=self.empresa,
            bodega=self.bodega,
            lote=lote,
            cantidad=Decimal(cantidad),
        )
        return lote, existencia

    def crear_factura(self, cantidad):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado="borrador",
        )
        LineaFactura.objects.create(
            factura=factura,
            producto=self.producto,
            cantidad=Decimal(cantidad),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        return factura

    def test_descuenta_primero_el_lote_que_vence_antes_y_continua_en_el_siguiente(self):
        hoy = timezone.localdate()
        lote_primero, existencia_primera = self.crear_existencia(
            "LOTE-01", hoy + timedelta(days=10), "5.00"
        )
        lote_segundo, existencia_segunda = self.crear_existencia(
            "LOTE-02", hoy + timedelta(days=200), "10.00"
        )
        lote_vencido, existencia_vencida = self.crear_existencia(
            "LOTE-VENCIDO", hoy - timedelta(days=1), "20.00"
        )
        factura = self.crear_factura("7.00")

        self.assertTrue(_registrar_salida_fefo_factura(factura))

        existencia_primera.refresh_from_db()
        existencia_segunda.refresh_from_db()
        existencia_vencida.refresh_from_db()
        self.assertEqual(existencia_primera.cantidad, Decimal("0.00"))
        self.assertEqual(existencia_segunda.cantidad, Decimal("8.00"))
        self.assertEqual(existencia_vencida.cantidad, Decimal("20.00"))
        movimientos = list(
            MovimientoLoteBodega.objects.filter(
                factura=factura,
                tipo="salida_factura",
            ).order_by("id")
        )
        self.assertEqual([movimiento.lote_id for movimiento in movimientos], [lote_primero.id, lote_segundo.id])
        self.assertEqual([movimiento.cantidad for movimiento in movimientos], [Decimal("5.00"), Decimal("2.00")])
        self.assertNotIn(lote_vencido.id, [movimiento.lote_id for movimiento in movimientos])

    def test_stock_insuficiente_no_deja_salidas_parciales(self):
        _, existencia = self.crear_existencia(
            "LOTE-UNICO", timezone.localdate() + timedelta(days=30), "3.00"
        )
        factura = self.crear_factura("4.00")

        with self.assertRaisesMessage(ValidationError, "Stock FEFO insuficiente"):
            _registrar_salida_fefo_factura(factura)

        existencia.refresh_from_db()
        self.assertEqual(existencia.cantidad, Decimal("3.00"))
        self.assertFalse(MovimientoLoteBodega.objects.filter(factura=factura).exists())

    def test_no_aplica_fefo_a_empresa_fuera_del_grupo(self):
        otra_empresa = Empresa.objects.create(
            nombre="Empresa sin FEFO",
            slug="empresa_sin_fefo",
            rtn="08011999333333",
        )
        otro_cliente = Cliente.objects.create(empresa=otra_empresa, nombre="Cliente comun")
        factura = Factura.objects.create(empresa=otra_empresa, cliente=otro_cliente, estado="borrador")

        self.assertFalse(_registrar_salida_fefo_factura(factura))

    def test_rechaza_existencia_que_cruza_empresas(self):
        otra_empresa = Empresa.objects.create(
            nombre="Otra empresa",
            slug="otra_empresa_fefo",
            rtn="08011999444444",
        )
        otra_bodega = BodegaInventario.objects.create(
            empresa=otra_empresa,
            nombre="Bodega ajena",
            tipo="principal",
        )
        lote, _ = self.crear_existencia(
            "LOTE-SEGURO", timezone.localdate() + timedelta(days=30), "1.00"
        )
        existencia_cruzada = ExistenciaLoteBodega(
            empresa=self.empresa,
            bodega=otra_bodega,
            lote=lote,
            cantidad=Decimal("1.00"),
        )

        with self.assertRaises(ValidationError):
            existencia_cruzada.full_clean()

    def test_ajuste_negativo_tambien_respeta_fefo_y_deja_trazabilidad(self):
        hoy = timezone.localdate()
        lote_primero, existencia_primera = self.crear_existencia(
            "LOTE-AJUSTE-01", hoy + timedelta(days=20), "2.00"
        )
        lote_segundo, existencia_segunda = self.crear_existencia(
            "LOTE-AJUSTE-02", hoy + timedelta(days=90), "5.00"
        )

        _registrar_ajuste_salida_fefo(
            empresa=self.empresa,
            producto=self.producto,
            cantidad=Decimal("3.00"),
            referencia="Consumo clinico",
            observacion="Prueba de trazabilidad.",
        )

        existencia_primera.refresh_from_db()
        existencia_segunda.refresh_from_db()
        self.assertEqual(existencia_primera.cantidad, Decimal("0.00"))
        self.assertEqual(existencia_segunda.cantidad, Decimal("4.00"))
        movimientos = MovimientoLoteBodega.objects.filter(tipo="ajuste_salida").order_by("id")
        self.assertEqual(
            [(movimiento.lote_id, movimiento.cantidad) for movimiento in movimientos],
            [(lote_primero.id, Decimal("2.00")), (lote_segundo.id, Decimal("1.00"))],
        )
