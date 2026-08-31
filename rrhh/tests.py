from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from crm.models import ConfiguracionCRM
from contabilidad.models import AsientoContable, CuentaContable, CuentaFinanciera

from .models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla
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

    def test_generar_planilla_permita_dias_trabajados_por_empleado(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-DIAS-001",
            nombres="Rosa",
            apellidos="Sanchez",
            identidad="0801199900123",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("30000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla con ausencias",
            frecuencia="mensual",
            fecha_inicio=date(2026, 7, 1),
            fecha_fin=date(2026, 7, 30),
            fecha_pago=date(2026, 7, 30),
        )
        self.client.login(username="rrhh", password="pass12345")

        response = self.client.post(
            reverse("generar_planilla", args=[self.empresa.slug, periodo.id]),
            {f"dias_empleado_{empleado.id}": "28.00"},
        )

        self.assertRedirects(response, reverse("ver_planilla", args=[self.empresa.slug, periodo.id]))
        detalle = DetallePlanilla.objects.get(periodo=periodo, empleado=empleado)
        self.assertEqual(detalle.dias_pagados, Decimal("28.00"))
        self.assertEqual(detalle.salario_base, Decimal("28000.00"))

    def test_cerrar_y_pagar_planilla_genera_asientos_balanceados(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-CONT-001",
            nombres="Maria",
            apellidos="Contable",
            identidad="0801199900099",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("1000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla contable junio",
            fecha_inicio=date(2026, 6, 1),
            fecha_fin=date(2026, 6, 30),
            fecha_pago=date(2026, 6, 30),
            estado="calculada",
            creado_por=self.usuario,
        )
        DetallePlanilla.objects.create(
            periodo=periodo,
            empleado=empleado,
            salario_base=Decimal("1000.00"),
            total_devengado=Decimal("1000.00"),
            ihss=Decimal("50.00"),
            rap=Decimal("20.00"),
            isr=Decimal("30.00"),
            prestamos=Decimal("40.00"),
            otras_deducciones=Decimal("10.00"),
            total_deducciones=Decimal("150.00"),
            neto_pagar=Decimal("850.00"),
        )
        banco = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110299", nombre="Banco planilla", tipo="activo"
        )
        cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Banco planilla", tipo="banco", cuenta_contable=banco
        )
        self.client.login(username="rrhh", password="pass12345")

        response = self.client.post(reverse("cerrar_planilla", args=[self.empresa.slug, periodo.id]))
        self.assertEqual(response.status_code, 302)
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrada")
        cierre = AsientoContable.objects.get(documento_tipo="planilla", documento_id=periodo.id, evento="cierre")
        self.assertEqual(cierre.total_debe, Decimal("1000.00"))
        self.assertEqual(cierre.total_haber, Decimal("1000.00"))
        self.assertTrue(cierre.lineas.filter(cuenta__codigo="610101", debe=Decimal("1000.00")).exists())
        self.assertTrue(cierre.lineas.filter(cuenta__codigo="210301", haber=Decimal("850.00")).exists())

        response = self.client.post(
            reverse("pagar_planilla", args=[self.empresa.slug, periodo.id]),
            {"cuenta_financiera": cuenta_financiera.id},
        )
        self.assertEqual(response.status_code, 302)
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "pagada")
        self.assertEqual(periodo.cuenta_financiera_pago, cuenta_financiera)
        self.assertEqual(periodo.metodo_pago, "transferencia")
        pago = AsientoContable.objects.get(documento_tipo="planilla", documento_id=periodo.id, evento="pago")
        self.assertEqual(pago.total_debe, Decimal("850.00"))
        self.assertEqual(pago.total_haber, Decimal("850.00"))
        self.assertTrue(pago.lineas.filter(cuenta=banco, haber=Decimal("850.00")).exists())

        self.client.post(
            reverse("pagar_planilla", args=[self.empresa.slug, periodo.id]),
            {"cuenta_financiera": cuenta_financiera.id},
        )
        self.assertEqual(
            AsientoContable.objects.filter(documento_tipo="planilla", documento_id=periodo.id, evento="pago").count(),
            1,
        )

    def test_pagar_planilla_registra_metodo_efectivo(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-EFE-001",
            nombres="Luis",
            apellidos="Caja",
            identidad="0801199900199",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("1000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla efectivo",
            fecha_inicio=date(2026, 7, 1),
            fecha_fin=date(2026, 7, 30),
            fecha_pago=date(2026, 7, 30),
            estado="cerrada",
            creado_por=self.usuario,
        )
        DetallePlanilla.objects.create(
            periodo=periodo,
            empleado=empleado,
            salario_base=Decimal("1000.00"),
            total_devengado=Decimal("1000.00"),
            neto_pagar=Decimal("1000.00"),
        )
        caja_contable = CuentaContable.objects.create(
            empresa=self.empresa, codigo="110101", nombre="Caja general", tipo="activo"
        )
        caja = CuentaFinanciera.objects.create(
            empresa=self.empresa, nombre="Caja General", tipo="caja", cuenta_contable=caja_contable
        )
        self.client.login(username="rrhh", password="pass12345")

        response = self.client.post(
            reverse("pagar_planilla", args=[self.empresa.slug, periodo.id]),
            {"cuenta_financiera": caja.id, "metodo_pago": "efectivo"},
        )

        self.assertRedirects(response, reverse("ver_planilla", args=[self.empresa.slug, periodo.id]))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "pagada")
        self.assertEqual(periodo.metodo_pago, "efectivo")

    def test_solo_dueno_puede_eliminar_planillas_y_empleados_rrhh(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-DEL-001",
            nombres="Prueba",
            apellidos="Borrar",
            identidad="0801199900299",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("1000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla prueba borrar",
            fecha_inicio=date(2026, 7, 1),
            fecha_fin=date(2026, 7, 30),
            fecha_pago=date(2026, 7, 30),
        )
        self.client.login(username="rrhh", password="pass12345")

        response = self.client.post(reverse("eliminar_planilla_rrhh", args=[self.empresa.slug, periodo.id]))

        self.assertRedirects(response, reverse("planillas_rrhh", args=[self.empresa.slug]))
        self.assertTrue(PeriodoPlanilla.objects.filter(id=periodo.id).exists())

        dueno = Usuario.objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(dueno)
        response = self.client.post(reverse("eliminar_planilla_rrhh", args=[self.empresa.slug, periodo.id]))
        self.assertRedirects(response, reverse("planillas_rrhh", args=[self.empresa.slug]))
        self.assertFalse(PeriodoPlanilla.objects.filter(id=periodo.id).exists())

        response = self.client.post(reverse("eliminar_empleado_rrhh", args=[self.empresa.slug, empleado.id]))
        self.assertRedirects(response, reverse("empleados_rrhh", args=[self.empresa.slug]))
        self.assertFalse(Empleado.objects.filter(id=empleado.id).exists())

    def test_no_borra_empleado_con_planillas_asociadas(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-PROT-001",
            nombres="Empleado",
            apellidos="Protegido",
            identidad="0801199900399",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("1000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla asociada",
            fecha_inicio=date(2026, 7, 1),
            fecha_fin=date(2026, 7, 30),
            fecha_pago=date(2026, 7, 30),
        )
        DetallePlanilla.objects.create(periodo=periodo, empleado=empleado, salario_base=Decimal("1000.00"))
        dueno = Usuario.objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(dueno)

        response = self.client.post(reverse("eliminar_empleado_rrhh", args=[self.empresa.slug, empleado.id]))

        self.assertRedirects(response, reverse("ver_empleado", args=[self.empresa.slug, empleado.id]))
        self.assertTrue(Empleado.objects.filter(id=empleado.id).exists())

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

        dueno = Usuario.objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(dueno)
        response = self.client.post(
            reverse("editar_detalle_planilla", args=[self.empresa.slug, detalle.id]),
            {
                "dias_pagados": "30.00",
                "salario_base": "13500.00",
                "horas_extra_diurnas": "2.00",
                "horas_extra_nocturnas": "0.00",
                "horas_extra_feriado": "0.00",
                "monto_horas_extra": "0.00",
                "bonos": "500.00",
                "comisiones": "250.00",
                "decimo_tercero": "0.00",
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
        self.assertEqual(detalle.salario_base, Decimal("13500.00"))
        self.assertEqual(detalle.prestamos, Decimal("300.00"))
        self.assertEqual(detalle.otras_deducciones, Decimal("100.00"))
        self.assertEqual(detalle.bonos, Decimal("500.00"))
        self.assertGreater(detalle.monto_horas_extra, Decimal("0.00"))
        self.assertEqual(detalle.total_deducciones, Decimal("877.58"))
        self.assertGreater(detalle.neto_pagar, Decimal("13300.00"))
        self.assertEqual(detalle.editado_por, dueno)
        self.assertIsNotNone(detalle.fecha_ultima_edicion)

    def test_usuario_normal_no_puede_editar_planilla_por_url_directa(self):
        empleado = Empleado.objects.create(
            empresa=self.empresa,
            codigo="EMP-SEG-1",
            nombres="Sin",
            apellidos="Permiso",
            fecha_ingreso=date(2026, 1, 1),
            salario_mensual=Decimal("10000.00"),
        )
        periodo = PeriodoPlanilla.objects.create(
            empresa=self.empresa,
            nombre="Planilla protegida",
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 1, 30),
            fecha_pago=date(2026, 1, 30),
        )
        detalle = DetallePlanilla.objects.create(periodo=periodo, empleado=empleado, salario_base=Decimal("10000.00"))

        self.client.force_login(self.usuario)
        response = self.client.get(reverse("editar_detalle_planilla", args=[self.empresa.slug, detalle.id]))

        self.assertRedirects(response, reverse("planillas_rrhh", args=[self.empresa.slug]))

    def test_dueno_puede_autorizar_otro_editor_de_planilla(self):
        dueno = Usuario.objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(dueno)

        response = self.client.post(
            reverse("configurar_editores_planilla", args=[self.empresa.slug]),
            {"editores_planilla": [self.usuario.id]},
        )

        self.assertRedirects(response, reverse("planillas_rrhh", args=[self.empresa.slug]))
        config = ConfiguracionRRHHEmpresa.objects.get(empresa=self.empresa)
        self.assertTrue(config.editores_planilla.filter(pk=self.usuario.pk).exists())

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

        config, _ = ConfiguracionRRHHEmpresa.objects.get_or_create(empresa=self.empresa)
        config.editores_planilla.add(self.usuario)
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
