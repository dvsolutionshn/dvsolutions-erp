from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse, QueryDict
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import patch

from core.clinical_permissions import (
    EMPRESAS_CLINICAS_CON_ROLES,
    GRUPOS_EXCLUSIVOS_CLINICA,
    PERMISOS_ERP,
    TODOS_LOS_PERMISOS_ERP,
)
from core.forms import RolSistemaForm, UsuarioControlUpdateForm
from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso
from core.views import _guardar_roles_por_empresa, _usuario_empresas_config


class GeneralErpRoleCatalogTests(TestCase):
    def test_catalogo_erp_conserva_acciones_generales_y_excluye_clinica(self):
        grupos = {nombre for nombre, _permisos in PERMISOS_ERP}
        permisos = set(TODOS_LOS_PERMISOS_ERP)
        self.assertFalse(grupos & GRUPOS_EXCLUSIVOS_CLINICA)
        self.assertIn("puede_reversar_asientos", permisos)
        self.assertIn("puede_revertir_pagos_proveedores", permisos)
        self.assertIn("puede_asignar_ordenes_taller", permisos)
        self.assertNotIn("puede_ver_historia_clinica", permisos)
        self.assertNotIn("puede_crear_planes_tratamiento", permisos)
        self.assertNotIn("solo_lectura_clinica", permisos)

    def test_formulario_de_mi_control_activa_v3_y_limpia_permisos_medicos(self):
        rol = RolSistema.objects.create(
            nombre="ERP anterior", codigo="erp-anterior",
            puede_clinica=True, puede_ver_historia_clinica=True,
        )
        form = RolSistemaForm({
            "nombre": "Auditor ERP",
            "codigo": "auditor-erp",
            "descripcion": "Acceso no clínico",
            "activo": "on",
            "puede_contabilidad": "on",
            "puede_reversar_asientos": "on",
        }, instance=rol)
        self.assertTrue(form.is_valid(), form.errors)
        guardado = form.save()
        self.assertTrue(guardado.usa_permisos_acciones_v3)
        self.assertFalse(guardado.es_rol_clinico)
        self.assertTrue(guardado.puede_reversar_asientos)
        self.assertFalse(guardado.puede_clinica)
        self.assertFalse(guardado.puede_ver_historia_clinica)


class GeneralErpRoleAssignmentTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.clinica = Empresa.objects.create(
            nombre="Hospital", slug=next(iter(EMPRESAS_CLINICAS_CON_ROLES)), rtn="CAT-CLINICA",
            tipo_solucion="clinica",
        )
        self.erp = Empresa.objects.create(
            nombre="Digital Planning", slug="digital_planning", rtn="CAT-ERP",
        )
        self.rol_clinico = RolSistema.objects.create(
            nombre="Doctora", codigo="catalogo-doctora", es_rol_clinico=True, activo=True,
        )
        self.rol_erp = RolSistema.objects.create(
            nombre="Contabilidad", codigo="catalogo-contabilidad", activo=True,
            usa_permisos_acciones_v3=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="catalogos-empresa", password="test", empresa=self.erp,
        )
        self.usuario.empresas_acceso.add(self.erp, self.clinica)

    def test_selector_muestra_roles_segun_tipo_de_empresa(self):
        request = self.factory.get("/control/usuarios/editar/")
        form = UsuarioControlUpdateForm(instance=self.usuario)
        contexto = _usuario_empresas_config(form, request)
        por_slug = {item["empresa"].slug: item for item in contexto["empresas_config"]}
        self.assertIn(self.rol_erp.pk, [rol.pk for rol in por_slug[self.erp.slug]["roles"]])
        self.assertNotIn(self.rol_clinico.pk, [rol.pk for rol in por_slug[self.erp.slug]["roles"]])
        self.assertIn(self.rol_clinico.pk, [rol.pk for rol in por_slug[self.clinica.slug]["roles"]])
        self.assertNotIn(self.rol_erp.pk, [rol.pk for rol in por_slug[self.clinica.slug]["roles"]])

    def test_backend_rechaza_rol_clinico_falsificado_en_empresa_erp(self):
        data = QueryDict(mutable=True)
        data.setlist("empresas_acceso", [str(self.erp.pk)])
        data[f"empresa_rol_{self.erp.pk}"] = str(self.rol_clinico.pk)
        _guardar_roles_por_empresa(self.usuario, data)
        self.assertFalse(UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario, empresa=self.erp).exists())


class GeneralErpRoleControlIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = Usuario.objects.create_superuser(
            username="control-erp-roles", email="control-erp@example.com", password="test",
        )
        self.erp = RolSistema.objects.create(nombre="Rol ERP visible", codigo="rol-erp-visible")
        self.clinico = RolSistema.objects.create(
            nombre="Rol clínico aislado", codigo="rol-clinico-aislado", es_rol_clinico=True,
        )
        self.client.force_login(self.admin)

    def test_mi_control_lista_y_edita_solo_roles_erp(self):
        listado = self.client.get(reverse("superadmin_roles"))
        self.assertContains(listado, self.erp.nombre)
        self.assertNotContains(listado, self.clinico.nombre)
        self.assertEqual(
            self.client.get(reverse("superadmin_rol_edit", args=[self.clinico.pk])).status_code,
            404,
        )


class GeneralErpMiddlewareV3Tests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.empresa = Empresa.objects.create(
            nombre="Digital Planning", slug="digital_planning", rtn="ERP-V3",
        )
        self.rol = RolSistema.objects.create(
            nombre="ERP V3", codigo="erp-v3-general", usa_permisos_acciones_v3=True,
            puede_contabilidad=True, puede_crear_asientos=True,
            puede_rrhh=True, puede_empleados=True,
            puede_crm=True, puede_campanias=True,
            puede_tecnicentro=True, puede_operacion_taller=True,
            puede_facturas=True, puede_ver_calendario=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="erp-v3-general", password="test", empresa=self.empresa,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario, empresa=self.empresa, rol_sistema=self.rol,
        )
        self.middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))

    def respuesta(self, modulo="contabilidad", suffix="asientos/4/reversar/"):
        request = self.factory.post(
            f"/{self.empresa.slug}/dashboard/{modulo}/{suffix}", {},
        )
        request.user = self.usuario
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            return self.middleware(request)

    def test_empresa_no_clinica_exige_permiso_exacto(self):
        casos = (
            ("contabilidad", "asientos/4/reversar/", "puede_reversar_asientos"),
            ("rrhh", "empleados/crear/", "puede_crear_empleados"),
            ("crm", "campanias/4/enviar-whatsapp-api/", "puede_enviar_campanias"),
            ("tecnicentro", "ordenes/4/asignar/", "puede_asignar_ordenes_taller"),
            ("facturacion", "cotizaciones/crear/", "puede_crear_cotizaciones"),
            ("citas", "7/reagendar-whatsapp/", "puede_reagendar_citas"),
        )
        for modulo, suffix, field in casos:
            with self.subTest(field=field):
                self.assertEqual(self.respuesta(modulo, suffix).status_code, 302)
                setattr(self.rol, field, True)
                self.rol.save(update_fields=[field])
                self.assertEqual(self.respuesta(modulo, suffix).status_code, 204)
