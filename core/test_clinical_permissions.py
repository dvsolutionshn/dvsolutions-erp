from django.test import TestCase

from core.clinical_permissions import permiso_agenda_granular, permiso_clinica_granular
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso


class ClinicalPermissionRoutingTests(TestCase):
    def test_reception_can_upload_but_cannot_view_previous_attachments(self):
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/documentos/laboratorio/subir/", "POST"),
            "puede_subir_anexos_clinicos",
        )
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/documentos/laboratorio/", "GET"),
            "puede_ver_anexos_clinicos",
        )

    def test_calendar_actions_are_separate(self):
        self.assertEqual(permiso_agenda_granular("", "GET"), "puede_ver_calendario")
        self.assertEqual(permiso_agenda_granular("", "POST"), "puede_crear_citas")
        self.assertEqual(permiso_agenda_granular("22/eliminar/", "POST"), "puede_eliminar_citas")

    def test_sensitive_history_and_general_data_are_separate(self):
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/", "GET"),
            "puede_ver_datos_generales_paciente",
        )
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/historias/consolidado/", "GET"),
            "puede_ver_historia_clinica",
        )

    def test_sending_manual_has_its_own_permission(self):
        self.assertEqual(
            permiso_clinica_granular("recetas/manuales/7/enviar-correo/", "POST"),
            "puede_enviar_manuales_pdf",
        )

    def test_operational_manual_module_separates_view_send_and_admin(self):
        self.assertEqual(
            permiso_clinica_granular("manuales-pdf/", "GET"),
            "puede_ver_manuales_pdf",
        )
        self.assertEqual(
            permiso_clinica_granular("manuales-pdf/enviar/", "POST"),
            "puede_enviar_manuales_pdf",
        )
        self.assertEqual(
            permiso_clinica_granular("recetas/manuales/", "GET"),
            "puede_administrar_manuales_pdf",
        )
        self.assertEqual(
            permiso_clinica_granular("recetas/manuales/7/archivo/", "GET"),
            "puede_ver_manuales_pdf",
        )

    def test_nursing_and_therapy_writes_are_separate_from_medical_history(self):
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/historias/nueva/enfermeria/", "POST"),
            "puede_escribir_enfermeria",
        )
        self.assertEqual(
            permiso_clinica_granular("pacientes/10/historias/nueva/terapias/", "POST"),
            "puede_escribir_terapias",
        )


class CompanyRoleAssignmentTests(TestCase):
    def setUp(self):
        self.empresa_a = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="A-1")
        self.empresa_b = Empresa.objects.create(nombre="Medical Spa", slug="medical_spa", rtn="B-1")
        self.usuario = Usuario.objects.create_user(username="persona", password="test")
        self.rol_a = RolSistema.objects.create(
            nombre="Recepción",
            codigo="test-recepcion",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_ver_calendario=True,
        )
        self.rol_b = RolSistema.objects.create(
            nombre="Bodega",
            codigo="test-bodega",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_inventario=True,
        )

    def test_same_user_has_independent_role_per_company(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa_a, rol_sistema=self.rol_a)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa_b, rol_sistema=self.rol_b)
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa_a))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_inventario", self.empresa_a))
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_inventario", self.empresa_b))

    def test_explicit_no_role_does_not_fall_back_to_global_role(self):
        self.usuario.rol_sistema = self.rol_a
        self.usuario.save(update_fields=["rol_sistema"])
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa_a, rol_sistema=None)
        self.assertIsNone(self.usuario.rol_para_empresa(self.empresa_a))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_calendario", self.empresa_a))


class ClinicalPermissionMiddlewareTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital MIA rutas",
            slug="hospital_mia",
            rtn="RUTAS-1",
            tipo_solucion="clinica",
        )
        for codigo in ("clinica_medica", "facturacion", "agenda_citas"):
            modulo = Modulo.objects.create(nombre=codigo, codigo=codigo)
            EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo)
        self.usuario = Usuario.objects.create_user(
            username="recepcion-rutas",
            password="test",
            empresa=self.empresa,
        )
        self.rol = RolSistema.objects.create(
            nombre="Recepción rutas",
            codigo="recepcion-rutas",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_ver_calendario=True,
            puede_crear_citas=True,
            puede_ver_pacientes=True,
            puede_ver_datos_generales_paciente=True,
            puede_crear_recordatorios_paciente=True,
            puede_subir_anexos_clinicos=True,
            puede_crear_facturas=True,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(self.usuario)

    def test_direct_history_url_is_blocked_before_reaching_view(self):
        response = self.client.get(
            "/hospital_mia/dashboard/clinica/pacientes/999/historias/consolidado/"
        )
        self.assertRedirects(response, "/hospital_mia/dashboard/", fetch_redirect_response=False)

    def test_upload_route_is_allowed_while_attachment_history_is_blocked(self):
        upload = self.client.get(
            "/hospital_mia/dashboard/clinica/pacientes/999/documentos/documento/subir/"
        )
        history = self.client.get(
            "/hospital_mia/dashboard/clinica/pacientes/999/documentos/documento/"
        )
        self.assertEqual(upload.status_code, 404)
        self.assertRedirects(history, "/hospital_mia/dashboard/", fetch_redirect_response=False)

    def test_invoice_history_is_denied_without_view_permission(self):
        response = self.client.get("/hospital_mia/dashboard/facturacion/facturas/")
        self.assertRedirects(response, "/hospital_mia/dashboard/", fetch_redirect_response=False)

    def test_nurse_can_open_plans_but_not_medical_history_or_billing(self):
        nurse = RolSistema.objects.create(
            nombre="Enfermera rutas",
            codigo="enfermera-rutas",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_ver_pacientes=True,
            puede_ver_datos_generales_paciente=True,
            puede_ver_planes_tratamiento=True,
            puede_escribir_enfermeria=True,
            puede_escribir_terapias=True,
        )
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario, empresa=self.empresa).update(rol_sistema=nurse)
        plan = self.client.get("/hospital_mia/dashboard/clinica/pacientes/999/planes-tratamiento/")
        history = self.client.get("/hospital_mia/dashboard/clinica/pacientes/999/historias/consolidado/")
        invoice = self.client.get("/hospital_mia/dashboard/facturacion/crear/")
        self.assertEqual(plan.status_code, 404)
        self.assertRedirects(history, "/hospital_mia/dashboard/", fetch_redirect_response=False)
        self.assertRedirects(invoice, "/hospital_mia/dashboard/", fetch_redirect_response=False)

    def test_warehouse_can_create_products_but_cannot_enter_patient_records(self):
        warehouse = RolSistema.objects.create(
            nombre="Bodega rutas",
            codigo="bodega-rutas",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_inventario=True,
            puede_productos=True,
            puede_crear_productos=True,
            puede_transferir_inventario=True,
            puede_crear_facturas=True,
            puede_proveedores=True,
        )
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario, empresa=self.empresa).update(rol_sistema=warehouse)
        product = self.client.get("/hospital_mia/dashboard/facturacion/productos/crear/")
        patients = self.client.get("/hospital_mia/dashboard/clinica/pacientes/")
        self.assertEqual(product.status_code, 200)
        self.assertRedirects(patients, "/hospital_mia/dashboard/", fetch_redirect_response=False)


class ClinicalRoleManagementViewTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Medical Spa roles",
            slug="medical_spa",
            rtn="ROLES-1",
            tipo_solucion="clinica",
        )
        self.admin = Usuario.objects.create_user(
            username="admin-roles",
            password="test",
            empresa=self.empresa,
            puede_administrar_usuarios_clinicos=True,
        )
        self.client.force_login(self.admin)

    def test_create_role_keeps_only_selected_permissions(self):
        response = self.client.post(
            f"/{self.empresa.slug}/dashboard/roles-clinicos/nuevo/",
            {
                "nombre": "Rol exacto",
                "activo": "1",
                "puede_ver_calendario": "1",
                "puede_subir_anexos_clinicos": "1",
            },
        )
        self.assertRedirects(response, f"/{self.empresa.slug}/dashboard/roles-clinicos/")
        rol = RolSistema.objects.get(nombre="Rol exacto")
        self.assertTrue(rol.usa_permisos_clinicos_granulares)
        self.assertTrue(rol.puede_ver_calendario)
        self.assertTrue(rol.puede_subir_anexos_clinicos)
        self.assertFalse(rol.puede_ver_anexos_clinicos)
        self.assertFalse(rol.puede_ver_historia_clinica)
