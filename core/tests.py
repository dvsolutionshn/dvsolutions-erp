from datetime import timedelta
import io
import json
import tempfile
import zipfile

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, PlanComercial, RegistroAuditoria, RolSistema, SolicitudComercial, Usuario
from core.models import PagoLicenciaEmpresa, RespaldoEmpresa, TokenAccesoUsuario, TokenRespaldoEmpresa
from core.backup_tokens import hash_token_respaldo
from core.forms import EmpresaControlForm, RolSistemaForm
from core.access import permiso_facturacion_desde_ruta
from core.audit_context import audit_scope
from core.middleware import AuditoriaRequestMiddleware
from facturacion.models import Cliente


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

    def test_control_muestra_permisos_destacados_de_clinica_y_citas(self):
        self.client.login(username="master", password="pass12345")

        response = self.client.get(reverse("superadmin_usuario_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_modo_creacion_0"', count=1)
        self.assertContains(response, 'id="id_modo_creacion_1"', count=1)
        self.assertContains(response, "Define una contraseña inicial")
        self.assertContains(response, "Envía un enlace seguro")

        response = self.client.get(reverse("superadmin_rol_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendario y gestión de citas")
        self.assertContains(response, "id_puede_citas")
        self.assertContains(response, "Habilitar Citas")
        self.assertContains(response, "id_puede_clinica")
        self.assertContains(response, "Habilitar Clínica")
        self.assertContains(response, "Expedientes clínicos")

    def test_crear_empresa_tecnicentro_activa_perfil_y_modulos_esenciales(self):
        self.client.login(username="master", password="pass12345")
        response = self.client.post(
            reverse("superadmin_empresa_create"),
            {
                "nombre": "Duron Tecnicentro",
                "tipo_solucion": "tecnicentro",
                "slug": "duron",
                "rtn": "08011999000991",
                "pais": "Honduras",
                "condiciones_pago": "Pago inmediato",
                "activa": "on",
            },
        )
        self.assertRedirects(response, reverse("superadmin_empresas"))
        empresa = Empresa.objects.get(slug="duron")
        self.assertEqual(empresa.tipo_solucion, "tecnicentro")
        self.assertTrue(empresa.tiene_modulo_activo("tecnicentro"))
        self.assertTrue(empresa.tiene_modulo_activo("facturacion"))
        self.assertTrue(empresa.tiene_modulo_activo("punto_venta"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_superadmin_puede_crear_usuario_rapido_sin_invitacion(self):
        empresa = Empresa.objects.create(nombre="Empresa Rapida", slug="empresa-rapida", rtn="08011999000066")
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "modo_creacion": "rapido",
                "first_name": "Mario",
                "last_name": "Rapido",
                "email": "mario@empresa.com",
                "empresa": empresa.id,
                "rol_sistema": self.rol_facturador.id,
                "password1": "ClaveInicialSegura2026",
                "password2": "ClaveInicialSegura2026",
                "groups": [self.group.id],
            },
        )

        self.assertRedirects(response, reverse("superadmin_usuarios"))
        usuario = Usuario.objects.get(email="mario@empresa.com")
        self.assertTrue(usuario.is_active)
        self.assertTrue(usuario.check_password("ClaveInicialSegura2026"))
        self.assertEqual(usuario.empresa, empresa)
        self.assertEqual(usuario.rol_sistema, self.rol_facturador)
        self.assertTrue(usuario.groups.filter(id=self.group.id).exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(TokenAccesoUsuario.objects.filter(usuario=usuario).exists())

    def test_superadmin_puede_eliminar_usuario_con_motivo_y_auditoria(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Baja Usuario",
            slug="empresa-baja-usuario",
            rtn="08011999000077",
        )
        usuario = Usuario.objects.create_user(
            username="usuario-eliminable",
            email="eliminar@empresa.com",
            password="pass12345",
            empresa=empresa,
            rol_sistema=self.rol_facturador,
        )
        usuario_id = usuario.id
        self.client.login(username="master", password="pass12345")

        confirmacion = self.client.get(reverse("superadmin_usuario_delete", args=[usuario_id]))
        self.assertEqual(confirmacion.status_code, 200)
        self.assertContains(confirmacion, "Eliminar definitivamente")

        response = self.client.post(
            reverse("superadmin_usuario_delete", args=[usuario_id]),
            {"motivo_eliminacion": "Usuario duplicado creado por error"},
        )

        self.assertRedirects(response, reverse("superadmin_usuarios"))
        self.assertFalse(Usuario.objects.filter(pk=usuario_id).exists())
        auditoria = RegistroAuditoria.objects.get(
            modelo="usuario",
            objeto_id=str(usuario_id),
            accion=RegistroAuditoria.ACCION_ELIMINAR,
        )
        self.assertEqual(auditoria.usuario, self.superadmin)
        self.assertEqual(auditoria.empresa, empresa)
        self.assertEqual(auditoria.motivo, "Usuario duplicado creado por error")

    def test_superadmin_no_puede_eliminar_su_propia_sesion(self):
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_usuario_delete", args=[self.superadmin.id]),
            {"motivo_eliminacion": "Prueba de seguridad"},
        )

        self.assertRedirects(response, reverse("superadmin_usuarios"))
        self.assertTrue(Usuario.objects.filter(pk=self.superadmin.id).exists())

    def test_no_elimina_ultimo_administrador_activo_de_empresa(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Un Solo Admin",
            slug="empresa-un-solo-admin",
            rtn="08011999000078",
        )
        administrador = Usuario.objects.create_user(
            username="admin-unico",
            password="pass12345",
            empresa=empresa,
            es_administrador_empresa=True,
            is_active=True,
        )
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_usuario_delete", args=[administrador.id]),
            {"motivo_eliminacion": "Solicitud de baja"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Usuario.objects.filter(pk=administrador.id).exists())
        self.assertContains(response, "No puedes eliminar el ultimo administrador activo")

    def test_usuario_rapido_rechaza_contrasenas_distintas(self):
        empresa = Empresa.objects.create(nombre="Empresa Clave", slug="empresa-clave", rtn="08011999000067")
        self.client.login(username="master", password="pass12345")

        response = self.client.post(
            reverse("superadmin_usuario_create"),
            {
                "modo_creacion": "rapido",
                "first_name": "Laura",
                "last_name": "Prueba",
                "email": "laura@empresa.com",
                "empresa": empresa.id,
                "rol_sistema": self.rol_facturador.id,
                "password1": "ClaveInicialSegura2026",
                "password2": "OtraClaveSegura2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Las contrasenas no coinciden")
        self.assertFalse(Usuario.objects.filter(email="laura@empresa.com").exists())

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
        self.assertContains(response, "Ya existe un usuario con este correo dentro de la empresa seleccionada.")
        self.assertEqual(Usuario.objects.filter(email__iexact="duplicado@empresa.com").count(), 1)

    def test_mismo_correo_puede_tener_accesos_separados_por_empresa(self):
        empresa_hospital = Empresa.objects.create(
            nombre="Hospital Mia",
            slug="hospital-mia-test",
            rtn="08011999000068",
        )
        empresa_spa = Empresa.objects.create(
            nombre="Medical Spa",
            slug="medical-spa-test",
            rtn="08011999000069",
        )
        self.client.login(username="master", password="pass12345")

        for empresa, password in [
            (empresa_hospital, "ClaveHospitalSegura2026"),
            (empresa_spa, "ClaveSpaSegura2026"),
        ]:
            response = self.client.post(
                reverse("superadmin_usuario_create"),
                {
                    "modo_creacion": "rapido",
                    "first_name": "Doctora",
                    "last_name": "Compartida",
                    "email": "doctora@ejemplo.com",
                    "empresa": empresa.id,
                    "rol_sistema": self.rol_facturador.id,
                    "password1": password,
                    "password2": password,
                },
            )
            self.assertRedirects(response, reverse("superadmin_usuarios"))

        self.assertEqual(Usuario.objects.filter(email__iexact="doctora@ejemplo.com").count(), 2)
        usuario_hospital = Usuario.objects.get(
            empresa=empresa_hospital,
            email__iexact="doctora@ejemplo.com",
        )
        usuario_spa = Usuario.objects.get(
            empresa=empresa_spa,
            email__iexact="doctora@ejemplo.com",
        )
        self.assertTrue(usuario_hospital.check_password("ClaveHospitalSegura2026"))
        self.assertFalse(usuario_hospital.check_password("ClaveSpaSegura2026"))
        self.assertTrue(usuario_spa.check_password("ClaveSpaSegura2026"))

        self.client.logout()
        response_hospital = self.client.post(
            reverse("empresa_login", args=[empresa_hospital.slug]),
            {
                "username": "doctora@ejemplo.com",
                "password": "ClaveHospitalSegura2026",
            },
        )
        self.assertRedirects(
            response_hospital,
            reverse("dashboard", args=[empresa_hospital.slug]),
            fetch_redirect_response=False,
        )

        self.client.logout()
        response_cruzada = self.client.post(
            reverse("empresa_login", args=[empresa_spa.slug]),
            {
                "username": "doctora@ejemplo.com",
                "password": "ClaveHospitalSegura2026",
            },
        )
        self.assertEqual(response_cruzada.status_code, 200)
        self.assertContains(response_cruzada, "Correo o contrasena incorrectos.")

        response_spa = self.client.post(
            reverse("empresa_login", args=[empresa_spa.slug]),
            {
                "username": "doctora@ejemplo.com",
                "password": "ClaveSpaSegura2026",
            },
        )
        self.assertRedirects(
            response_spa,
            reverse("dashboard", args=[empresa_spa.slug]),
            fetch_redirect_response=False,
        )

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

    def test_empresa_clinica_usa_login_medico_y_entra_directo_a_clinica(self):
        empresa = Empresa.objects.create(
            nombre="Clinica Perfil",
            slug="clinica-perfil",
            rtn="08011999000092",
            tipo_solucion="clinica",
            estado_licencia="activa",
        )
        modulo, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "Clinica Medica", "es_comercial": True}
        )
        EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
        Usuario.objects.create_user(
            username="medico-perfil",
            password="ClaveClinica2026",
            empresa=empresa,
            es_administrador_empresa=True,
        )

        login_url = reverse("empresa_login", args=[empresa.slug])
        response = self.client.get(login_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/login_hospital_mia.html")
        response = self.client.post(login_url, {
            "username": "medico-perfil",
            "password": "ClaveClinica2026",
        })
        self.assertRedirects(response, reverse("clinica_dashboard", args=[empresa.slug]))

    def test_empresas_medicas_historicas_conservan_login_futurista(self):
        for indice, slug in enumerate(["hospital_mia", "medical_spa"], start=1):
            empresa = Empresa.objects.create(
                nombre=slug.replace("_", " ").title(),
                slug=slug,
                rtn=f"08011999009{indice:03d}",
                tipo_solucion="erp",
                estado_licencia="activa",
            )
            response = self.client.get(reverse("empresa_login", args=[empresa.slug]))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "core/login_hospital_mia.html")
            self.assertContains(response, "Sistema Clínico")

    def test_empresa_erp_tambien_usa_login_futurista_empresarial(self):
        empresa = Empresa.objects.create(
            nombre="ERP Futuro", slug="erp-futuro", rtn="08011999000871",
            tipo_solucion="erp", estado_licencia="activa",
        )
        response = self.client.get(reverse("empresa_login", args=[empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/login_hospital_mia.html")
        self.assertContains(response, "Sistema Empresarial")
        self.assertContains(response, "ERP Futuro")

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

    @override_settings(ALLOWED_HOSTS=["hospital-mia.erp.test", "testserver"])
    def test_login_por_subdominio_resuelve_slug_con_guion_bajo(self):
        empresa = Empresa.objects.create(
            nombre="Hospital Mia",
            slug="hospital_mia",
            rtn="08011999000022",
        )
        Usuario.objects.create_user(
            username="usuario_hospital_mia",
            password="pass12345",
            empresa=empresa,
        )

        response = self.client.post(
            "/",
            {"username": "usuario_hospital_mia", "password": "pass12345"},
            HTTP_HOST="hospital-mia.erp.test",
        )

        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        dashboard_response = self.client.get("/dashboard/", HTTP_HOST="hospital-mia.erp.test")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Hospital Mia")


class AuditoriaGlobalTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Empresa Auditada", slug="empresa-auditada", rtn="08011999000999"
        )
        self.usuario = Usuario.objects.create_user(
            username="auditor",
            password="pass12345",
            empresa=self.empresa,
            es_administrador_empresa=True,
        )

    def test_registra_creacion_modificacion_y_eliminacion_con_contexto(self):
        with audit_scope(user=self.usuario, reason="Alta solicitada por ventas"):
            cliente = Cliente.objects.create(empresa=self.empresa, nombre="Cliente Inicial")

        creacion = RegistroAuditoria.objects.get(
            app_label="facturacion", modelo="cliente", objeto_id=str(cliente.id), accion="crear"
        )
        self.assertEqual(creacion.empresa, self.empresa)
        self.assertEqual(creacion.usuario, self.usuario)
        self.assertEqual(creacion.motivo, "Alta solicitada por ventas")
        self.assertEqual(creacion.cambios["nombre"]["nuevo"], "Cliente Inicial")

        with audit_scope(user=self.usuario, reason="Correccion confirmada por el cliente"):
            cliente.nombre = "Cliente Corregido"
            cliente.save(update_fields=["nombre"])

        modificacion = RegistroAuditoria.objects.get(
            app_label="facturacion", modelo="cliente", objeto_id=str(cliente.id), accion="modificar"
        )
        self.assertEqual(
            modificacion.cambios["nombre"],
            {"anterior": "Cliente Inicial", "nuevo": "Cliente Corregido"},
        )

        cliente_id = cliente.id
        with audit_scope(user=self.usuario, reason="Depuracion autorizada"):
            cliente.delete()

        eliminacion = RegistroAuditoria.objects.get(
            app_label="facturacion", modelo="cliente", objeto_id=str(cliente_id), accion="eliminar"
        )
        self.assertEqual(eliminacion.usuario, self.usuario)
        self.assertEqual(eliminacion.motivo, "Depuracion autorizada")
        self.assertEqual(
            eliminacion.cambios["registro_eliminado"]["anterior"]["nombre"],
            "Cliente Corregido",
        )

    def test_middleware_toma_usuario_motivo_ruta_e_ip(self):
        request = RequestFactory().post(
            "/empresa-auditada/dashboard/clientes/nuevo/",
            {"motivo": "Registro solicitado en mostrador"},
            REMOTE_ADDR="192.0.2.10",
        )
        request.user = self.usuario

        def crear_cliente(_request):
            Cliente.objects.create(empresa=self.empresa, nombre="Cliente Web")
            return HttpResponse("ok")

        response = AuditoriaRequestMiddleware(crear_cliente)(request)
        self.assertEqual(response.status_code, 200)
        registro = RegistroAuditoria.objects.get(
            app_label="facturacion", modelo="cliente", objeto_representacion="Cliente Web", accion="crear"
        )
        self.assertEqual(registro.usuario, self.usuario)
        self.assertEqual(registro.motivo, "Registro solicitado en mostrador")
        self.assertEqual(registro.metodo_http, "POST")
        self.assertEqual(registro.direccion_ip, "192.0.2.10")

    def test_administrador_solo_ve_bitacora_de_su_empresa(self):
        otra_empresa = Empresa.objects.create(
            nombre="Otra Empresa", slug="otra-empresa-auditada", rtn="08011999000998"
        )
        with audit_scope(user=self.usuario, reason="Registro propio"):
            Cliente.objects.create(empresa=self.empresa, nombre="Visible")
        with audit_scope(reason="Registro externo"):
            Cliente.objects.create(empresa=otra_empresa, nombre="Oculto")

        self.client.login(username="auditor", password="pass12345")
        response = self.client.get(reverse("auditoria_empresa", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible")
        self.assertNotContains(response, "Oculto")


class RolSistemaPermisosTests(TestCase):
    def test_formulario_expone_todos_los_permisos_del_modelo(self):
        form = RolSistemaForm()
        permisos_modelo = {
            field.name
            for field in RolSistema._meta.fields
            if field.name.startswith("puede_")
        }

        self.assertTrue(permisos_modelo)
        self.assertEqual(permisos_modelo, permisos_modelo.intersection(form.fields))
        self.assertIn("puede_punto_venta", form.fields)
        self.assertIn("puede_configuracion_facturacion", form.fields)
        self.assertIn("puede_cierres_caja", form.fields)
        self.assertIn("puede_crm", form.fields)
        self.assertIn("puede_citas", form.fields)
        self.assertIn("puede_clinica", form.fields)

    def test_ruta_pos_exige_permiso_explicito(self):
        self.assertEqual(
            permiso_facturacion_desde_ruta("pos/"),
            "puede_punto_venta",
        )

    def test_configuracion_y_cierres_exigen_permisos_explicitos(self):
        self.assertEqual(
            permiso_facturacion_desde_ruta("configuracion/"),
            "puede_configuracion_facturacion",
        )
        self.assertEqual(
            permiso_facturacion_desde_ruta("cierres-caja/resumen-diario/"),
            "puede_cierres_caja",
        )

    def test_lista_usuarios_muestra_rol_sistema_asignado(self):
        empresa = Empresa.objects.create(
            nombre="Empresa Roles",
            slug="empresa-roles",
            rtn="08011999000888",
        )
        rol = RolSistema.objects.create(
            nombre="Facturacion y Caja",
            codigo="facturacion-caja",
            puede_facturas=True,
            puede_punto_venta=True,
            puede_cierres_caja=True,
        )
        Usuario.objects.create_user(
            username="krystel-prueba",
            email="krystel@example.com",
            password="pass12345",
            empresa=empresa,
            rol_sistema=rol,
        )
        superadmin = Usuario.objects.create_superuser(
            username="superadmin-roles",
            email="superadmin@example.com",
            password="pass12345",
        )
        self.client.force_login(superadmin)

        response = self.client.get(reverse("superadmin_usuarios"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Facturacion y Caja")
        usuario_listado = next(
            usuario for usuario in response.context["usuarios"]
            if usuario.email == "krystel@example.com"
        )
        self.assertEqual(usuario_listado.rol_sistema, rol)
