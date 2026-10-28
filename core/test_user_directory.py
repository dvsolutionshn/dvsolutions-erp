from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, RolSistema, TokenAccesoUsuario, Usuario, UsuarioEmpresaPermiso
from .user_directory import construir_filas_usuarios


class _UserDirectoryMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.rows = {}
        self.row = None
        self.form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-user-identifier" in attrs:
            self.row = {"links": [], "forms": []}
            self.rows[attrs["data-user-identifier"]] = self.row
        elif self.row is not None:
            if tag == "a":
                self.row["links"].append(attrs.get("href"))
            elif tag == "form":
                self.form = {"attributes": attrs, "inputs": []}
                self.row["forms"].append(self.form)
            elif tag == "input" and self.form is not None:
                self.form["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        elif tag == "tr":
            self.row = None


class UserDirectoryPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.erp = Empresa.objects.create(nombre="ERP directorio", slug="directorio-erp", rtn="DIR-ERP")
        cls.clinica = Empresa.objects.create(nombre="Clínica directorio", slug="hospital_mia", rtn="DIR-CLINICA")
        cls.ajena = Empresa.objects.create(nombre="Empresa sin membresía", slug="directorio-ajena", rtn="DIR-AJENA")
        cls.global_role = RolSistema.objects.create(
            nombre="Consulta global directorio", codigo="directorio-global",
            puede_contabilidad=True, puede_crear_asientos=True,
        )
        cls.company_role = RolSistema.objects.create(
            nombre="Consulta clínica local", codigo="directorio-clinica", es_rol_clinico=True,
            puede_ver_pacientes=True,
        )
        cls.usuario = Usuario.objects.create_user(
            username="directorio-persona", email="persona@example.test", first_name="Ana", last_name="López",
            empresa=cls.erp, rol_sistema=cls.global_role, password="DirectorioTest2026!",
        )
        cls.usuario.empresas_acceso.add(cls.erp, cls.clinica)
        cls.admin = Usuario.objects.create_superuser(username="directorio-admin", password="DirectorioAdmin2026!")

    def fila(self, usuario=None):
        return construir_filas_usuarios([usuario or self.usuario])[0]

    def test_union_de_empresas_deduplica_principal_y_muestra_rol_local(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.clinica, rol_sistema=self.company_role)
        fila = self.fila()
        self.assertEqual(fila["nombre"], "Ana López")
        self.assertEqual(fila["iniciales"], "AL")
        self.assertEqual(len(fila["empresas"]), 2)
        self.assertTrue(fila["empresas"][0]["principal"])
        self.assertEqual(fila["empresas"][0]["empresa"], self.erp)
        local = next(item for item in fila["empresas"] if item["empresa"].pk == self.clinica.pk)
        self.assertEqual(local["modo"], "rol_empresa")
        self.assertEqual(local["rol"], self.company_role)
        self.assertTrue(fila["tiene_roles_empresa"])
        self.assertEqual(set(fila["empresa_ids"].split()), {str(self.erp.pk), str(self.clinica.pk)})

    def test_personalizado_vacio_prevalece_y_no_recupera_rol_global(self):
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario, empresa=self.erp, rol_sistema=self.global_role, permisos_personalizados={},
        )
        fila = self.fila()
        principal = fila["empresas"][0]
        self.assertEqual(principal["modo"], "personalizado")
        self.assertIsNone(principal["rol"])
        self.assertEqual(principal["seleccionados"], 0)
        self.assertTrue(fila["tiene_personalizados"])

    def test_personalizados_cuentan_solo_true_del_catalogo_de_la_empresa(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.erp, permisos_personalizados={
            "puede_contabilidad": True,
            "puede_crear_asientos": "on",
            "puede_inventario": False,
            "puede_ver_historia_clinica": True,
            "is_superuser": True,
        })
        self.assertEqual(self.fila()["empresas"][0]["seleccionados"], 1)

    def test_asignacion_activa_sin_rol_no_hereda_el_rol_global(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.erp, rol_sistema=None)
        principal = self.fila()["empresas"][0]
        self.assertEqual(principal["modo"], "sin_rol")
        self.assertIsNone(principal["rol"])
        self.assertFalse(principal["rol_activo"])
        self.assertEqual(principal["seleccionados"], 0)

    def test_asignacion_inactiva_usa_rol_global_y_rol_inactivo_no_se_presenta_activo(self):
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario, empresa=self.erp, rol_sistema=None, permisos_personalizados={}, activo=False,
        )
        self.global_role.activo = False
        self.global_role.save(update_fields=["activo"])
        self.usuario.rol_sistema = self.global_role
        principal = self.fila()["empresas"][0]
        self.assertEqual(principal["modo"], "rol_global")
        self.assertEqual(principal["rol"], self.global_role)
        self.assertFalse(principal["rol_activo"])
        self.assertEqual(principal["seleccionados"], 2)

    def test_asignaciones_ajenas_no_se_presentan_como_empresa_permitida(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.ajena, rol_sistema=self.global_role)
        fila = self.fila()
        self.assertNotIn(str(self.ajena.pk), fila["empresa_ids"].split())
        self.assertNotIn(self.ajena.pk, [item["empresa"].pk for item in fila["empresas"]])

    def test_superusuario_declara_alcance_global_sin_crear_asignaciones(self):
        antes = UsuarioEmpresaPermiso.objects.count()
        fila = self.fila(self.admin)
        self.assertTrue(fila["todas_empresas"])
        self.assertEqual(fila["empresas"], [])
        self.assertEqual(UsuarioEmpresaPermiso.objects.count(), antes)

    def test_prioridad_de_estados_conserva_labels_originales(self):
        ahora = timezone.now()
        self.usuario.is_active = False
        self.usuario.invitaciones_recientes = []
        self.assertEqual(self.fila()["estado"]["codigo"], "inactivo")
        casos = (
            ({"estado_envio": "error", "revocado": True, "fecha_uso": ahora, "fecha_expiracion": ahora - timedelta(days=1)}, "error", "Error de envio"),
            ({"revocado": True, "fecha_uso": ahora}, "revocado", "Revocado"),
            ({"fecha_uso": ahora, "fecha_expiracion": ahora - timedelta(days=1)}, "utilizado", "Utilizado"),
            ({"fecha_expiracion": ahora - timedelta(days=1)}, "vencido", "Vencido"),
            ({"estado_envio": "pendiente"}, "sin_enviar", "Sin enviar"),
            ({"estado_envio": "desconocido"}, "sin_confirmar", "Envio sin confirmar"),
            ({"estado_envio": "enviado"}, "pendiente", "Pendiente"),
        )
        for cambios, codigo, label in casos:
            with self.subTest(codigo=codigo):
                valores = {"estado_envio": "enviado", "fecha_expiracion": ahora + timedelta(days=1), **cambios}
                self.usuario.invitaciones_recientes = [TokenAccesoUsuario(usuario=self.usuario, tipo="invitacion", **valores)]
                estado = self.fila()["estado"]
                self.assertEqual(estado["codigo"], codigo)
                self.assertEqual(estado["label"], label)
        self.usuario.is_active = True
        self.assertEqual(self.fila()["estado"]["codigo"], "activo")
        self.usuario.retirado_control = True
        self.assertEqual(self.fila()["estado"]["codigo"], "retirado")

    def test_condiciones_de_reenvio_no_amplian_la_accion_existente(self):
        password_utilizable = self.usuario.password
        for email, activo, utilizable, retirado, esperado in (
            ("persona@example.test", False, False, False, True),
            ("", False, False, False, False),
            ("persona@example.test", True, False, False, False),
            ("persona@example.test", False, True, False, False),
            ("persona@example.test", False, False, True, False),
        ):
            with self.subTest(email=email, activo=activo, utilizable=utilizable, retirado=retirado):
                self.usuario.email = email
                self.usuario.is_active = activo
                self.usuario.password = password_utilizable if utilizable else "!inutilizable"
                self.usuario.retirado_control = retirado
                self.assertEqual(self.fila()["puede_reenviar"], esperado)

    def test_get_conserva_scope_estado_y_no_escribe_datos(self):
        self.client.force_login(self.admin)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.erp, permisos_personalizados={})
        antes = {
            "usuarios": list(Usuario.objects.order_by("pk").values()),
            "asignaciones": list(UsuarioEmpresaPermiso.objects.order_by("pk").values()),
            "roles": list(RolSistema.objects.order_by("pk").values()),
        }
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse("superadmin_usuarios"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["estado_seleccionado"], "operativos")
        self.assertIn(self.usuario.pk, [usuario.pk for usuario in response.context["usuarios"]])
        fila = next(item for item in response.context["filas_usuarios"] if item["usuario"].pk == self.usuario.pk)
        self.assertTrue(fila["tiene_personalizados"])
        escrituras = [consulta["sql"] for consulta in consultas if consulta["sql"].lstrip().split(maxsplit=1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}]
        self.assertEqual(escrituras, [])
        self.assertEqual(list(Usuario.objects.order_by("pk").values()), antes["usuarios"])
        self.assertEqual(list(UsuarioEmpresaPermiso.objects.order_by("pk").values()), antes["asignaciones"])
        self.assertEqual(list(RolSistema.objects.order_by("pk").values()), antes["roles"])

    def test_ultima_invitacion_es_estable_si_las_fechas_coinciden(self):
        self.client.force_login(self.admin)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_active=False)
        ahora = timezone.now()
        tokens = [
            TokenAccesoUsuario.objects.create(
                usuario=self.usuario, tipo="invitacion", token_hash=str(indice) * 64,
                token_preview=f"directorio-{indice}", fecha_expiracion=ahora + timedelta(days=1), estado_envio=estado,
            )
            for indice, estado in ((1, "enviado"), (2, "error"))
        ]
        TokenAccesoUsuario.objects.filter(pk__in=[token.pk for token in tokens]).update(fecha_creacion=ahora)
        response = self.client.get(reverse("superadmin_usuarios"))
        fila = next(item for item in response.context["filas_usuarios"] if item["usuario"].pk == self.usuario.pk)
        self.assertEqual(fila["estado"]["codigo"], "error")
        self.assertEqual(fila["estado"]["label"], "Error de envio")
        self.assertIn("No se envio esta invitacion", fila["estado"]["nota"])

    def test_acciones_conservan_self_retiro_restauracion_reenvio_y_csrf(self):
        retirado = Usuario.objects.create_user(
            username="directorio-retirado", email="retirado@example.test", empresa=self.erp,
            is_active=False, retirado_control=True,
        )
        pendiente = Usuario.objects.create_user(
            username="directorio-pendiente", email="pendiente@example.test", empresa=self.erp,
            is_active=False,
        )
        bloqueado = Usuario.objects.create_user(
            username="directorio-bloqueado", email="bloqueado@example.test", empresa=self.erp,
            is_active=False, password="DirectorioBloqueado2026!",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("superadmin_usuarios") + "?estado=todos")
        self.assertEqual(response.status_code, 200)
        rows = _UserDirectoryMarkup(response.content.decode()).rows

        self.assertIn(reverse("superadmin_usuario_edit", args=[self.admin.pk]), rows[self.admin.username]["links"])
        self.assertNotIn(reverse("superadmin_usuario_delete", args=[self.admin.pk]), rows[self.admin.username]["links"])
        retired_row = rows[retirado.username]
        self.assertNotIn(reverse("superadmin_usuario_edit", args=[retirado.pk]), retired_row["links"])
        self.assertNotIn(reverse("superadmin_usuario_delete", args=[retirado.pk]), retired_row["links"])
        self.assertEqual(
            [form["attributes"].get("action") for form in retired_row["forms"]],
            [reverse("superadmin_usuario_restore", args=[retirado.pk])],
        )
        self.assertIn(
            reverse("superadmin_usuario_reenviar_invitacion", args=[pendiente.pk]),
            [form["attributes"].get("action") for form in rows[pendiente.username]["forms"]],
        )
        self.assertNotIn(
            reverse("superadmin_usuario_reenviar_invitacion", args=[bloqueado.pk]),
            [form["attributes"].get("action") for form in rows[bloqueado.username]["forms"]],
        )
        for row in rows.values():
            for form in row["forms"]:
                self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
                csrf = [field for field in form["inputs"] if field.get("name") == "csrfmiddlewaretoken"]
                self.assertEqual(len(csrf), 1)
                self.assertTrue(csrf[0].get("value"))
                self.assertEqual(csrf[0].get("type"), "hidden")
