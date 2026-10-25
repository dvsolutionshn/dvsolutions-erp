from django.test import SimpleTestCase, TestCase

from core.access import permiso_clinica_desde_ruta
from core.clinical_permissions import PERMISOS_CONSULTA_RESTRICCION_CITAS, TODOS_LOS_PERMISOS_CLINICOS, permiso_clinica_granular
from core.clinical_role_details import DESCRIPCIONES_PERMISOS
from core.forms import RolSistemaForm
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso


class RestriccionCitasPermissionRoutingTests(SimpleTestCase):
    def test_restringir_y_habilitar_tienen_permisos_separados_en_ambos_catalogos(self):
        for resolver in (permiso_clinica_desde_ruta, permiso_clinica_granular):
            with self.subTest(resolver=resolver.__name__):
                self.assertEqual(
                    resolver("pacientes/23/restriccion/", "POST"),
                    "puede_restringir_citas_paciente",
                )
                self.assertEqual(
                    resolver("pacientes/23/restriccion/habilitar/", "POST"),
                    "puede_habilitar_citas_paciente",
                )

    def test_historial_permite_consulta_con_cualquiera_de_los_tres_permisos(self):
        for resolver in (permiso_clinica_desde_ruta, permiso_clinica_granular):
            for ruta in ("pacientes/23/restriccion/", "pacientes/23/restriccion/habilitar/"):
                for metodo in ("GET", "HEAD"):
                    with self.subTest(resolver=resolver.__name__, ruta=ruta, metodo=metodo):
                        self.assertEqual(resolver(ruta, metodo), PERMISOS_CONSULTA_RESTRICCION_CITAS)

    def test_todos_los_permisos_estan_disponibles_para_configurar_roles(self):
        formulario = RolSistemaForm()
        for campo in (
            "puede_restringir_citas_paciente",
            "puede_habilitar_citas_paciente",
            "puede_ver_motivo_restriccion_paciente",
        ):
            with self.subTest(campo=campo):
                self.assertIn(campo, TODOS_LOS_PERMISOS_CLINICOS)
                self.assertIn(campo, formulario.fields)
                self.assertIn(campo, DESCRIPCIONES_PERMISOS)


class RestriccionCitasCompanyPermissionTests(TestCase):
    def test_permiso_legado_pacientes_no_concede_acciones_nuevas(self):
        rol = RolSistema.objects.create(nombre="Pacientes legado", codigo="pacientes-restriccion-legado", puede_pacientes=True)
        usuario = Usuario.objects.create_user(username="restriccion-legado", rol_sistema=rol)

        for campo in (
            "puede_restringir_citas_paciente",
            "puede_habilitar_citas_paciente",
            "puede_ver_motivo_restriccion_paciente",
        ):
            with self.subTest(campo=campo):
                self.assertFalse(usuario.tiene_permiso_erp(campo))

    def test_la_asignacion_de_empresa_separa_restringir_habilitar_y_ver_motivo(self):
        empresa_a = Empresa.objects.create(nombre="Clínica A", slug="restriccion-a", rtn="RESTRICCION-A")
        empresa_b = Empresa.objects.create(nombre="Clínica B", slug="restriccion-b", rtn="RESTRICCION-B")
        rol_restringir = RolSistema.objects.create(
            nombre="Restringir citas", codigo="restriccion-solo-marcar", puede_restringir_citas_paciente=True,
        )
        rol_habilitar = RolSistema.objects.create(
            nombre="Habilitar citas", codigo="restriccion-solo-habilitar",
            puede_habilitar_citas_paciente=True, puede_ver_motivo_restriccion_paciente=True,
        )
        usuario = Usuario.objects.create_user(username="restriccion-empresas", empresa=empresa_a)
        usuario.empresas_acceso.add(empresa_b)
        UsuarioEmpresaPermiso.objects.create(usuario=usuario, empresa=empresa_a, rol_sistema=rol_restringir)
        UsuarioEmpresaPermiso.objects.create(usuario=usuario, empresa=empresa_b, rol_sistema=rol_habilitar)

        self.assertTrue(usuario.tiene_permiso_erp("puede_restringir_citas_paciente", empresa_a))
        self.assertFalse(usuario.tiene_permiso_erp("puede_habilitar_citas_paciente", empresa_a))
        self.assertFalse(usuario.tiene_permiso_erp("puede_ver_motivo_restriccion_paciente", empresa_a))
        self.assertFalse(usuario.tiene_permiso_erp("puede_restringir_citas_paciente", empresa_b))
        self.assertTrue(usuario.tiene_permiso_erp("puede_habilitar_citas_paciente", empresa_b))
        self.assertTrue(usuario.tiene_permiso_erp("puede_ver_motivo_restriccion_paciente", empresa_b))
