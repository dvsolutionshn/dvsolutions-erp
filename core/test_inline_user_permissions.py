from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.clinical_permissions import rol_acciones_v3, rol_clinico_v3
from core.context_processors import erp_access
from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InlineUserPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(
            username="inline-control", email="inline-control@example.com", password="TestInline2026!",
        )
        cls.erp = Empresa.objects.create(nombre="ERP inline", slug="inline-erp", rtn="INLINE-ERP")
        cls.clinic = Empresa.objects.create(
            nombre="Hospital inline", slug="hospital_mia", rtn="INLINE-CLINIC", tipo_solucion="clinica",
        )
        cls.shared_role = RolSistema.objects.create(
            nombre="Rol compartido inline", codigo="inline-compartido", activo=True,
            usa_permisos_acciones_v3=True, puede_contabilidad=True,
            puede_crear_asientos=True, puede_reversar_asientos=True, puede_inventario=True,
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    def create_payload(self, email="inline-new@example.com", **extra):
        return {
            "first_name": "Persona", "last_name": "Inline", "email": email,
            "empresa": str(self.erp.pk), "empresas_acceso": [str(self.erp.pk)],
            "modo_creacion": "invitacion",
            f"empresa_modo_permisos_{self.erp.pk}": "personalizado",
            f"empresa_permisos_{self.erp.pk}": ["puede_contabilidad"],
            **extra,
        }

    def existing_user(self, **extra):
        user = Usuario.objects.create_user(
            username="inline-existing", email="inline-existing@example.com",
            empresa=self.erp, rol_sistema=self.shared_role, **extra,
        )
        user.empresas_acceso.add(self.erp, self.clinic)
        return user

    def edit_payload(self, user, **extra):
        return {
            "username": user.username, "first_name": user.first_name,
            "last_name": user.last_name, "email": user.email,
            "empresa": str(self.erp.pk),
            "empresas_acceso": [str(self.erp.pk), str(self.clinic.pk)],
            "rol_sistema": str(self.shared_role.pk), "is_active": "on",
            f"empresa_modo_permisos_{self.erp.pk}": "personalizado",
            f"empresa_permisos_{self.erp.pk}": ["puede_contabilidad"],
            **extra,
        }

    def test_creacion_sin_rol_guarda_permisos_antes_de_enviar_invitacion(self):
        roles_before = RolSistema.objects.count()
        observed = []

        def inspect_saved_permissions(request, user, *args, **kwargs):
            persisted = Usuario.objects.get(pk=user.pk)
            assignment = UsuarioEmpresaPermiso.objects.get(usuario=persisted, empresa=self.erp)
            self.assertIsNone(persisted.rol_sistema_id)
            self.assertIsNone(assignment.rol_sistema_id)
            self.assertEqual(assignment.permisos_personalizados, {"puede_contabilidad": True})
            self.assertTrue(persisted.tiene_permiso_erp("puede_contabilidad", self.erp))
            self.assertFalse(persisted.tiene_permiso_erp("puede_crear_asientos", self.erp))
            self.assertFalse(persisted.is_active)
            observed.append(persisted.pk)

        with patch("core.views.enviar_correo_acceso", side_effect=inspect_saved_permissions) as send:
            response = self.client.post(reverse("superadmin_usuario_create"), self.create_payload())
        self.assertRedirects(response, reverse("superadmin_usuarios"), fetch_redirect_response=False)
        send.assert_called_once()
        self.assertEqual(len(observed), 1)
        self.assertEqual(RolSistema.objects.count(), roles_before)

    def test_casillas_disponibles_al_crear_y_editar_usuario(self):
        create = self.client.get(reverse("superadmin_usuario_create"))
        self.assertContains(create, "data-personal-permissions")
        self.assertContains(create, f'name="empresa_permisos_{self.erp.pk}"')
        self.assertContains(create, f'name="empresa_permisos_{self.clinic.pk}"')
        user = self.existing_user()
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={"puede_contabilidad": True},
        )
        edit = self.client.get(reverse("superadmin_usuario_edit", args=[user.pk]))
        self.assertContains(edit, "data-personal-permissions")
        self.assertContains(edit, f'name="empresa_permisos_{self.erp.pk}"')

    def test_permisos_individuales_aislados_por_persona_y_empresa(self):
        user = self.existing_user()
        other = Usuario.objects.create_user(
            username="inline-other", empresa=self.erp, rol_sistema=self.shared_role,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={"puede_contabilidad": True},
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.clinic,
            permisos_personalizados={"puede_ver_pacientes": True, "puede_subir_anexos_clinicos": True},
        )
        self.assertFalse(user.tiene_permiso_erp("puede_reversar_asientos", self.erp))
        self.assertTrue(other.tiene_permiso_erp("puede_reversar_asientos", self.erp))
        self.assertFalse(user.tiene_permiso_erp("puede_ver_pacientes", self.erp))
        self.assertTrue(user.tiene_permiso_erp("puede_ver_pacientes", self.clinic))
        self.assertTrue(user.tiene_permiso_erp("puede_subir_anexos_clinicos", self.clinic))
        self.assertFalse(user.tiene_permiso_erp("puede_ver_anexos_clinicos", self.clinic))
        self.assertIsNotNone(rol_acciones_v3(user, self.erp))
        self.assertIsNotNone(rol_clinico_v3(user, self.clinic))
        self.shared_role.refresh_from_db()
        self.assertTrue(self.shared_role.puede_reversar_asientos)

    def test_edicion_reemplaza_casillas_sin_conservar_permisos_anteriores(self):
        user = self.existing_user()
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, rol_sistema=self.shared_role,
            permisos_personalizados={"puede_contabilidad": True, "puede_reversar_asientos": True},
        )
        clinic_assignment = UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.clinic, permisos_personalizados={"puede_ver_pacientes": True},
        )
        response = self.client.post(
            reverse("superadmin_usuario_edit", args=[user.pk]), self.edit_payload(
                user,
                **{
                    f"empresa_modo_permisos_{self.clinic.pk}": "personalizado",
                    f"empresa_permisos_{self.clinic.pk}": ["puede_ver_pacientes"],
                },
            ),
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"), fetch_redirect_response=False)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=user, empresa=self.erp)
        self.assertEqual(assignment.permisos_personalizados, {"puede_contabilidad": True})
        self.assertFalse(user.tiene_permiso_erp("puede_reversar_asientos", self.erp))
        clinic_assignment.refresh_from_db()
        self.assertEqual(clinic_assignment.permisos_personalizados, {"puede_ver_pacientes": True})

    def test_seleccion_vacia_deniega_todo_aunque_exista_rol_global(self):
        user = self.existing_user()
        response = self.client.post(
            reverse("superadmin_usuario_edit", args=[user.pk]),
            self.edit_payload(user, **{f"empresa_permisos_{self.erp.pk}": []}),
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"), fetch_redirect_response=False)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=user, empresa=self.erp)
        self.assertEqual(assignment.permisos_personalizados, {})
        for permission in ("puede_contabilidad", "puede_crear_asientos", "puede_inventario"):
            self.assertFalse(user.tiene_permiso_erp(permission, self.erp), permission)

    def test_backend_rechaza_permiso_clinico_inyectado_en_erp_sin_crear_usuario(self):
        with patch("core.views.enviar_correo_acceso") as send:
            response = self.client.post(
                reverse("superadmin_usuario_create"),
                self.create_payload(**{f"empresa_permisos_{self.erp.pk}": ["puede_ver_historia_clinica"]}),
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Usuario.objects.filter(email="inline-new@example.com").exists())
        send.assert_not_called()

    def test_backend_rechaza_campos_de_administracion_en_catalogo_individual(self):
        with patch("core.views.enviar_correo_acceso") as send:
            response = self.client.post(
                reverse("superadmin_usuario_create"),
                self.create_payload(**{f"empresa_permisos_{self.erp.pk}": ["is_superuser"]}),
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Usuario.objects.filter(email="inline-new@example.com").exists())
        send.assert_not_called()

    def test_volver_a_rol_limpia_permisos_personalizados(self):
        user = self.existing_user()
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={"puede_clientes": True},
        )
        response = self.client.post(
            reverse("superadmin_usuario_edit", args=[user.pk]),
            self.edit_payload(user, **{
                f"empresa_modo_permisos_{self.erp.pk}": "rol",
                f"empresa_rol_{self.erp.pk}": str(self.shared_role.pk),
            }),
        )
        self.assertRedirects(response, reverse("superadmin_usuarios"), fetch_redirect_response=False)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=user, empresa=self.erp)
        self.assertIsNone(assignment.permisos_personalizados)
        self.assertEqual(assignment.rol_sistema_id, self.shared_role.pk)
        self.assertTrue(user.tiene_permiso_erp("puede_reversar_asientos", self.erp))
        self.assertFalse(user.tiene_permiso_erp("puede_clientes", self.erp))

    def test_permisos_directos_cumplen_rutas_backend_y_solo_lectura(self):
        user = self.existing_user()
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp,
            permisos_personalizados={"solo_lectura_contabilidad": True},
        )
        factory = RequestFactory()
        middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))
        for method, suffix, status in (
            ("get", "", 204),
            ("get", "asientos/crear/", 302),
            ("post", "asientos/4/reversar/", 302),
        ):
            with self.subTest(method=method, suffix=suffix):
                request = getattr(factory, method)(f"/{self.erp.slug}/dashboard/contabilidad/{suffix}")
                request.user = user
                SessionMiddleware(lambda req: None).process_request(request)
                request.session.save()
                request._messages = FallbackStorage(request)
                with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
                    self.assertEqual(middleware(request).status_code, status)

    def mobile_target(self):
        user = self.existing_user()
        assignment = UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.clinic,
            permisos_personalizados={"puede_ver_pacientes": True, "puede_subir_anexos_clinicos": True},
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={"solo_lectura_contabilidad": True},
        )
        actor = Usuario.objects.create_user(
            username="inline-doctora", empresa=self.clinic,
            puede_administrar_usuarios_clinicos=True,
        )
        self.client.force_login(actor)
        return user, assignment

    def test_app_edita_permisos_directos_y_conserva_otra_empresa_y_rol_compartido(self):
        user, assignment = self.mobile_target()
        url = reverse("usuario_clinico_permisos_editar_app", args=[self.clinic.slug, user.pk]) + "?app=1"
        original_roles = list(RolSistema.objects.order_by("pk").values())
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["mobile_user_permissions_editable"])
        checked = {
            item["campo"]
            for group in page.context["mobile_user_permission_groups"]
            for item in group["permisos"] if item["activo"]
        }
        self.assertEqual(checked, {"puede_ver_pacientes", "puede_subir_anexos_clinicos"})
        version = page.context["mobile_user_permissions_version"]
        response = self.client.post(url, {
            "app": "1", "version_permisos": version,
            "puede_ver_planes_tratamiento": "1", "puede_escribir_enfermeria": "1",
            "confirmar_permisos": "1",
        })
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.permisos_personalizados)
        self.assertIsNotNone(assignment.rol_sistema_id)
        self.assertTrue(user.tiene_permiso_erp("puede_ver_planes_tratamiento", self.clinic))
        self.assertTrue(user.tiene_permiso_erp("puede_escribir_enfermeria", self.clinic))
        self.assertFalse(user.tiene_permiso_erp("puede_ver_pacientes", self.clinic))
        self.assertFalse(user.tiene_permiso_erp("puede_subir_anexos_clinicos", self.clinic))
        self.assertEqual(
            UsuarioEmpresaPermiso.objects.get(usuario=user, empresa=self.erp).permisos_personalizados,
            {"solo_lectura_contabilidad": True},
        )
        self.assertEqual(
            list(RolSistema.objects.filter(pk__in=[row["id"] for row in original_roles]).order_by("pk").values()),
            original_roles,
        )
        user.refresh_from_db()
        self.assertEqual(user.rol_sistema_id, self.shared_role.pk)

    def test_app_detecta_json_modificado_despues_de_abrir_formulario(self):
        user, assignment = self.mobile_target()
        url = reverse("usuario_clinico_permisos_editar_app", args=[self.clinic.slug, user.pk]) + "?app=1"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        updated = {"puede_ver_pacientes": True, "puede_ver_recetas": True}
        UsuarioEmpresaPermiso.objects.filter(pk=assignment.pk).update(permisos_personalizados=updated)
        role_count = RolSistema.objects.count()
        response = self.client.post(url, {
            "app": "1", "version_permisos": page.context["mobile_user_permissions_version"],
            "puede_ver_planes_tratamiento": "1", "confirmar_permisos": "1",
        })
        self.assertEqual(response.status_code, 409)
        assignment.refresh_from_db()
        self.assertEqual(assignment.permisos_personalizados, updated)
        self.assertIsNone(assignment.rol_sistema_id)
        self.assertEqual(RolSistema.objects.count(), role_count)

    def test_app_asigna_rol_clinico_y_reemplaza_permisos_directos(self):
        user, assignment = self.mobile_target()
        role = RolSistema.objects.create(
            nombre="Enfermería inline", codigo="inline-clinico", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True, puede_escribir_terapias=True,
        )
        url = reverse("usuario_clinico_permisos", args=[self.clinic.slug, user.pk]) + "?app=1"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context["rol_actual"].pk)
        self.assertTrue(page.context["rol_actual"].puede_subir_anexos_clinicos)
        response = self.client.post(url, {"app": "1", "rol_sistema": str(role.pk)})
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.permisos_personalizados)
        self.assertEqual(assignment.rol_sistema_id, role.pk)
        self.assertTrue(user.tiene_permiso_erp("puede_escribir_terapias", self.clinic))
        self.assertFalse(user.tiene_permiso_erp("puede_subir_anexos_clinicos", self.clinic))
        self.assertFalse(user.tiene_permiso_erp("puede_ver_pacientes", self.clinic))
        self.assertEqual(
            UsuarioEmpresaPermiso.objects.get(usuario=user, empresa=self.erp).permisos_personalizados,
            {"solo_lectura_contabilidad": True},
        )

    def test_agenda_erp_muestra_menu_y_acciones_exactas_sin_rol_global(self):
        from crm.views import _proteger_agenda_mobile, _puede_gestionar_bloqueos_otros

        user = self.existing_user()
        user.rol_sistema = None
        user.save(update_fields=["rol_sistema"])
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={
                "puede_ver_calendario": True, "puede_crear_bloqueos_agenda": True,
            },
        )
        request = RequestFactory().get(reverse("agenda_citas", args=[self.erp.slug]))
        request.user = user
        with patch.object(Empresa, "tiene_modulo_activo", side_effect=lambda modulo: modulo == "agenda_citas"):
            access = erp_access(request)["erp_access"]
        self.assertTrue(access["modulo_citas"])
        self.assertTrue(access["citas"])
        self.assertTrue(access["ver_calendario"])
        self.assertTrue(access["crear_bloqueos_agenda"])
        self.assertFalse(access["crear_citas"])
        self.assertFalse(access["editar_citas"])
        self.assertFalse(access["marcar_citas_pagadas"])
        self.assertFalse(access["marcar_citas_cortesia"])
        self.assertTrue(_puede_gestionar_bloqueos_otros(user, self.erp))
        self.assertIsNone(_proteger_agenda_mobile(request, self.erp))

    def test_agenda_erp_rechaza_post_pagada_cortesia_sin_permiso_web_y_app(self):
        user = self.existing_user()
        user.rol_sistema = None
        user.save(update_fields=["rol_sistema"])
        Empresa.objects.filter(pk=self.erp.pk).update(estado_licencia="activa")
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp, permisos_personalizados={
                "puede_ver_calendario": True, "puede_crear_citas": True,
            },
        )
        self.client.force_login(user)
        count_before = CitaCliente.objects.count()
        for route in ("agenda_citas", "agenda_mobile"):
            for field in ("pagada", "cortesia"):
                with self.subTest(route=route, field=field):
                    with patch.object(Empresa, "tiene_modulo_activo", side_effect=lambda modulo: modulo == "agenda_citas"):
                        response = self.client.post(
                            reverse(route, args=[self.erp.slug]), {field: "on", "titulo": "Intento sin permiso"},
                        )
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(CitaCliente.objects.count(), count_before)

    def test_login_publico_tecnicentro_admite_permisos_directos_sin_rol_global(self):
        user = self.existing_user()
        user.rol_sistema = None
        user.set_password("InlineTaller2026!")
        user.save(update_fields=["rol_sistema", "password"])
        UsuarioEmpresaPermiso.objects.create(
            usuario=user, empresa=self.erp,
            permisos_personalizados={"puede_operacion_taller": True},
        )
        self.client.logout()
        url = reverse("tecnicentro_login", args=[self.erp.slug])
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            response = self.client.post(url, {"username": user.email, "password": "InlineTaller2026!"})
        self.assertRedirects(
            response, reverse("tecnicentro_dashboard", args=[self.erp.slug]), fetch_redirect_response=False,
        )
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            existing_session = self.client.get(url)
        self.assertRedirects(
            existing_session, reverse("tecnicentro_dashboard", args=[self.erp.slug]), fetch_redirect_response=False,
        )
