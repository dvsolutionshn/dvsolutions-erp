"""Los encabezados compartidos no desplazan ni alteran el contenido contable."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .comparacion_reportes import datos_comparacion_balance, datos_comparacion_resultados
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_balance_general, datos_estado_resultados


class EncabezadosExcelReportesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        plan = PlanComercial.objects.create(nombre="Plan encabezados", codigo="plan-encabezados", precio_mensual="99.00")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa de encabezados", slug="encabezados-reportes", rtn="08011999430101",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra = Empresa.objects.create(
            nombre="Empresa privada encabezados", slug="encabezados-privados", rtn="08011999430102",
            estado_licencia="activa", plan_comercial=plan,
        )
        rol = RolSistema.objects.create(
            nombre="Consulta de encabezados", codigo="consulta-encabezados",
            puede_contabilidad=True, puede_reportes_contables=True,
        )
        cls.usuario = Usuario.objects.create_user(username="consulta-encabezados", empresa=cls.empresa, rol_sistema=rol)
        cls.ajeno = Usuario.objects.create_user(username="ajeno-encabezados", empresa=cls.otra, rol_sistema=rol)
        cls.sin_permiso = Usuario.objects.create_user(username="sin-encabezados", empresa=cls.empresa)

        def cuenta(codigo, nombre, tipo, padre=None):
            return CuentaContable.objects.create(
                empresa=cls.empresa, codigo=codigo, nombre=nombre, tipo=tipo, cuenta_padre=padre,
            )

        activo = cuenta("1", "Activo", "activo")
        bancos = cuenta("1.1", "Bancos", "activo", activo)
        cls.caja = cuenta("1.1.01", "Caja de operaciones", "activo", bancos)
        cls.banco = cuenta("1.1.02", "Banco de operaciones", "activo", bancos)
        patrimonio = cuenta("3", "Patrimonio", "patrimonio")
        cls.capital = cuenta("3.1", "Capital", "patrimonio", patrimonio)
        ingresos = cuenta("4", "Ingresos", "ingreso")
        cls.ventas = cuenta("4.1", "Ingresos por servicios", "ingreso", ingresos)
        gastos = cuenta("6", "Gastos", "gasto")
        cls.gasto = cuenta("6.1", "Gastos administrativos", "gasto", gastos)
        cls.registro(date(2025, 12, 31), [(cls.caja, "1000000.50", 0), (cls.capital, 0, "1000000.50")])
        cls.registro(date(2026, 1, 10), [(cls.banco, "1250000.50", 0), (cls.ventas, 0, "1250000.50")])
        cls.registro(date(2026, 1, 20), [(cls.gasto, "1250.10", 0), (cls.banco, 0, "1250.10")])
        cls.registro(date(2026, 9, 5), [(cls.caja, "1250.35", 0), (cls.ventas, 0, "1250.35")])

    @classmethod
    def registro(cls, fecha, partidas):
        asiento = AsientoContable.objects.create(
            empresa=cls.empresa, fecha=fecha, numero=f"ENC-{fecha.isoformat()}",
            descripcion="Operación para validar encabezados", estado="contabilizado", creado_por=cls.usuario,
        )
        for cuenta, debe, haber in partidas:
            LineaAsientoContable.objects.create(
                asiento=asiento, cuenta=cuenta, debe=Decimal(str(debe)), haber=Decimal(str(haber)),
            )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.filtros = {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-09-30"}

    def casos(self, *, con_periodo=True):
        filtros = self.filtros if con_periodo else {}
        fechas = {"Desde": "2026-01-01", "Hasta": "2026-09-30"} if con_periodo else {}
        for nombre, firma, extras in (
            ("exportar_libro_diario_excel", ("Fecha", "Asiento"), {}),
            ("exportar_mayor_cuenta_excel", ("Fecha", "Asiento"), {}),
            ("exportar_mayor_cuenta_excel", ("Fecha", "Asiento"), {"cuenta": self.caja.pk}),
            ("exportar_libro_mayor_general_excel", ("Fecha", "Asiento"), {}),
            ("exportar_balance_comprobacion_excel", ("Cuenta", "Tipo"), {}),
            ("exportar_estado_resultados_excel", ("Código", "Cuenta"), {}),
            ("exportar_estado_flujo_efectivo_excel", ("Actividad", "Fecha"), {}),
            ("exportar_reporte_impuestos_excel", ("Resumen", "Documentos"), {}),
            ("exportar_auditoria_contable_excel", ("Fecha", "Número"), {}),
        ):
            yield nombre, firma, {**filtros, **extras}, fechas
        yield (
            "exportar_balance_general_excel", ("Código", "Cuenta"),
            {"fecha_corte": "2026-09-30"} if con_periodo else {},
            {"Hasta": "2026-09-30"} if con_periodo else {},
        )
        if con_periodo:
            for nombre in ("exportar_balance_general_excel", "exportar_estado_resultados_excel"):
                # La comparación debe publicar sus cortes mensuales reales,
                # incluso si la URL conserva filtros individuales anteriores.
                yield nombre, ("Código", "Cuenta"), {
                    "comparar": "1", "mes_inicio": "2026-01", "mes_fin": "2026-09",
                    "fecha_inicio": "2024-02-11", "fecha_fin": "2030-07-13", "fecha_corte": "2030-07-13",
                }, ({"Hasta": "2026-09-30"} if nombre == "exportar_balance_general_excel" else fechas)

    def exportar(self, nombre, parametros):
        respuesta = self.client.get(reverse(nombre, args=[self.empresa.slug]), parametros)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return load_workbook(BytesIO(respuesta.content), data_only=True).active

    def encabezado(self, hoja, firma):
        return next(
            fila[0].row for fila in hoja
            if tuple(celda.value for celda in fila[:len(firma)]) == firma
        )

    def comprobar_metadata(self, hoja, encabezado, fechas):
        self.assertTrue(hoja["A1"].value)
        self.assertTrue(hoja["A1"].font.bold)
        for etiqueta, valor in {"Empresa": self.empresa.nombre, "Moneda funcional": "Lempiras (HNL)", **fechas}.items():
            filas = [fila for fila in hoja if fila[0].value == etiqueta]
            self.assertEqual(len(filas), 1, etiqueta)
            self.assertLess(filas[0][0].row, encabezado)
            self.assertEqual(filas[0][1].value, valor)
            self.assertTrue(filas[0][0].font.bold)
        for etiqueta in {"Desde", "Hasta"} - fechas.keys():
            self.assertFalse(any(fila[0].value == etiqueta for fila in hoja))
        self.assertTrue(all(celda.font.bold for celda in hoja[encabezado] if celda.value is not None))
        self.assertEqual(hoja[hoja.freeze_panes].row, encabezado + 1)

    def test_metadata_unica_antes_de_tabla_en_todos_los_reportes_y_comparativos(self):
        for nombre, firma, parametros, fechas in self.casos():
            with self.subTest(reporte=nombre, parametros=parametros):
                hoja = self.exportar(nombre, parametros)
                encabezado = self.encabezado(hoja, firma)
                self.comprobar_metadata(hoja, encabezado, fechas)
                if parametros.get("comparar"):
                    self.assertGreaterEqual(hoja[hoja.freeze_panes].column, 3)
                    self.assertEqual(hoja.print_title_rows, f"$1:${encabezado}")
                    valores = [celda.value for fila in hoja for celda in fila]
                    self.assertNotIn("2024-02-11", valores)
                    self.assertNotIn("2030-07-13", valores)

    def test_sin_fechas_el_encabezado_no_inventa_un_periodo(self):
        for nombre, firma, parametros, fechas in self.casos(con_periodo=False):
            with self.subTest(reporte=nombre, parametros=parametros):
                hoja = self.exportar(nombre, parametros)
                self.comprobar_metadata(hoja, self.encabezado(hoja, firma), fechas)

    def test_advertencia_usd_se_conserva_una_vez_al_inicio_y_con_el_corte_correcto(self):
        for nombre, firma, parametros, fechas in self.casos():
            with self.subTest(reporte=nombre, parametros=parametros):
                with patch("contabilidad.views._conversiones_pendientes_reporte", return_value=2) as consulta:
                    hoja = self.exportar(nombre, parametros)
                avisos = [fila for fila in hoja if fila[0].value == "Conversión histórica pendiente"]
                self.assertEqual(len(avisos), 1)
                self.assertLess(avisos[0][0].row, self.encabezado(hoja, firma))
                self.assertIn("2 asientos en dólares", avisos[0][1].value)
                self.assertTrue(avisos[0][0].font.bold)
                filtros_consultados = consulta.call_args.args[1]
                self.assertEqual(filtros_consultados.get("fecha_fin") or filtros_consultados.get("fecha_corte"), fechas["Hasta"])
                if "Desde" in fechas:
                    self.assertEqual(filtros_consultados.get("fecha_inicio"), fechas["Desde"])

    def test_cuentas_principales_saldos_e_indentacion_son_los_de_los_reportes(self):
        for nombre, grupos, calcular, calcular_comparacion in (
            ("exportar_balance_general_excel", ("activos", "pasivos", "patrimonio"), datos_balance_general, datos_comparacion_balance),
            ("exportar_estado_resultados_excel", ("ingresos", "costos", "gastos"), datos_estado_resultados, datos_comparacion_resultados),
        ):
            for comparar in (False, True):
                with self.subTest(reporte=nombre, comparar=comparar):
                    parametros = (
                        {"comparar": "1", "mes_inicio": "2026-01", "mes_fin": "2026-09"} if comparar else
                        ({"fecha_corte": "2026-09-30"} if "balance_general" in nombre else self.filtros)
                    )
                    hoja = self.exportar(nombre, parametros)
                    if comparar:
                        esperadas = [fila for grupo in calcular_comparacion(self.empresa, "2026-01", "2026-09")["grupos"] for fila in grupo["filas"] if fila.get("cuenta")]
                    else:
                        datos = calcular(self.empresa, **parametros)
                        esperadas = [fila for grupo in grupos for fila in datos[f"{grupo}_jerarquia"]]
                    for fila in esperadas:
                        registro = next(registro for registro in hoja if registro[0].value == fila["cuenta"].codigo)
                        self.assertEqual(registro[1].value, fila["cuenta"].nombre)
                        self.assertEqual(registro[1].alignment.indent, fila["nivel"])
                        importes = fila["saldos"] if comparar else [fila["saldo"]]
                        for celda, esperado in zip(registro[2:], importes):
                            self.assertEqual(celda.data_type, "n")
                            self.assertEqual(Decimal(str(celda.value)), esperado)
                            self.assertEqual(celda.number_format, '#,##0.00;[Red]-#,##0.00')
                            self.assertEqual(bool(celda.font.bold), fila["es_agrupadora"])
                        self.assertEqual(bool(registro[1].font.bold), fila["es_agrupadora"])

    def test_exportaciones_no_exponen_otra_empresa_ni_omiten_permisos(self):
        nombres = {nombre for nombre, _firma, _parametros, _fechas in self.casos()}
        for usuario in (self.ajeno, self.sin_permiso):
            self.client.force_login(usuario)
            for nombre in nombres:
                with self.subTest(usuario=usuario.username, reporte=nombre):
                    respuesta = self.client.get(reverse(nombre, args=[self.empresa.slug]), self.filtros)
                    self.assertIn(respuesta.status_code, (302, 403))
                    self.assertNotIn("spreadsheetml.sheet", respuesta.get("Content-Type", ""))
