from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.clinical_permissions import TODOS_LOS_PERMISOS_CLINICOS
from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso
from core.read_only_permissions import (
    PERMISOS_MODULO,
    SOLO_LECTURA_CAMPOS,
    modulo_facturacion_desde_ruta,
    rol_solo_lectura,
)


class ReadOnlyModulePermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia",
            slug="hospital_mia",
            rtn="08011999123456",
            tipo_solucion="clinica",
        )
        self.otra_empresa = Empresa.objects.create(
            nombre="Medical Spa",
            slug="medical_spa",
            rtn="08011999654321",
            tipo_solucion="clinica",
        )
        self.rol = RolSistema.objects.create(
            nombre="Auditor sólo lectura",
            codigo="auditor-solo-lectura-prueba",
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True,
            solo_lectura_contabilidad=True,
            puede_crear_asientos=True,
            puede_contabilizar_asientos=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="auditor-prueba",
            password="pass12345",
            empresa=self.empresa,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            rol_sistema=self.rol,
            activo=True,
        )
        self.middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))

    def _request(self, empresa, modulo, suffix="", *, method="GET", query=None):
        path = f"/{empresa.slug}/dashboard/{modulo}/{suffix}"
        if query:
            path = f"{path}?{query}"
        request = getattr(self.factory, method.lower())(path, {} if method == "GET" else {"dato": "1"})
        request.user = self.usuario
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _response(self, empresa, modulo, suffix="", *, method="GET", query=None):
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            return self.middleware(self._request(empresa, modulo, suffix, method=method, query=query))

    def test_contabilidad_solo_lectura_concede_consulta_y_revoca_escritura(self):
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_contabilidad", self.empresa))
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_catalogo_cuentas", self.empresa))
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_reportes_contables", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_crear_asientos", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_contabilizar_asientos", self.empresa))

    def test_contabilidad_permite_listados_y_bloquea_acciones_get_y_post(self):
        self.assertEqual(self._response(self.empresa, "contabilidad", "asientos/").status_code, 204)
        self.assertEqual(self._response(self.empresa, "contabilidad", "asientos/crear/").status_code, 302)
        self.assertEqual(
            self._response(self.empresa, "contabilidad", "asientos/7/contabilizar/", method="POST").status_code,
            302,
        )
        self.assertEqual(self._response(self.empresa, "contabilidad", "asientos/", method="POST").status_code, 302)

    def test_query_de_edicion_tambien_se_bloquea(self):
        self.assertEqual(
            self._response(self.empresa, "contabilidad", "asientos/7/", query="editar=1").status_code,
            302,
        )

    def test_permiso_es_independiente_por_empresa(self):
        rol_normal = RolSistema.objects.create(nombre="Normal", codigo="normal-otra-empresa", puede_contabilidad=True)
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario,
            empresa=self.otra_empresa,
            rol_sistema=rol_normal,
            activo=True,
        )
        self.assertIsNone(rol_solo_lectura(self.usuario, self.otra_empresa, "contabilidad"))
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_contabilidad", self.otra_empresa))

    def test_inventario_y_facturacion_se_configuran_por_separado(self):
        self.rol.solo_lectura_contabilidad = False
        self.rol.solo_lectura_inventario = True
        self.rol.save(update_fields=["solo_lectura_contabilidad", "solo_lectura_inventario"])
        self.assertEqual(modulo_facturacion_desde_ruta("inventario/kardex/"), "inventario")
        self.assertEqual(modulo_facturacion_desde_ruta("facturas/"), "facturacion")
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_ver_kardex", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_crear_productos", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_facturas", self.empresa))
        self.assertEqual(self._response(self.empresa, "facturacion", "inventario/kardex/").status_code, 204)
        self.assertEqual(self._response(self.empresa, "facturacion", "productos/crear/").status_code, 302)

    def test_agenda_solo_lectura_permite_calendario_pero_no_cambios(self):
        self.rol.solo_lectura_contabilidad = False
        self.rol.solo_lectura_agenda = True
        self.rol.save(update_fields=["solo_lectura_contabilidad", "solo_lectura_agenda"])
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_crear_citas", self.empresa))
        self.assertEqual(self._response(self.empresa, "citas").status_code, 204)
        self.assertEqual(self._response(self.empresa, "citas", method="POST").status_code, 302)

    def test_clinica_solo_lectura_revoca_escritura_y_bloquea_post(self):
        self.rol.solo_lectura_contabilidad = False
        self.rol.solo_lectura_clinica = True
        self.rol.puede_editar_historia_clinica = True
        self.rol.save(update_fields=[
            "solo_lectura_contabilidad", "solo_lectura_clinica", "puede_editar_historia_clinica",
        ])
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_editar_historia_clinica", self.empresa))
        self.assertEqual(
            self._response(self.empresa, "clinica", "pacientes/8/historias/consolidado/").status_code,
            204,
        )
        self.assertEqual(
            self._response(
                self.empresa, "clinica", "pacientes/8/historias/9/editar/", method="POST",
            ).status_code,
            302,
        )

    def test_administrador_no_queda_limitado_por_solo_lectura(self):
        self.usuario.es_administrador_empresa = True
        self.usuario.save(update_fields=["es_administrador_empresa"])
        self.assertIsNone(rol_solo_lectura(self.usuario, self.empresa, "contabilidad"))
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_crear_asientos", self.empresa))
        self.assertEqual(self._response(self.empresa, "contabilidad", "asientos/crear/").status_code, 204)

    def test_los_ocho_selectores_forman_parte_del_catalogo_de_roles(self):
        esperados = {
            "solo_lectura_facturacion", "solo_lectura_inventario", "solo_lectura_contabilidad",
            "solo_lectura_rrhh", "solo_lectura_crm", "solo_lectura_agenda", "solo_lectura_clinica",
            "solo_lectura_tecnicentro",
        }
        self.assertTrue(esperados.issubset(TODOS_LOS_PERMISOS_CLINICOS))

    def test_cada_modulo_solo_lectura_permite_get_y_rechaza_post(self):
        rutas = {
            "facturacion": ("facturacion", "facturas/"),
            "inventario": ("facturacion", "inventario/"),
            "contabilidad": ("contabilidad", "asientos/"),
            "rrhh": ("rrhh", "empleados/"),
            "crm": ("crm", "campanias/"),
            "agenda": ("citas", ""),
            "clinica": ("clinica", "pacientes/"),
            "tecnicentro": ("tecnicentro", "ordenes/"),
        }
        for modulo, (seccion, suffix) in rutas.items():
            with self.subTest(modulo=modulo):
                for campo in SOLO_LECTURA_CAMPOS.values():
                    setattr(self.rol, campo, campo == SOLO_LECTURA_CAMPOS[modulo])
                self.rol.save(update_fields=list(SOLO_LECTURA_CAMPOS.values()))
                self.assertEqual(self._response(self.empresa, seccion, suffix).status_code, 204)
                self.assertEqual(
                    self._response(self.empresa, seccion, suffix, method="POST").status_code,
                    302,
                )

    def test_todos_los_permisos_del_modelo_estan_clasificados(self):
        permisos_modelo = {
            field.name for field in RolSistema._meta.fields if field.name.startswith("puede_")
        }
        permisos_clasificados = set().union(*PERMISOS_MODULO.values())
        self.assertEqual(permisos_modelo, permisos_clasificados)
