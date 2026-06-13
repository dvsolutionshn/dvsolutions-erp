from datetime import timedelta
import io
import json
import tempfile
import zipfile

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, Modulo, PlanComercial, RolSistema, SolicitudComercial, Usuario
from core.models import PagoLicenciaEmpresa, RespaldoEmpresa, TokenAccesoUsuario, TokenRespaldoEmpresa
from core.backup_tokens import hash_token_respaldo
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

    def test_public_home_responde_en_raiz(self):
        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DV Solutions")
        self.assertContains(response, "Solicitar propuesta o demo")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_public_home_puede_registrar_solicitud_comercial(self):
        response = self.client.post(
            reverse("public_home"),
            {
                "nombre_contacto": "Daniela Rivera",
                "empresa_interesada": "Rivera Logistics",
                "rtn_empresa": "08011999111223",
                "correo": "daniela@rivera.com",
                "telefono": "9999-8888",
                "servicio_interes": "erp",
                "mensaje": "Necesitamos un ERP y una web corporativa.",
                "solicita_prueba": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SolicitudComercial.objects.count(), 1)
        solicitud = SolicitudComercial.objects.get()
        self.assertTrue(solicitud.solicita_prueba)
        self.assertEqual(solicitud.rtn_empresa, "08011999111223")
        self.assertContains(response, "Tu solicitud ya fue registrada correctamente.")
        self.assertContains(response, "En localhost la notificacion se genero en consola")

    def test_public_demo_detalle_responde(self):
        response = self.client.get(reverse("public_demo_detail", args=["facturacion"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vista demo de factura empresarial")
        self.assertContains(response, "Solicitar una demo comercial de facturacion")

    def test_public_access_redirige_a_empresa_por_slug(self):
        empresa = Empresa.objects.create(nombre="Acceso Demo", slug="acceso-demo", rtn="08011999000040")
        response = self.client.post(reverse("public_access"), {"slug": empresa.slug})
        self.assertRedirects(response, reverse("empresa_login", args=[empresa.slug]), fetch_redirect_response=False)

    def test_csrf_vencido_en_control_redirige_a_login_con_mensaje(self):
        empresa = Empresa.objects.create(nombre="Empresa CSRF", slug="empresa-csrf", rtn="08011999000041")
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(reverse("superadmin_empresa_activar_licencia", args=[empresa.id]), follow=True)

        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertContains(response, "Vuelve a iniciar sesion para continuar.")

    def test_csrf_vencido_en_empresa_redirige_a_login_con_mensaje(self):
        empresa = Empresa.objects.create(nombre="Empresa Sesion", slug="empresa-sesion", rtn="08011999000042")
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(reverse("dashboard", args=[empresa.slug]), follow=True)

        self.assertRedirects(response, reverse("empresa_login", args=[empresa.slug]))
        self.assertContains(response, "Vuelve a iniciar sesion para continuar.")

    def test_usuario_no_superadmin_no_puede_entrar(self):
        self.client.login(username="operador", password="pass12345")
        response = self.client.get(reverse("superadmin_dashboard"))
        self.assertRedirects(response, reverse("superadmin_login"))

    def test_solo_superadmin_puede_generar_respaldo(self):
        empresa = Empresa.objects.create(nombre="Empresa Protegida", slug="empresa-protegida", rtn="08011999000055")
        self.client.login(username="operador", password="pass12345")

        response = self.client.post(reverse("superadmin_empresa_generar_respaldo", args=[empresa.id]))

        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertEqual(RespaldoEmpresa.objects.count(), 0)

    def test_respaldo_empresa_genera_zip_aislado_con_manifiesto(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            empresa = Empresa.objects.create(
                nombre="Empresa Uno",
                slug="empresa-uno",
                rtn="08011999000056",
                logo=SimpleUploadedFile("logo-prueba.png", b"imagen-prueba", content_type="image/png"),
            )
            otra_empresa = Empresa.objects.create(nombre="Empresa Dos", slug="empresa-dos", rtn="08011999000057")
            Usuario.objects.create_user(
                username="usuario-empresa-uno",
                password="pass12345",
                empresa=empresa,
            )
            Usuario.objects.create_user(
                username="usuario-empresa-dos",
                password="pass12345",
                empresa=otra_empresa,
            )
            self.client.login(username="master", password="pass12345")

            response = self.client.post(reverse("superadmin_empresa_generar_respaldo", args=[empresa.id]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/zip")
            archive_bytes = b"".join(response.streaming_content)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as backup_zip:
                names = backup_zip.namelist()
                self.assertIn("manifest.json", names)
                self.assertIn("datos.json", names)
                self.assertIn("LEEME_RESTAURACION.txt", names)
                self.assertTrue(any(name.startswith("media/logos/logo-prueba") for name in names))
                manifest = json.loads(backup_zip.read("manifest.json"))
                data = json.loads(backup_zip.read("datos.json"))

            self.assertEqual(manifest["empresa"]["slug"], empresa.slug)
            self.assertEqual(manifest["archivos_media"], 1)
            serialized_users = [
                item["fields"]["username"]
                for item in data
                if item["model"] == "core.usuario"
            ]
            self.assertIn("usuario-empresa-uno", serialized_users)
            self.assertNotIn("usuario-empresa-dos", serialized_users)
            registro = RespaldoEmpresa.objects.get()
            self.assertEqual(registro.estado, "exitoso")
            self.assertGreater(registro.registros_incluidos, 0)
            self.assertEqual(registro.archivos_incluidos, 1)
            self.assertEqual(len(registro.sha256), 64)

    def test_superadmin_emite_token_sin_guardar_el_codigo_visible(self):
        empresa = Empresa.objects.create(nombre="Empresa Token", slug="empresa-token", rtn="08011999000058")
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_empresa_generar_token_respaldo", args=[empresa.id]),
            {"horas_vigencia": "24", "referencia_pago": "TRX-45821"},
        )

        self.assertEqual(response.status_code, 200)
        token = TokenRespaldoEmpresa.objects.get()
        self.assertEqual(len(token.token_hash), 64)
        self.assertNotContains(response, token.token_hash)
        self.assertContains(response, "DVS-RSP-")
        self.assertEqual(token.referencia_pago, "TRX-45821")

    def test_nuevo_token_revoca_autorizacion_anterior_sin_usar(self):
        empresa = Empresa.objects.create(nombre="Empresa Renovacion", slug="empresa-renovacion", rtn="08011999000063")
        anterior = TokenRespaldoEmpresa.objects.create(
            empresa=empresa,
            token_hash=hash_token_respaldo("DVS-RSP-anterior"),
            token_preview="DVS-RSP-ante...rior",
            creado_por=self.superadmin,
            fecha_expiracion=timezone.now() + timedelta(hours=24),
        )
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_empresa_generar_token_respaldo", args=[empresa.id]),
            {"horas_vigencia": "48"},
        )

        self.assertEqual(response.status_code, 200)
        anterior.refresh_from_db()
        self.assertTrue(anterior.revocado)
        self.assertIsNotNone(anterior.fecha_revocacion)
        self.assertEqual(TokenRespaldoEmpresa.objects.filter(empresa=empresa).count(), 2)

    def test_administrador_empresa_descarga_respaldo_con_token_una_sola_vez(self):
        empresa = Empresa.objects.create(nombre="Empresa Cliente", slug="empresa-cliente", rtn="08011999000059")
        administrador = Usuario.objects.create_user(
            username="admin-cliente",
            password="pass12345",
            empresa=empresa,
            es_administrador_empresa=True,
        )
        token_raw = "DVS-RSP-token-prueba-seguro"
        autorizacion = TokenRespaldoEmpresa.objects.create(
            empresa=empresa,
            token_hash=hash_token_respaldo(token_raw),
            token_preview="DVS-RSP-toke...guro",
            creado_por=self.superadmin,
            fecha_expiracion=timezone.now() + timedelta(hours=24),
        )
        self.client.force_login(administrador)

        page_response = self.client.get(reverse("empresa_respaldo", args=[empresa.slug]))
        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "Mi respaldo empresarial")

        response = self.client.post(
            reverse("empresa_respaldo", args=[empresa.slug]),
            {"token_respaldo": token_raw},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        b"".join(response.streaming_content)
        autorizacion.refresh_from_db()
        self.assertIsNotNone(autorizacion.fecha_uso)
        self.assertEqual(autorizacion.usado_por, administrador)
        self.assertEqual(RespaldoEmpresa.objects.filter(empresa=empresa, estado="exitoso").count(), 1)

        segundo_intento = self.client.post(
            reverse("empresa_respaldo", args=[empresa.slug]),
            {"token_respaldo": token_raw},
        )
        self.assertRedirects(segundo_intento, reverse("empresa_respaldo", args=[empresa.slug]))
        self.assertEqual(RespaldoEmpresa.objects.filter(empresa=empresa, estado="exitoso").count(), 1)

    def test_token_respaldo_no_funciona_para_otra_empresa(self):
        empresa = Empresa.objects.create(nombre="Empresa A", slug="empresa-a", rtn="08011999000060")
        otra_empresa = Empresa.objects.create(nombre="Empresa B", slug="empresa-b", rtn="08011999000061")
        administrador = Usuario.objects.create_user(
            username="admin-b",
            password="pass12345",
            empresa=otra_empresa,
            es_administrador_empresa=True,
        )
        token_raw = "DVS-RSP-token-empresa-a"
        TokenRespaldoEmpresa.objects.create(
            empresa=empresa,
            token_hash=hash_token_respaldo(token_raw),
            token_preview="DVS-RSP-toke...sa-a",
            creado_por=self.superadmin,
            fecha_expiracion=timezone.now() + timedelta(hours=24),
        )
        self.client.force_login(administrador)

        response = self.client.post(
            reverse("empresa_respaldo", args=[otra_empresa.slug]),
            {"token_respaldo": token_raw},
        )

        self.assertRedirects(response, reverse("empresa_respaldo", args=[otra_empresa.slug]))
        self.assertEqual(RespaldoEmpresa.objects.count(), 0)

    def test_usuario_empresa_sin_permiso_no_puede_ver_respaldos(self):
        empresa = Empresa.objects.create(nombre="Empresa Operador", slug="empresa-operador", rtn="08011999000062")
        operador = Usuario.objects.create_user(
            username="operador-empresa",
            password="pass12345",
            empresa=empresa,
        )
        self.client.force_login(operador)

        response = self.client.get(reverse("empresa_respaldo", args=[empresa.slug]))

        self.assertRedirects(response, reverse("dashboard", args=[empresa.slug]))

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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_superadmin_puede_crear_usuario_con_rol(self):
        empresa = Empresa.objects.create(nombre="Demo", slug="demo", rtn="08011999000002")
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "first_name": "Nuevo",
                "last_name": "Usuario",
                "email": "nuevo@demo.com",
                "empresa": empresa.id,
                "rol_sistema": self.rol_facturador.id,
                "es_administrador_empresa": "on",
                "is_staff": "on",
                "groups": [self.group.id],
            },
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"))
        usuario = Usuario.objects.get(username="nuevo")
        self.assertEqual(usuario.empresa, empresa)
        self.assertTrue(usuario.es_administrador_empresa)
        self.assertTrue(usuario.groups.filter(name="Ventas").exists())
        self.assertEqual(usuario.rol_sistema, self.rol_facturador)
        self.assertFalse(usuario.is_active)
        self.assertFalse(usuario.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Activa tu acceso", mail.outbox[0].subject)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invitacion_permite_crear_password_y_entrar_con_correo(self):
        import re

        empresa = Empresa.objects.create(nombre="Empresa Invitada", slug="empresa-invitada", rtn="08011999000064")
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "first_name": "Ana",
                "last_name": "Lopez",
                "email": "ana@empresa.com",
                "empresa": empresa.id,
                "rol_sistema": self.rol_facturador.id,
                "groups": [self.group.id],
            },
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"))
        usuario = Usuario.objects.get(email="ana@empresa.com")
        match = re.search(r"/acceso/establecer/([^/]+)/", mail.outbox[0].body)
        self.assertIsNotNone(match)

        self.client.logout()
        activation_response = self.client.post(
            reverse("establecer_acceso", args=[match.group(1)]),
            {
                "new_password1": "ClavePersonalSegura2026",
                "new_password2": "ClavePersonalSegura2026",
            },
        )
        self.assertRedirects(
            activation_response,
            reverse("empresa_login", args=[empresa.slug]),
        )
        usuario.refresh_from_db()
        self.assertTrue(usuario.is_active)
        self.assertTrue(usuario.check_password("ClavePersonalSegura2026"))
        token = TokenAccesoUsuario.objects.get(usuario=usuario)
        self.assertIsNotNone(token.fecha_uso)
        reused_response = self.client.get(
            reverse("establecer_acceso", args=[match.group(1)])
        )
        self.assertEqual(reused_response.status_code, 400)
        self.assertContains(reused_response, "ya fue utilizado", status_code=400)

        login_response = self.client.post(
            reverse("empresa_login", args=[empresa.slug]),
            {"username": "ana@empresa.com", "password": "ClavePersonalSegura2026"},
        )
        self.assertRedirects(
            login_response,
            reverse("dashboard", args=[empresa.slug]),
            fetch_redirect_response=False,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_recuperacion_de_password_por_correo(self):
        import re

        empresa = Empresa.objects.create(nombre="Empresa Recuperacion", slug="empresa-recuperacion", rtn="08011999000065")
        usuario = Usuario.objects.create_user(
            username="recuperacion",
            email="recuperacion@empresa.com",
            password="ClaveAnterior2026",
            empresa=empresa,
        )

        response = self.client.post(
            reverse("solicitar_recuperacion", args=[empresa.slug]),
            {"email": "recuperacion@empresa.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recibiras un enlace")
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"/acceso/establecer/([^/]+)/", mail.outbox[0].body)
        self.assertIsNotNone(match)

        reset_response = self.client.post(
            reverse("establecer_acceso", args=[match.group(1)]),
            {
                "new_password1": "ClaveNuevaSegura2026",
                "new_password2": "ClaveNuevaSegura2026",
            },
        )
        self.assertRedirects(reset_response, reverse("empresa_login", args=[empresa.slug]))
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password("ClaveNuevaSegura2026"))

    def test_creacion_usuario_rechaza_correo_duplicado(self):
        empresa = Empresa.objects.create(nombre="Empresa Correo", slug="empresa-correo", rtn="08011999000066")
        Usuario.objects.create_user(
            username="existente",
            email="duplicado@empresa.com",
            password="pass12345",
            empresa=empresa,
        )
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "first_name": "Otro",
                "last_name": "Usuario",
                "email": "DUPLICADO@empresa.com",
                "empresa": empresa.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe un usuario registrado con este correo.")
        self.assertEqual(Usuario.objects.filter(email__iexact="duplicado@empresa.com").count(), 1)

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
