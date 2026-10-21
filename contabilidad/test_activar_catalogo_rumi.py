"""La activación solicitada conserva el catálogo y el aislamiento de RUMI."""

from datetime import date
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import Empresa
from .forms import LineaAsientoContableForm
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_balance_general, datos_estado_resultados


MIGRACION = import_module("contabilidad.migrations.0026_activar_catalogo_existente_rumi")


class ActivarCatalogoRumiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.apps_historicas = MigrationExecutor(connection).loader.project_state([
            ("contabilidad", "0025_catalogo_operativo_rumi"),
        ]).apps

    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999188501")
        cls.otra = Empresa.objects.create(nombre="RUMI Sucursal", slug="rumi-sucursal", rtn="08011999188502")
        raices = [
            CuentaContable(
                empresa=cls.rumi, codigo=codigo, nombre=nombre, tipo=tipo,
                activa=False, acepta_movimientos=False, descripcion="Jerarquía existente",
            )
            for codigo, nombre, tipo in [
                ("1", "Activo", "activo"), ("2", "Pasivo", "pasivo"),
                ("3", "Patrimonio", "patrimonio"),
                ("4", "Activos y Pasivos Contingentes", "orden"), ("7", "Cuentas de Orden", "orden"),
            ]
        ]
        CuentaContable.objects.bulk_create(raices)
        cuentas = []
        for raiz in raices:
            cantidad = 8 if raiz.tipo == "orden" else 107
            for numero in range(1, cantidad + 1):
                cuentas.append(CuentaContable(
                    empresa=cls.rumi, codigo=f"{raiz.codigo}{numero:04d}",
                    nombre=f"Cuenta existente {raiz.codigo}-{numero}", tipo=raiz.tipo,
                    cuenta_padre=raiz, activa=numero % 3 == 0 and raiz.tipo != "orden",
                    acepta_movimientos=raiz.tipo != "orden", actividad_flujo="operacion",
                    descripcion="Condicionada en la propuesta original",
                ))
        CuentaContable.objects.bulk_create(cuentas)
        cls.ajena = CuentaContable.objects.create(
            empresa=cls.otra, codigo="10001", nombre="Cuenta ajena inactiva", tipo="activo", activa=False,
        )
        cls.ajena_activa = CuentaContable.objects.create(
            empresa=cls.otra, codigo="10002", nombre="Cuenta ajena activa", tipo="activo", activa=True,
        )

    def activar(self):
        editor = SimpleNamespace(connection=connection)
        MIGRACION.activar_catalogo_existente_rumi(self.apps_historicas, editor)

    def instantanea(self, empresa, *, omitir_activa=False):
        campos = [campo.attname for campo in CuentaContable._meta.concrete_fields if not (omitir_activa and campo.name == "activa")]
        return list(CuentaContable.objects.filter(empresa=empresa).order_by("pk").values_list(*campos))

    def test_activa_las_342_cuentas_existentes_incluidas_18_de_orden(self):
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi).count(), 342)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi, tipo="orden").count(), 18)
        self.activar()
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi, activa=True).count(), 342)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi, activa=False).exists())

    def test_conserva_ids_codigos_nombres_jerarquia_tipo_y_todos_los_demas_campos(self):
        anterior = self.instantanea(self.rumi, omitir_activa=True)
        otras = self.instantanea(self.otra)
        self.activar()
        self.assertEqual(self.instantanea(self.rumi, omitir_activa=True), anterior)
        self.assertEqual(self.instantanea(self.otra), otras)

    def test_no_cambia_empresa_distinta_aunque_su_nombre_contenga_rumi(self):
        anterior = self.instantanea(self.otra)
        self.activar()
        self.assertEqual(self.instantanea(self.otra), anterior)
        self.ajena.refresh_from_db()
        self.ajena_activa.refresh_from_db()
        self.assertFalse(self.ajena.activa)
        self.assertTrue(self.ajena_activa.activa)

    def test_ejecutarla_dos_veces_no_crea_ni_cambia_cuentas(self):
        self.activar()
        primera = self.instantanea(self.rumi)
        self.activar()
        self.assertEqual(self.instantanea(self.rumi), primera)

    def test_no_crea_rumi_ni_cuentas_si_empresa_no_existe(self):
        Empresa.objects.filter(pk=self.rumi.pk).update(slug="catalogo-ya-existente")
        empresas = list(Empresa.objects.values_list("pk", "slug"))
        cuentas = list(CuentaContable.objects.values())
        self.activar()
        self.assertEqual(list(Empresa.objects.values_list("pk", "slug")), empresas)
        self.assertEqual(list(CuentaContable.objects.values()), cuentas)

    def test_no_crea_cuentas_cuando_catalogo_esta_vacio(self):
        CuentaContable.objects.filter(empresa=self.rumi).delete()
        self.activar()
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi).exists())
        self.assertTrue(Empresa.objects.filter(pk=self.rumi.pk).exists())

    def test_reversion_no_desactiva_cuentas_legitimas(self):
        self.activar()
        antes = self.instantanea(self.rumi)
        MIGRACION.Migration.operations[0].reverse_code(self.apps_historicas, SimpleNamespace(connection=connection))
        self.assertEqual(self.instantanea(self.rumi), antes)

    def test_todas_las_cuentas_de_movimiento_quedan_disponibles_solo_en_su_empresa(self):
        self.activar()
        formulario = LineaAsientoContableForm(empresa=self.rumi)
        esperadas = set(CuentaContable.objects.filter(empresa=self.rumi, acepta_movimientos=True).values_list("pk", flat=True))
        self.assertEqual(set(formulario.fields["cuenta"].queryset.values_list("pk", flat=True)), esperadas)
        self.assertEqual(len(esperadas), 321)
        self.assertFalse(formulario.fields["cuenta"].queryset.filter(tipo="orden").exists())
        self.assertNotIn(self.ajena.pk, esperadas)

    def test_orden_activa_sigue_sin_permitir_movimientos_ni_asientos_financieros(self):
        self.activar()
        control = CuentaContable.objects.get(empresa=self.rumi, codigo="4")
        control.full_clean()
        control.save(update_fields=["activa"])
        self.assertTrue(control.activa)
        self.assertFalse(control.acepta_movimientos)
        control.acepta_movimientos = True
        with self.assertRaisesMessage(ValidationError, "no admiten asientos"):
            control.full_clean()
        control.acepta_movimientos = False
        asiento = AsientoContable.objects.create(
            empresa=self.rumi, fecha=date(2026, 10, 15), descripcion="Prohibición de movimiento extracontable",
        )
        with self.assertRaisesMessage(ValidationError, "no admiten asientos"):
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=control, debe=Decimal("100.00"))

    def test_otra_empresa_con_control_heredado_no_obtiene_excepcion_rumi(self):
        CuentaContable.objects.bulk_create([CuentaContable(
            empresa=self.otra, codigo="7", nombre="Control heredado", tipo="orden",
            activa=False, acepta_movimientos=False,
        )])
        control = CuentaContable.objects.get(empresa=self.otra, codigo="7")
        control.full_clean()
        control.activa = True
        with self.assertRaisesMessage(ValidationError, "inactivas"):
            control.full_clean()
        self.otra.slug = "rumi"
        control.empresa = self.otra
        with self.assertRaisesMessage(ValidationError, "inactivas"):
            control.full_clean()

    def test_balance_y_resultados_no_incluyen_cuentas_de_orden_activadas(self):
        self.activar()
        balance = datos_balance_general(self.rumi)
        resultados = datos_estado_resultados(self.rumi)
        for grupo in ("activos", "pasivos", "patrimonio"):
            self.assertFalse(any(fila["cuenta"].tipo == "orden" for fila in balance[grupo]))
        self.assertEqual(balance["resumen"]["total_activos"], Decimal("0.00"))
        self.assertEqual(balance["resumen"]["total_pasivo_patrimonio"], Decimal("0.00"))
        self.assertTrue(balance["resumen"]["cuadrado"])
        self.assertEqual(resultados["resumen"]["utilidad"], Decimal("0.00"))

    def test_cuentas_activadas_aceptan_partidas_y_conservan_balance_cuadrado(self):
        self.activar()
        activo = CuentaContable.objects.get(empresa=self.rumi, codigo="10001")
        capital = CuentaContable.objects.get(empresa=self.rumi, codigo="30001")
        asiento = AsientoContable.objects.create(
            empresa=self.rumi, fecha=date(2026, 10, 15), descripcion="Capital con cuentas antes inactivas",
            estado="contabilizado",
        )
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=activo, debe=Decimal("1250000.50"))
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=capital, haber=Decimal("1250000.50"))
        resumen = datos_balance_general(self.rumi)["resumen"]
        self.assertEqual(resumen["total_activos"], Decimal("1250000.50"))
        self.assertEqual(resumen["total_pasivo_patrimonio"], Decimal("1250000.50"))
        self.assertTrue(resumen["cuadrado"])
