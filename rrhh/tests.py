from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from crm.models import ConfiguracionCRM

from .models import DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla
from .services import generar_planilla


class RRHHTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="Empresa RRHH", slug="empresa-rrhh", rtn="08011999111112", estado_licencia="activa")
        self.modulo, _ = Modulo.objects.get_or_create(codigo="rrhh", defaults={"nombre": "Recursos Humanos", "es_comercial": True})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo, activo=True)
        self.rol = RolSistema.objects.create(
            nombre="RRHH Total",
            codigo="rrhh-total",
            puede_rrhh=True,
            puede_empleados=True,
            puede_planillas=True,
            puede_vacaciones=True,
            puede_configuracion_rrhh=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="rrhh",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )

    def test_generar_planilla_calcula_deducciones_y_14avo(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-001",
            nombres="Ana",
            apellidos="Lopez",
            identidad="0801199900001",
            fecha_ingreso=date(2026, 5, 1),
            salario_mensual=Decimal("30000.00"),
            telefono="99999999",
            correo="ana@example.com",
        )
        MovimientoPlanilla.objects.create(
            empleado=empleado,
            tipo="bono",
            descripcion="Bono productividad",
            monto=Decimal("1000.00"),
            fecha=date(2026, 6, 25),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Junio 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 6, 1),
            fecha_fin=date(2026, 6, 30),
            fecha_pago=date(2026, 6, 30),
            incluir_14avo=True,
        )

        creados = generar_planilla(periodo)

        self.assertEqual(creados, 1)
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)
        self.assertEqual(detalle.salario_base, Decimal("30000.00"))
        self.assertEqual(detalle.bonos, Decimal("1000.00"))
        self.assertGreater(detalle.decimo_cuarto, Decimal("0.00"))
        self.assertGreater(detalle.ihss, Decimal("0.00"))
        self.assertGreater(detalle.rap, Decimal("0.00"))
        self.assertGreater(detalle.neto_pagar, Decimal("0.00"))

    def test_dashboard_rrhh_responde_con_permiso(self):
        self.client.login(username="rrhh", password="pass12345")
        response = self.client.get(reverse("rrhh_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recursos Humanos")

    def test_editar_detalle_planilla_recalcula_neto_por_empleado(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-002",
            nombres="Carlos",
            apellidos="Mejia",
            identidad="0801199900002",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("12000.00"),
            telefono="99999999",
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Enero 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 1, 30),
            fecha_pago=date(2026, 1, 30),
        )
        generar_planilla(periodo)
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)

        self.client.login(username="rrhh", password="pass12345")
        response = self.client.post(
            reverse("editar_detalle_planilla", args=[self.empresa.slug, detalle.id]),
            {
                "dias_pagados": "30.00",
                "salario_base": "12000.00",
                "horas_extra_diurnas": "2.00",
                "horas_extra_nocturnas": "0.00",
                "horas_extra_feriado": "0.00",
                "monto_horas_extra": "0.00",
                "bonos": "500.00",
                "comisiones": "250.00",
                "decimo_cuarto": "0.00",
                "ihss": "297.58",
                "rap": "180.00",
                "isr": "0.00",
                "prestamos": "300.00",
                "otras_deducciones": "100.00",
                "observacion": "Ajuste manual revisado.",
            },
        )

        self.assertRedirects(response, reverse("ver_planilla", args=[self.empresa.slug, periodo.id]))
        detalle.refresh_from_db()
        self.assertEqual(detalle.prestamos, Decimal("300.00"))
        self.assertEqual(detalle.otras_deducciones, Decimal("100.00"))
        self.assertEqual(detalle.bonos, Decimal("500.00"))
        self.assertGreater(detalle.monto_horas_extra, Decimal("0.00"))
        self.assertEqual(detalle.total_deducciones, Decimal("877.58"))
        self.assertGreater(detalle.neto_pagar, Decimal("11800.00"))

    def test_no_permite_editar_detalle_de_planilla_cerrada(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-003",
            nombres="Maria",
            apellidos="Reyes",
            identidad="0801199900003",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("10000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Febrero 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 2, 1),
            fecha_fin=date(2026, 2, 28),
            fecha_pago=date(2026, 2, 28),
            estado="cerrada",
        )
        detalle = DetallePlanilla.objects.create(periodo=periodo, empleado=empleado, salario_base=Decimal("10000.00"))

        self.client.login(username="rrhh", password="pass12345")
        response = self.client.get(reverse("editar_detalle_planilla", args=[self.empresa.slug, detalle.id]))

        self.assertRedirects(response, reverse("ver_planilla", args=[self.empresa.slug, periodo.id]))

    def test_whatsapp_incluye_resumen_detallado_del_voucher(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-004",
            nombres="Lucia",
            apellidos="Flores",
            identidad="0801199900004",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("15000.00"),
            telefono="99999999",
            banco="Banco Test",
            cuenta_bancaria="123456789",
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Marzo 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 3, 1),
            fecha_fin=date(2026, 3, 30),
            fecha_pago=date(2026, 3, 30),
        )
        generar_planilla(periodo)
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)

        texto = parse_qs(urlparse(detalle.whatsapp_url).query)["text"][0]

        self.assertIn("resumen de tu voucher", texto)
        self.assertIn("Banco Test", texto)
        self.assertIn("123456789", texto)
        self.assertIn("Total devengado", texto)
        self.assertIn("Total deducciones", texto)
        self.assertIn("Neto a pagar", texto)

    def test_voucher_pdf_muestra_cuenta_acreditada(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-005",
            nombres="Jose",
            apellidos="Molina",
            identidad="0801199900005",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("18000.00"),
            banco="Banco Atlantida",
            cuenta_bancaria="000111222333",
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Abril 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 4, 1),
            fecha_fin=date(2026, 4, 30),
            fecha_pago=date(2026, 4, 30),
        )
        generar_planilla(periodo)
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)

        self.client.login(username="rrhh", password="pass12345")
        response = self.client.get(reverse("voucher_planilla_pdf", args=[self.empresa.slug, detalle.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    @patch("rrhh.views.enviar_mensaje_whatsapp_texto")
    def test_envia_voucher_por_whatsapp_api(self, mock_enviar):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-006",
            nombres="Mario",
            apellidos="Pineda",
            identidad="0801199900006",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("16000.00"),
            telefono="99998888",
            banco="Banco de Occidente",
            cuenta_bancaria="44556677",
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Mayo 2026",
            frecuencia="mensual",
            fecha_inicio=date(2026, 5, 1),
            fecha_fin=date(2026, 5, 31),
            fecha_pago=date(2026, 5, 31),
        )
        generar_planilla(periodo)
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "phone-id"
        config.whatsapp_token = "token"
        config.save()

        self.client.login(username="rrhh", password="pass12345")
        response = self.client.post(reverse("enviar_voucher_whatsapp_api", args=[self.empresa.slug, detalle.id]))

        self.assertRedirects(response, reverse("ver_planilla", args=[self.empresa.slug, periodo.id]))
        self.assertTrue(mock_enviar.called)
        argumentos = mock_enviar.call_args[0]
        self.assertEqual(argumentos[1], "50499998888")
        self.assertIn("Neto a pagar", argumentos[2])

# Create your tests here.
