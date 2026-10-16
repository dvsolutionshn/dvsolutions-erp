"""La presentación de miles no cambia el contrato decimal del formulario."""

from decimal import Decimal

from django.test import TestCase

from . import test_asiento_interfaz as interfaz
from .models import AsientoContable


class FormatoImportesAsientoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        interfaz.AsientoInterfazTests.setUpTestData.__func__(cls)

    setUp = interfaz.AsientoInterfazTests.setUp
    ruta = interfaz.AsientoInterfazTests.ruta
    datos = interfaz.AsientoInterfazTests.datos

    def test_conserva_centavos_miles_millones_y_limite_del_importe(self):
        for importe in ("0.01", "1250.00", "1250000.50", "999999999999.99"):
            with self.subTest(importe=importe):
                respuesta = self.client.post(self.ruta(), self.datos(**{
                    "lineas-0-debe": importe, "lineas-1-haber": importe,
                }))
                self.assertEqual(respuesta.status_code, 302)
                asiento = AsientoContable.objects.filter(empresa=self.empresa).latest("pk")
                self.assertEqual(asiento.total_debe, Decimal(importe))
                self.assertEqual(asiento.total_haber, Decimal(importe))
                self.assertTrue(asiento.esta_balanceado)
                self.assertEqual(asiento.lineas.get(cuenta=self.caja).debe, Decimal(importe))
                self.assertFalse(AsientoContable.objects.filter(empresa=self.otra).exists())

    def test_suma_exacta_de_centavos_en_tres_lineas(self):
        respuesta = self.client.post(self.ruta(), self.datos(**{
            "lineas-TOTAL_FORMS": "3",
            "lineas-0-debe": "0.10", "lineas-1-debe": "0.20", "lineas-1-haber": "0.00",
            "lineas-2-cuenta": str(self.ingreso.pk), "lineas-2-detalle": "Total exacto",
            "lineas-2-debe": "0.00", "lineas-2-haber": "0.30",
        }))
        self.assertEqual(respuesta.status_code, 302)
        asiento = AsientoContable.objects.get(empresa=self.empresa)
        self.assertEqual(asiento.total_debe, Decimal("0.30"))
        self.assertEqual(asiento.total_haber, Decimal("0.30"))
        self.assertTrue(asiento.esta_balanceado)

    def test_rechaza_comas_o_exceso_de_decimales_en_el_post_canonico(self):
        for importe in ("1,250.00", "1,25", "1250000.501", "1000000000000.00"):
            with self.subTest(importe=importe):
                respuesta = self.client.post(self.ruta(), self.datos(**{
                    "lineas-0-debe": importe, "lineas-1-haber": "1250000.50",
                }))
                self.assertEqual(respuesta.status_code, 200)
                formulario = respuesta.context["formset"].forms[0]
                self.assertIn("debe", formulario.errors)
                self.assertEqual(formulario["debe"].value(), importe)
                self.assertEqual(respuesta.context["formset"].forms[1]["haber"].value(), "1250000.50")
                self.assertFalse(AsientoContable.objects.exists())
