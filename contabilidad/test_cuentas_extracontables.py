from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Empresa
from .forms import CuentaContableForm
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_balance_general, datos_estado_resultados


class CuentasExtracontablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999166337")
        cls.padre = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="7", nombre="Cuentas de orden", tipo="orden", activa=False, acepta_movimientos=False,
        )

    def test_cuenta_de_control_no_se_habilita_para_asientos(self):
        self.padre.activa = True
        with self.assertRaisesMessage(ValidationError, "inactivas"):
            self.padre.full_clean()
        self.padre.activa = False
        self.padre.acepta_movimientos = True
        with self.assertRaisesMessage(ValidationError, "no admiten"):
            self.padre.full_clean()

    def test_controles_no_entran_en_estados_financieros(self):
        balance = datos_balance_general(self.empresa)
        resultados = datos_estado_resultados(self.empresa)
        self.assertEqual(balance["resumen"]["total_activos"], Decimal("0"))
        self.assertEqual(balance["resumen"]["total_pasivos"], Decimal("0"))
        self.assertEqual(resultados["resumen"]["total_ingresos"], Decimal("0"))
        self.assertEqual(resultados["resumen"]["total_gastos"], Decimal("0"))

    def test_partida_en_control_se_rechaza_incluso_fuera_de_formulario(self):
        asiento = AsientoContable.objects.create(empresa=self.empresa, fecha=date(2026, 10, 14), descripcion="Prueba de control")
        with self.assertRaisesMessage(ValidationError, "no admiten asientos"):
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.padre, debe=10)

    def test_formulario_conserva_padre_de_control_inactivo(self):
        hija = CuentaContable.objects.create(
            empresa=self.empresa, codigo="701", nombre="Cartera en seguimiento", tipo="orden",
            cuenta_padre=self.padre, activa=False, acepta_movimientos=False,
        )
        form = CuentaContableForm(empresa=self.empresa, instance=hija)
        self.assertIn(self.padre, form.fields["cuenta_padre"].queryset)

    def test_control_y_cuenta_financiera_no_comparten_jerarquia(self):
        financiera = CuentaContable(empresa=self.empresa, codigo="1101", nombre="Caja", tipo="activo", cuenta_padre=self.padre)
        with self.assertRaisesMessage(ValidationError, "jerarquías separadas"):
            financiera.full_clean()
