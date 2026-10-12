from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Factura, MovimientoInventario, Producto
from . import views
from .forms import PlantillaRecetaForm
from .models import Paciente, PlantillaReceta, PlantillaRecetaDetalle, ProfesionalSalud, RecetaMedica, RecetaMedicaDetalle


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileRecipeTemplatesIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.plantillas, cls.pacientes = {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Plantillas móvil {slug}", slug=slug, rtn=f"MRT-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=cls.modulo, defaults={"activo": True})
            cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente {slug}", expediente_codigo=f"MRT-{indice}")
            plantilla = PlantillaReceta.objects.create(
                empresa=empresa, nombre=f"Plantilla {slug}", diagnostico="Texto de diagnóstico de prueba",
                indicaciones_generales="Texto general existente", observaciones="Observación existente",
            )
            cls.plantillas[slug] = plantilla
            PlantillaRecetaDetalle.objects.create(
                plantilla=plantilla, medicamento_manual=f"Externo {slug}", cantidad="Cantidad de prueba",
                indicaciones="Indicación de prueba", observaciones="Observación de línea", orden=0,
            )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.plantilla = cls.plantillas[cls.empresa.slug]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Plantillas móvil administrador", codigo="mobile-recipe-template-admin", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_editar_historia_clinica=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-recipe-template-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.plantilla.creada_por = cls.usuario
        cls.plantilla.save(update_fields=["creada_por"])
        cls.inactiva = PlantillaReceta.objects.create(empresa=cls.empresa, nombre="Plantilla inactiva", activa=False)
        cls.producto_spa = Producto.objects.create(empresa=cls.empresas["medical_spa"], nombre="Producto compartido Spa", codigo="MRT-SPA", precio=100)
        cls.producto_hospital = Producto.objects.create(empresa=cls.empresa, nombre="Producto Hospital no catálogo", codigo="MRT-HM", precio=30)
        cls.producto_luque = Producto.objects.create(empresa=cls.empresas["luque_aestetic"], nombre="Producto Luque no catálogo", codigo="MRT-LUQ", precio=20)
        cls.producto_inactivo = Producto.objects.create(empresa=cls.empresas["medical_spa"], nombre="Producto Spa inactivo", codigo="MRT-OFF", precio=10, activo=False)
        cls.producto_eliminado = Producto.objects.create(empresa=cls.empresas["medical_spa"], nombre="Producto Spa eliminado", codigo="MRT-DEL", precio=10, eliminado=True)

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="dv-mobile-recipe-templates-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        for target in ("clinica.views.EmailMultiAlternatives.send", "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            patcher = patch(target)
            transport = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(transport.assert_not_called)

    def url(self, nombre="clinica_plantillas_recetas", empresa=None, plantilla=None, mobile=True):
        args = [(empresa or self.empresa).slug] + ([plantilla.pk] if plantilla is not None else [])
        return reverse(nombre, args=args) + ("?app=1" if mobile else "")

    def datos(self, **overrides):
        data = {
            "app": "1", "nombre": "Plantilla nueva móvil", "diagnostico": "Diagnóstico sintético",
            "indicaciones_generales": "Texto general conservado", "observaciones": "Observaciones conservadas", "activa": "on",
            "medicamento_producto_id": ["", ""], "medicamento_manual": ["Externo uno", "Externo dos"],
            "medicamento_cantidad": ["Cantidad uno", "Cantidad dos"], "medicamento_indicaciones": ["Texto uno", "Texto dos"],
            "medicamento_observaciones": ["Nota uno", "Nota dos"],
        }
        data.update(overrides)
        return data

    def asignar_rol(self, empresa=None, granular=True, **permisos):
        rol = RolSistema.objects.create(
            nombre="Plantillas móvil restringido", codigo=f"mobile-recipe-template-role-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def conteos(self):
        return tuple(model.objects.count() for model in (
            PlantillaReceta, PlantillaRecetaDetalle, RecetaMedica, RecetaMedicaDetalle,
            Paciente, ProfesionalSalud, Producto, MovimientoInventario, Factura, RegistroAuditoria,
        ))

    def request(self, view, route, method="get", empresa=None, plantilla=None, data=None):
        request = getattr(RequestFactory(), method)(self.url(route, empresa, plantilla), data or {"app": "1"})
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if plantilla is not None:
            kwargs["plantilla_id"] = plantilla.pk
        return view(request, **kwargs)

    def test_four_clinics_mobile_templates_do_not_replace_web(self):
        before = self.conteos()
        for empresa in self.empresas.values():
            for route, plantilla, mobile, web in (
                ("clinica_plantillas_recetas", None, "mobile_recipe_template_list.html", "plantillas_recetas.html"),
                ("clinica_crear_plantilla_receta", None, "mobile_recipe_template_form.html", "plantilla_receta_form.html"),
                ("clinica_editar_plantilla_receta", self.plantillas[empresa.slug], "mobile_recipe_template_form.html", "plantilla_receta_form.html"),
            ):
                with self.subTest(empresa=empresa.slug, route=route):
                    response = self.client.get(self.url(route, empresa, plantilla))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "clinica/"+mobile)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotContains(response, "<table")
                    original = self.client.get(self.url(route, empresa, plantilla, mobile=False))
                    self.assertEqual(original.status_code, 200)
                    self.assertTemplateUsed(original, "clinica/"+web)
                    self.assertTemplateNotUsed(original, "clinica/"+mobile)
        self.assertEqual(self.conteos(), before)

    def test_other_clinic_app_flag_keeps_original_web(self):
        empresa = Empresa.objects.create(nombre="Otra clínica plantillas", slug="otra_clinica_plantillas", rtn="MRT-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        self.asignar_rol(empresa=empresa, granular=False, puede_expediente_clinico=True)
        for route, web in (("clinica_plantillas_recetas", "plantillas_recetas.html"), ("clinica_crear_plantilla_receta", "plantilla_receta_form.html")):
            response = self.client.get(self.url(route, empresa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/"+web)
            self.assertFalse(response.context.get("modo_app", False))
        empresa.tipo_solucion = "erp"
        empresa.save(update_fields=["tipo_solucion"])
        self.assertEqual(self.client.get(self.url(empresa=empresa)).status_code, 404)

    def test_original_five_field_form_shared_catalog_and_editor_are_reused(self):
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                app = self.client.get(self.url("clinica_crear_plantilla_receta", empresa))
                web = self.client.get(self.url("clinica_crear_plantilla_receta", empresa, mobile=False))
                self.assertIsInstance(app.context["form"], PlantillaRecetaForm)
                self.assertEqual(list(app.context["form"].fields), ["nombre", "diagnostico", "indicaciones_generales", "observaciones", "activa"])
                self.assertEqual(app.context["catalogo_medicamentos"], web.context["catalogo_medicamentos"])
                self.assertEqual([p["id"] for p in app.context["catalogo_medicamentos"]], [] if empresa == self.empresa else [self.producto_spa.pk])
                self.assertTemplateUsed(app, "clinica/includes/receta_medicamentos_editor.html")
                self.assertContains(app, 'name="app" value="1"')
                self.assertContains(app, 'name="nombre"', count=1)
                self.assertContains(app, 'name="activa"', count=1)

    def test_list_own_inactive_actor_and_dates_without_read_side_effects(self):
        before = self.conteos()
        response = self.client.get(self.url())
        self.assertEqual({p.pk for p in response.context["plantillas"]}, {self.plantilla.pk, self.inactiva.pk})
        self.assertEqual({item["plantilla"].pk for item in response.context["mobile_recipe_template_items"]}, {self.plantilla.pk, self.inactiva.pk})
        self.assertContains(response, self.plantilla.nombre)
        self.assertContains(response, self.inactiva.nombre)
        self.assertNotContains(response, self.plantillas["medical_spa"].nombre)
        for item in response.context["mobile_recipe_template_items"]:
            self.assertEqual(item["edit_url"], self.url("clinica_editar_plantilla_receta", plantilla=item["plantilla"]))
        self.assertEqual(self.conteos(), before)

    def test_administrator_only_navigation_never_grants_recipes_patients_or_calendar(self):
        response = self.client.get(self.url())
        self.assertTrue(response.context["can_manage_recipe_templates"])
        self.assertFalse(response.context["app_home_disponible"])
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        self.assertEqual(response.context["app_modules_url"], hub)
        self.assertContains(response, f'href="{hub}"')
        self.assertNotContains(response, 'rel="manifest"')
        for route, args in (("clinica_pacientes", [self.empresa.slug]), ("clinica_recetas_paciente", [self.empresa.slug, self.paciente.pk]),
                            ("clinica_crear_receta_paciente", [self.empresa.slug, self.paciente.pk])):
            url = reverse(route, args=args)+"?app=1"
            self.assertNotContains(response, f'href="{url}"')
            self.assertIn(self.client.get(url).status_code, (302, 403))
        self.rol.refresh_from_db()
        for field in ("puede_ver_pacientes", "puede_ver_calendario", "puede_ver_recetas", "puede_crear_recetas", "puede_enviar_recetas"):
            self.assertFalse(getattr(self.rol, field))

    def test_recipe_view_create_send_permissions_do_not_authorize_template_management(self):
        before = self.conteos()
        for permisos in ({"puede_ver_recetas": True}, {"puede_crear_recetas": True}, {"puede_enviar_recetas": True},
                         {"puede_ver_recetas": True, "puede_crear_recetas": True, "puede_enviar_recetas": True}):
            self.asignar_rol(**permisos)
            for view, route, plantilla, method in (
                (views.plantillas_recetas, "clinica_plantillas_recetas", None, "get"),
                (views.crear_plantilla_receta, "clinica_crear_plantilla_receta", None, "post"),
                (views.editar_plantilla_receta, "clinica_editar_plantilla_receta", self.plantilla, "post"),
            ):
                with self.subTest(permisos=permisos, route=route):
                    response = getattr(self.client, method)(self.url(route, plantilla=plantilla), self.datos() if method == "post" else {})
                    self.assertIn(response.status_code, (302, 403))
                    with self.assertRaises(PermissionDenied):
                        self.request(view, route, method, plantilla=plantilla, data=self.datos())
        # Role fixture changes are audited; no clinical or fiscal mutation is allowed.
        self.assertEqual(self.conteos()[:-1], before[:-1])

    def test_legacy_expediente_alone_matches_existing_global_template_menu(self):
        rol = self.asignar_rol(granular=False, puede_expediente_clinico=True)
        for route in ("clinica_plantillas_recetas", "clinica_crear_plantilla_receta"):
            response = self.client.get(self.url(route))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["can_manage_recipe_templates"])
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(nombre="Plantilla legado global"))
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_pacientes)

    def test_legacy_patients_only_does_not_exploit_original_global_mapper_gap(self):
        self.asignar_rol(granular=False, puede_pacientes=True)
        before = self.conteos()
        for view, route, plantilla in ((views.plantillas_recetas, "clinica_plantillas_recetas", None),
                                      (views.crear_plantilla_receta, "clinica_crear_plantilla_receta", None),
                                      (views.editar_plantilla_receta, "clinica_editar_plantilla_receta", self.plantilla)):
            self.assertEqual(self.client.get(self.url(route, plantilla=plantilla)).status_code, 403)
            with self.assertRaises(PermissionDenied):
                self.request(view, route, plantilla=plantilla)
        self.assertEqual(self.conteos(), before)

    def test_legacy_configuration_alone_keeps_existing_menu_management_without_grants(self):
        rol = self.asignar_rol(granular=False, puede_configuracion_clinica=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage_recipe_templates"])
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(nombre="Plantilla configuración legado"))
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_expediente_clinico)
        self.assertFalse(rol.puede_pacientes)

    def test_hub_advertises_only_existing_admin_union_not_recipe_or_granular_config_permissions(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for granular, permisos, allowed in (
            (True, {"puede_editar_historia_clinica": True}, True),
            (True, {"puede_configuracion_clinica": True}, False),
            (True, {"puede_ver_recetas": True, "puede_crear_recetas": True, "puede_enviar_recetas": True}, False),
            (False, {"puede_configuracion_clinica": True}, True),
            (False, {"puede_expediente_clinico": True}, True),
            (False, {"puede_pacientes": True}, False),
        ):
            with self.subTest(granular=granular, permisos=permisos):
                self.asignar_rol(granular=granular, **permisos)
                response = self.client.get(hub)
                self.assertEqual(response.status_code, 200)
                cards = {m["key"]: m for m in response.context["modulos_app"]}
                self.assertEqual("plantillas_recetas" in cards, allowed)
                if allowed:
                    self.assertEqual(cards["plantillas_recetas"]["url"], self.url())
                    self.assertEqual(cards["plantillas_recetas"]["estado"], "disponible")
                    self.assertEqual(self.client.get(cards["plantillas_recetas"]["url"]).status_code, 200)
                else:
                    with self.assertRaises(PermissionDenied):
                        self.request(views.crear_plantilla_receta, "clinica_crear_plantilla_receta", "post", data=self.datos())

    def test_membership_missing_company_role_module_and_feature_block_direct_access(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        with self.assertRaises(PermissionDenied):
            self.request(views.plantillas_recetas, "clinica_plantillas_recetas", empresa=self.empresas["medical_spa"])
        no_access = Usuario.objects.create_user(username="mobile-template-no-company", rol_sistema=self.rol)
        request = RequestFactory().get(self.url())
        request.user = no_access
        with self.assertRaises(PermissionDenied):
            views.plantillas_recetas(request, empresa_slug=self.empresa.slug)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            self.request(views.crear_plantilla_receta, "clinica_crear_plantilla_receta", "post", data=self.datos())
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        with patch("clinica.views._recetas_avanzadas_activas", return_value=False):
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            self.assertEqual(self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos()).status_code, 404)

    def test_mobile_list_post_is_read_only_and_original_web_post_stays_unchanged(self):
        before = self.conteos()
        head = self.client.head(self.url())
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertIn("no-store", head["Cache-Control"])
        response = self.client.post(self.url(), self.datos())
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.request(views.plantillas_recetas, "clinica_plantillas_recetas", "post", data=self.datos())
        original = self.client.post(self.url(mobile=False), {"nombre": "No se crea desde listado"})
        self.assertEqual(original.status_code, 200)
        self.assertTemplateUsed(original, "clinica/plantillas_recetas.html")
        self.assertEqual(self.conteos(), before)

    def test_create_manual_lines_reuses_parser_saver_atomic_actor_and_audit(self):
        before = self.conteos()
        with patch("clinica.views._extraer_lineas_receta", wraps=views._extraer_lineas_receta) as parser, \
             patch("clinica.views._guardar_detalles_plantilla", wraps=views._guardar_detalles_plantilla) as saver:
            response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos())
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.assertIn("no-store", response["Cache-Control"])
        parser.assert_called_once()
        saver.assert_called_once()
        plantilla = PlantillaReceta.objects.get(empresa=self.empresa, nombre="Plantilla nueva móvil")
        self.assertEqual(plantilla.creada_por_id, self.usuario.pk)
        self.assertTrue(plantilla.activa)
        self.assertEqual(list(plantilla.detalles.values_list("medicamento_manual", "cantidad", "indicaciones", "observaciones", "orden")),
                         [("Externo uno", "Cantidad uno", "Texto uno", "Nota uno", 0), ("Externo dos", "Cantidad dos", "Texto dos", "Nota dos", 1)])
        audit = RegistroAuditoria.objects.get(modelo="plantillareceta", objeto_id=str(plantilla.pk), accion="crear")
        self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (self.usuario.pk, self.empresa.pk, "POST"))
        self.assertEqual(audit.cambios["nombre"]["nuevo"], plantilla.nombre)
        self.assertEqual(self.conteos()[2:-1], before[2:-1])

    def test_three_shared_clinics_use_original_spa_product_without_stock_mutations(self):
        before = self.conteos()
        for slug in ("medical_spa", "luque_aestetic", "serviciosmedicos"):
            response = self.client.post(self.url("clinica_crear_plantilla_receta", self.empresas[slug]), self.datos(
                nombre=f"Catálogo {slug}", medicamento_producto_id=[str(self.producto_spa.pk), ""], medicamento_manual=["Ignorado al elegir catálogo", "Externo dos"],
            ))
            self.assertRedirects(response, self.url(empresa=self.empresas[slug]), fetch_redirect_response=False)
            plantilla = PlantillaReceta.objects.get(empresa=self.empresas[slug], nombre=f"Catálogo {slug}")
            self.assertEqual(list(plantilla.detalles.values_list("producto_id", "medicamento_manual")), [(self.producto_spa.pk, ""), (None, "Externo dos")])
        self.producto_spa.refresh_from_db()
        self.assertEqual(self.producto_spa.empresa_id, self.empresas["medical_spa"].pk)
        self.assertEqual(self.conteos()[2:-1], before[2:-1])

    def test_hospital_rejects_product_even_from_own_company_without_writes(self):
        for producto in (self.producto_hospital, self.producto_spa):
            with self.subTest(producto=producto.pk):
                before = self.conteos()
                response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(medicamento_producto_id=[str(producto.pk)], medicamento_manual=[""]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].non_field_errors())
                self.assertContains(response, "ya no está disponible")
                self.assertEqual(self.conteos(), before)

    def test_foreign_inactive_deleted_missing_product_ids_preserve_edit_and_audit(self):
        empresa, plantilla = self.empresas["luque_aestetic"], self.plantillas["luque_aestetic"]
        for producto_id in (self.producto_luque.pk, self.producto_hospital.pk, self.producto_inactivo.pk, self.producto_eliminado.pk, 999999, "forged"):
            with self.subTest(producto_id=producto_id):
                before = self.conteos()
                response = self.client.post(self.url("clinica_editar_plantilla_receta", empresa, plantilla), self.datos(
                    nombre="Edición inválida", medicamento_producto_id=[str(producto_id)], medicamento_manual=[""],
                ))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].non_field_errors())
                plantilla.refresh_from_db()
                self.assertEqual(plantilla.nombre, "Plantilla luque_aestetic")
                self.assertEqual(self.conteos(), before)

    def test_foreign_template_id_denied_get_post_no_mutations(self):
        before = self.conteos()
        foreign = self.plantillas["medical_spa"]
        for method in ("get", "post"):
            response = getattr(self.client, method)(self.url("clinica_editar_plantilla_receta", plantilla=foreign), self.datos() if method == "post" else {})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.conteos(), before)

    def test_invalid_title_keeps_exact_bound_fields_and_all_valid_lines(self):
        data, before = self.datos(nombre=""), self.conteos()
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_recipe_template_form.html")
        form = response.context["form"]
        self.assertIn("nombre", form.errors)
        for campo in ("diagnostico", "indicaciones_generales", "observaciones"):
            self.assertEqual(form[campo].value(), data[campo])
        self.assertEqual([line["medicamento_manual"] for line in response.context["lineas_iniciales"]], ["Externo uno", "Externo dos"])
        self.assertEqual([line["cantidad"] for line in response.context["lineas_iniciales"]], ["Cantidad uno", "Cantidad dos"])
        self.assertEqual(self.conteos(), before)

    def test_empty_lines_reuses_original_validation_and_does_not_save(self):
        before = self.conteos()
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(medicamento_producto_id=[""], medicamento_manual=[""]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agregue al menos un medicamento a la plantilla.")
        self.assertEqual(response.context["form"]["nombre"].value(), "Plantilla nueva móvil")
        self.assertEqual(self.conteos(), before)

    def test_name_unique_per_company_keeps_original_form_constraint(self):
        before = self.conteos()
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(nombre=self.plantilla.nombre))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(self.conteos(), before)
        response = self.client.post(self.url("clinica_crear_plantilla_receta", self.empresas["medical_spa"]), self.datos(nombre=self.plantilla.nombre))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PlantillaReceta.objects.filter(nombre=self.plantilla.nombre).count(), 2)

    def test_edit_preserves_creator_creation_date_replaces_lines_and_records_changes(self):
        creator = Usuario.objects.create_user(username="mobile-template-original-creator", empresa=self.empresa)
        old_date = timezone.now()-timedelta(days=10)
        PlantillaReceta.objects.filter(pk=self.plantilla.pk).update(creada_por=creator, fecha_creacion=old_date, fecha_actualizacion=old_date)
        old_line_ids = list(self.plantilla.detalles.values_list("pk", flat=True))
        data = self.datos(nombre="Nombre editado", medicamento_producto_id=[""], medicamento_manual=["Externo actualizado"],
                          medicamento_cantidad=["Nueva cantidad"], medicamento_indicaciones=["Nuevo texto"], medicamento_observaciones=["Nueva nota"])
        del data["activa"]
        response = self.client.post(self.url("clinica_editar_plantilla_receta", plantilla=self.plantilla), data)
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.plantilla.refresh_from_db()
        self.assertEqual(self.plantilla.creada_por_id, creator.pk)
        self.assertEqual(self.plantilla.fecha_creacion, old_date)
        self.assertGreater(self.plantilla.fecha_actualizacion, old_date)
        self.assertFalse(self.plantilla.activa)
        self.assertFalse(PlantillaRecetaDetalle.objects.filter(pk__in=old_line_ids).exists())
        self.assertEqual(list(self.plantilla.detalles.values_list("medicamento_manual", "cantidad")), [("Externo actualizado", "Nueva cantidad")])
        audit = RegistroAuditoria.objects.get(modelo="plantillareceta", objeto_id=str(self.plantilla.pk), accion="modificar", metodo_http="POST")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertEqual(audit.cambios["nombre"], {"anterior": "Plantilla hospital_mia", "nuevo": "Nombre editado"})
        self.assertEqual(audit.cambios["activa"], {"anterior": True, "nuevo": False})

    def test_posted_company_creator_dates_and_extra_actions_are_not_authoritative(self):
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos(
            empresa=self.empresas["medical_spa"].pk, creada_por=999999, fecha_creacion="2001-01-01", fecha_actualizacion="2001-01-01",
            accion="eliminar", paciente=self.pacientes["medical_spa"].pk,
        ))
        self.assertEqual(response.status_code, 302)
        plantilla = PlantillaReceta.objects.get(nombre="Plantilla nueva móvil")
        self.assertEqual((plantilla.empresa_id, plantilla.creada_por_id), (self.empresa.pk, self.usuario.pk))
        self.assertEqual(timezone.localtime(plantilla.fecha_creacion).date(), timezone.localdate())
        self.assertTrue(PlantillaReceta.objects.filter(pk=self.plantilla.pk).exists())

    def test_create_transaction_rolls_back_template_details_and_audit_on_original_saver_failure(self):
        before = self.conteos()
        original = views._guardar_detalles_plantilla
        def fail(plantilla, lineas):
            original(plantilla, lineas)
            raise RuntimeError("Fallo de persistencia simulado")
        with patch("clinica.views._guardar_detalles_plantilla", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "Fallo de persistencia"):
                self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos())
        self.assertEqual(self.conteos(), before)

    def test_edit_transaction_rolls_back_fields_existing_lines_and_audit_on_saver_failure(self):
        before, snapshot = self.conteos(), list(self.plantilla.detalles.values())
        original = views._guardar_detalles_plantilla
        def fail(plantilla, lineas):
            original(plantilla, lineas)
            raise RuntimeError("Fallo de persistencia simulado")
        with patch("clinica.views._guardar_detalles_plantilla", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "Fallo de persistencia"):
                self.client.post(self.url("clinica_editar_plantilla_receta", plantilla=self.plantilla), self.datos(nombre="Nombre no debe guardarse"))
        self.plantilla.refresh_from_db()
        self.assertEqual(self.plantilla.nombre, "Plantilla hospital_mia")
        self.assertEqual(list(self.plantilla.detalles.values()), snapshot)
        self.assertEqual(self.conteos(), before)

    def test_saved_active_template_available_in_original_recipe_form_and_inactive_excluded(self):
        response = self.client.post(self.url("clinica_crear_plantilla_receta"), self.datos())
        self.assertEqual(response.status_code, 302)
        plantilla = PlantillaReceta.objects.get(nombre="Plantilla nueva móvil")
        self.asignar_rol(puede_crear_recetas=True)
        url = reverse("clinica_crear_receta_paciente", args=[self.empresa.slug, self.paciente.pk])
        before = self.conteos()
        for suffix in ("", "?app=1"):
            response = self.client.get(url+suffix)
            self.assertEqual(response.status_code, 200)
            self.assertIn(plantilla.pk, [p.pk for p in response.context["plantillas"]])
            self.assertNotIn(self.inactiva.pk, [p.pk for p in response.context["plantillas"]])
            item = next(item for item in response.context["plantillas_json"] if item["id"] == plantilla.pk)
            self.assertEqual([line["medicamento_manual"] for line in item["detalles"]], ["Externo uno", "Externo dos"])
        self.assertEqual(self.conteos(), before)
        self.assertIn(self.client.get(self.url()).status_code, (302, 403))
        self.asignar_rol(puede_editar_historia_clinica=True)
        data = self.datos(nombre=plantilla.nombre)
        del data["activa"]
        self.assertEqual(self.client.post(self.url("clinica_editar_plantilla_receta", plantilla=plantilla), data).status_code, 302)
        self.asignar_rol(puede_crear_recetas=True)
        for suffix in ("", "?app=1"):
            response = self.client.get(url+suffix)
            self.assertNotIn(plantilla.pk, [p.pk for p in response.context["plantillas"]])

    def test_web_create_and_edit_keep_original_templates_and_redirects(self):
        data = self.datos(nombre="Plantilla web intacta")
        del data["app"]
        response = self.client.post(self.url("clinica_crear_plantilla_receta", mobile=False), data)
        self.assertRedirects(response, self.url(mobile=False), fetch_redirect_response=False)
        plantilla = PlantillaReceta.objects.get(nombre="Plantilla web intacta")
        response = self.client.get(self.url("clinica_editar_plantilla_receta", plantilla=plantilla, mobile=False))
        self.assertTemplateUsed(response, "clinica/plantilla_receta_form.html")
        response = self.client.post(self.url("clinica_editar_plantilla_receta", plantilla=plantilla, mobile=False), {**data, "nombre": "Plantilla web editada"})
        self.assertRedirects(response, self.url(mobile=False), fetch_redirect_response=False)
