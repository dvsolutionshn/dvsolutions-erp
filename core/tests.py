from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, Modulo, PlanComercial, RolSistema, Usuario
from core.models import PagoLicenciaEmpresa
from core.forms import EmpresaControlForm


class SuperAdminControlTests(TestCase):
    def setUp(self):
        cache.clear()
        self.superadmin = Usuario.objects.create_user(
            username="master",
            password="pass12345",
            is_superuser=True,
            is_staff=True,
        )
        self.usuario_normal = Usuario.objects.create_user(
            username="operador",
            password="pass12345",
        )
        self.modulo = Modulo.objects.create(nombre="Facturacion", codigo="facturacion")
        self.group = Group.objects.create(name="Ventas")
        self.plan = PlanComercial.objects.create(nombre="Plan Pro", codigo="plan-pro", precio_mensual="99.99")
        self.rol_facturador = RolSistema.objects.create(
            nombre="Facturador",
            codigo="facturador",
            puede_facturas=True,
            puede_recibos=True,
        )

    def test_dashboard_privado_redirige_a_login(self):
        response = self.client.get(reverse("superadmin_dashboard"))
        self.assertRedirects(response, "/control/login/?next=/control/")

    def test_usuario_no_superadmin_no_puede_entrar(self):
        self.client.login(username="operador", password="pass12345")
        response = self.client.get(reverse("superadmin_dashboard"))
        self.assertRedirects(response, reverse("superadmin_login"))

    def test_superadmin_puede_crear_empresa_con_modulo(self):
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_empresa_create"),
            {
                "nombre": "Empresa Control",
                "slug": "empresa-control",
                "rtn": "08011999000001",
                "pais": "Honduras",
                "condiciones_pago": "Pago inmediato",
                "activa": "on",
                "modulos_activos": [self.modulo.id],
            },
        )
        self.assertRedirects(response, reverse("superadmin_empresas"))
        empresa = Empresa.objects.get(slug="empresa-control")
        self.assertEqual(empresa.empresamodulo_set.filter(activo=True).count(), 1)

    def test_superadmin_puede_crear_usuario_con_rol(self):
        empresa = Empresa.objects.create(nombre="Demo", slug="demo", rtn="08011999000002")
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "username": "nuevo",
                "first_name": "Nuevo",
                "last_name": "Usuario",
                "email": "nuevo@demo.com",
                "empresa": empresa.id,
                "rol_sistema": self.rol_facturador.id,
                "es_administrador_empresa": "on",
                "is_active": "on",
                "is_staff": "on",
                "groups": [self.group.id],
                "password1": "ClaveSegura123",
                "password2": "ClaveSegura123",
            },
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"))
        usuario = Usuario.objects.get(username="nuevo")
        self.assertEqual(usuario.empresa, empresa)
        self.assertTrue(usuario.es_administrador_empresa)
        self.assertTrue(usuario.groups.filter(name="Ventas").exists())
        self.assertEqual(usuario.rol_sistema, self.rol_facturador)

    def test_plan_habilita_modulo_para_empresa(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Plan",
            slug="empresa-plan",
            rtn="08011999000003",
            plan_comercial=self.plan,
        )
        from core.models import PlanModulo

        PlanModulo.objects.create(plan=self.plan, modulo=self.modulo, activo=True)
        self.assertTrue(empresa.tiene_modulo_activo("facturacion"))

    def test_superadmin_puede_ver_ficha_empresa(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Ficha",
            slug="empresa-ficha",
            rtn="08011999000004",
            plan_comercial=self.plan,
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.get(reverse("superadmin_empresa_detail", args=[empresa.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Empresa Ficha")

    def test_superadmin_puede_ver_catalogo_modulos(self):
        self.client.login(username="master", password="pass12345")
        response = self.client.get(reverse("superadmin_modulos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Facturacion")

    def test_formularios_solo_muestran_modulos_comerciales(self):
        Modulo.objects.create(nombre="Inventario", codigo="inventario", es_comercial=False)
        self.client.login(username="master", password="pass12345")

        response_planes = self.client.get(reverse("superadmin_plan_create"))
        response_empresas = self.client.get(reverse("superadmin_empresa_create"))

        self.assertContains(response_planes, "Facturacion")
        self.assertNotContains(response_planes, "Inventario")
        self.assertContains(response_empresas, "Facturacion")
        self.assertNotContains(response_empresas, "Inventario")

    def test_empresa_control_form_valida_fechas_de_licencia(self):
        form = EmpresaControlForm(data={
            "nombre": "Empresa Licencia",
            "slug": "empresa-licencia",
            "rtn": "08011999000005",
            "pais": "Honduras",
            "condiciones_pago": "Pago inmediato",
            "estado_licencia": "activa",
            "fecha_inicio_plan": "2026-04-10",
            "fecha_vencimiento_plan": "2026-04-01",
            "activa": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("fecha_vencimiento_plan", form.errors)

    def test_dashboard_bloquea_empresa_con_licencia_vencida(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Vencida",
            slug="empresa-vencida",
            rtn="08011999000006",
            estado_licencia="activa",
            fecha_vencimiento_plan=timezone.localdate() - timedelta(days=1),
        )
        usuario = Usuario.objects.create_user(
            username="usuario_vencido",
            password="pass12345",
            empresa=empresa,
        )
        self.client.login(username="usuario_vencido", password="pass12345")
        response = self.client.get(reverse("dashboard", args=[empresa.slug]))
        self.assertRedirects(response, reverse("empresa_login", args=[empresa.slug]))

    def test_superadmin_puede_ver_estado_comercial_en_ficha_empresa(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Comercial",
            slug="empresa-comercial",
            rtn="08011999000007",
            estado_licencia="suspendida",
            fecha_inicio_plan=timezone.localdate(),
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=30),
            observaciones_comerciales="Pendiente de confirmar renovacion.",
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.get(reverse("superadmin_empresa_detail", args=[empresa.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Suspendida")
        self.assertContains(response, "Pendiente de confirmar renovacion.")

    def test_empresa_nueva_entra_con_prueba_de_siete_dias(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Trial",
            slug="empresa-trial",
            rtn="08011999000008",
        )
        self.assertEqual(empresa.estado_licencia, "prueba")
        self.assertIsNotNone(empresa.fecha_inicio_plan)
        self.assertIsNotNone(empresa.fecha_vencimiento_plan)
        self.assertEqual((empresa.fecha_vencimiento_plan - empresa.fecha_inicio_plan).days, 7)

    def test_pago_licencia_activa_y_extiende_vencimiento(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Pago",
            slug="empresa-pago",
            rtn="08011999000009",
            estado_licencia="suspendida",
        )
        pago = PagoLicenciaEmpresa.objects.create(
            empresa=empresa,
            plan_comercial=self.plan,
            cantidad_meses=3,
            monto="300.00",
        )
        empresa.aplicar_pago_licencia(pago)
        empresa.refresh_from_db()
        self.assertEqual(empresa.estado_licencia, "activa")
        self.assertTrue(empresa.licencia_operativa)

    def test_superadmin_puede_registrar_pago_licencia(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Cobro",
            slug="empresa-cobro",
            rtn="08011999000010",
            estado_licencia="vencida",
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_empresa_registrar_pago_licencia", args=[empresa.id]),
            {
                "plan_comercial": self.plan.id,
                "fecha_pago": "2026-04-10",
                "cantidad_meses": 12,
                "monto": "1200.00",
                "metodo": "transferencia",
                "referencia": "TRX-001",
                "observacion": "Pago anual",
            },
        )
        self.assertRedirects(response, reverse("superadmin_empresa_detail", args=[empresa.id]))
        empresa.refresh_from_db()
        self.assertEqual(empresa.estado_licencia, "activa")
        self.assertEqual(empresa.pagos_licencia.count(), 1)

    def test_superadmin_puede_ver_panel_licencias(self):
        self.client.login(username="master", password="pass12345")
        response = self.client.get(reverse("superadmin_licencias"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Licencias Comerciales")

    def test_superadmin_puede_suspender_empresa(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Suspendible",
            slug="empresa-suspendible",
            rtn="08011999000011",
            estado_licencia="activa",
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=30),
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.post(reverse("superadmin_empresa_suspender_licencia", args=[empresa.id]))
        self.assertRedirects(response, reverse("superadmin_empresa_detail", args=[empresa.id]))
        empresa.refresh_from_db()
        self.assertEqual(empresa.estado_licencia, "suspendida")

    def test_superadmin_puede_activar_empresa_suspendida_con_vigencia(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Reactivable",
            slug="empresa-reactivable",
            rtn="08011999000012",
            estado_licencia="suspendida",
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=15),
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.post(reverse("superadmin_empresa_activar_licencia", args=[empresa.id]))
        self.assertRedirects(response, reverse("superadmin_empresa_detail", args=[empresa.id]))
        empresa.refresh_from_db()
        self.assertEqual(empresa.estado_licencia, "activa")

    def test_superadmin_suspender_licencia_requiere_post(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Solo Post",
            slug="empresa-solo-post",
            rtn="08011999000013",
            estado_licencia="activa",
        )
        self.client.login(username="master", password="pass12345")
        response = self.client.get(reverse("superadmin_empresa_suspender_licencia", args=[empresa.id]))
        self.assertEqual(response.status_code, 405)

    @override_settings(LOGIN_THROTTLE_LIMIT=3, LOGIN_THROTTLE_WINDOW_SECONDS=60)
    def test_login_empresa_bloquea_acceso_tras_varios_intentos_fallidos(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Login",
            slug="empresa-login",
            rtn="08011999000014",
        )
        Usuario.objects.create_user(
            username="usuario_login",
            password="ClaveCorrecta123",
            empresa=empresa,
        )

        login_url = reverse("empresa_login", args=[empresa.slug])
        for _ in range(3):
            self.client.post(login_url, {"username": "usuario_login", "password": "incorrecta"})

        response = self.client.post(login_url, {"username": "usuario_login", "password": "ClaveCorrecta123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bloqueamos temporalmente este acceso")

    @override_settings(LOGIN_THROTTLE_LIMIT=3, LOGIN_THROTTLE_WINDOW_SECONDS=60)
    def test_superadmin_login_bloquea_acceso_tras_varios_intentos_fallidos(self):
        login_url = reverse("superadmin_login")
        for _ in range(3):
            self.client.post(login_url, {"username": "master", "password": "incorrecta"})

        response = self.client.post(login_url, {"username": "master", "password": "pass12345"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bloqueamos temporalmente el acceso maestro")

    def test_configuracion_avanzada_no_habilita_cai_historico_por_nombre(self):
        empresa = Empresa.objects.create(
            nombre="AMKT Digital",
            slug="amkt-digital",
            rtn="08011999000015",
        )
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(empresa)
        self.assertFalse(configuracion.permite_cai_historico)

    def test_empresa_control_form_puede_activar_cai_historico(self):
        empresa = Empresa.objects.create(
            nombre="AMKT Digital",
            slug="amkt_digital_2024",
            rtn="08011999000016",
        )
        form = EmpresaControlForm(
            data={
                "nombre": empresa.nombre,
                "slug": empresa.slug,
                "rtn": empresa.rtn,
                "pais": empresa.pais,
                "condiciones_pago": empresa.condiciones_pago,
                "estado_licencia": empresa.estado_licencia,
                "fecha_inicio_plan": empresa.fecha_inicio_plan,
                "fecha_vencimiento_plan": empresa.fecha_vencimiento_plan,
                "activa": "on",
                "permite_cai_historico": "on",
            },
            instance=empresa,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(empresa)
        self.assertTrue(configuracion.permite_cai_historico)

    def test_empresa_control_form_puede_desactivar_cai_historico(self):
        empresa = Empresa.objects.create(
            nombre="Digital Planning",
            slug="digital-planning",
            rtn="0801199900001701",
        )
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])
        form = EmpresaControlForm(
            data={
                "nombre": empresa.nombre,
                "slug": empresa.slug,
                "rtn": empresa.rtn,
                "pais": empresa.pais,
                "condiciones_pago": empresa.condiciones_pago,
                "estado_licencia": empresa.estado_licencia,
                "fecha_inicio_plan": empresa.fecha_inicio_plan,
                "fecha_vencimiento_plan": empresa.fecha_vencimiento_plan,
                "activa": "on",
            },
            instance=empresa,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        configuracion.refresh_from_db()
        self.assertFalse(configuracion.permite_cai_historico)

    def test_empresa_control_form_puede_activar_plantilla_factura_independiente(self):
        empresa = Empresa.objects.create(
            nombre="Integrated Sales And Services",
            slug="integrated-sales-and-services",
            rtn="0801199900001702",
        )
        form = EmpresaControlForm(
            data={
                "nombre": empresa.nombre,
                "slug": empresa.slug,
                "rtn": empresa.rtn,
                "pais": empresa.pais,
                "condiciones_pago": empresa.condiciones_pago,
                "estado_licencia": empresa.estado_licencia,
                "fecha_inicio_plan": empresa.fecha_inicio_plan,
                "fecha_vencimiento_plan": empresa.fecha_vencimiento_plan,
                "activa": "on",
                "permite_plantilla_factura_independiente": "on",
            },
            instance=empresa,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(empresa)
        self.assertTrue(configuracion.permite_plantilla_factura_independiente)

    def test_asistente_consulta_responde_segun_contexto_facturacion(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Asistente",
            slug="empresa-asistente",
            rtn="08011999000018",
        )
        usuario = Usuario.objects.create_user(
            username="usuario_asistente",
            password="pass12345",
            empresa=empresa,
        )
        self.client.login(username="usuario_asistente", password="pass12345")

        response = self.client.post(
            reverse("asistente_consulta", args=[empresa.slug]),
            {
                "pregunta": "Como registrar un pago parcial",
                "pagina": f"/{empresa.slug}/dashboard/facturacion/facturas/",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["context_label"], "Cobros y pagos")
        self.assertIn("registrar un pago", payload["title"].lower())
        self.assertTrue(payload["steps"])

    def test_asistente_consulta_bloquea_usuario_de_otra_empresa(self):
        empresa_a = Empresa.objects.create(
            nombre="Empresa A",
            slug="empresa-a",
            rtn="08011999000019",
        )
        empresa_b = Empresa.objects.create(
            nombre="Empresa B",
            slug="empresa-b",
            rtn="08011999000020",
        )
        usuario = Usuario.objects.create_user(
            username="usuario_otra_empresa",
            password="pass12345",
            empresa=empresa_b,
        )
        self.client.login(username="usuario_otra_empresa", password="pass12345")

        response = self.client.post(
            reverse("asistente_consulta", args=[empresa_a.slug]),
            {
                "pregunta": "Como crear una factura",
                "pagina": f"/{empresa_a.slug}/dashboard/facturacion/crear/",
            },
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(ALLOWED_HOSTS=["digital-planning.erp.test", "testserver"])
    def test_login_por_subdominio_redirige_a_dashboard_host(self):
        empresa = Empresa.objects.create(
            nombre="Digital Planning",
            slug="digital-planning",
            rtn="08011999000021",
        )
        Usuario.objects.create_user(
            username="usuario_subdominio",
            password="pass12345",
            empresa=empresa,
        )

        response = self.client.post(
            "/",
            {"username": "usuario_subdominio", "password": "pass12345"},
            HTTP_HOST="digital-planning.erp.test",
        )

        self.assertRedirects(
            response,
            "/dashboard/",
            fetch_redirect_response=False,
        )

        dashboard_response = self.client.get("/dashboard/", HTTP_HOST="digital-planning.erp.test")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Digital Planning")
