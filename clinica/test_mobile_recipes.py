from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM
from crm.services import WhatsAppAPIError
from facturacion.models import MovimientoInventario, Producto
from .forms import RecetaMedicaForm
from .models import ManualReceta, Paciente, PlantillaReceta, PlantillaRecetaDetalle, ProfesionalSalud, RecetaMedica, RecetaMedicaDetalle
from . import views


class MobileRecipesIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales, cls.recetas, cls.plantillas = {}, {}, {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        agenda, _ = Modulo.objects.get_or_create(codigo="agenda_citas", defaults={"nombre": "Agenda"})
        facturacion, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Recetas clínica {slug}", slug=slug, rtn=f"MR-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            for modulo in (cls.modulo, agenda, facturacion):
                EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, manuales_pdf_habilitados=True)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente receta {slug}", identidad=f"080119998300{indice}",
                expediente_codigo=f"MR-{indice}", correo=f"mr-{indice}@example.com", whatsapp=f"5049999300{indice}",
            )
            cls.pacientes[slug] = paciente
            profesional = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional {slug}")
            cls.profesionales[slug] = profesional
            receta = RecetaMedica.objects.create(
                empresa=empresa, paciente=paciente, profesional=profesional, diagnostico=f"Diagnóstico {slug}",
                indicaciones="Indicaciones generales existentes", observaciones="Observación existente",
            )
            cls.recetas[slug] = receta
            RecetaMedicaDetalle.objects.create(receta=receta, medicamento_manual=f"Medicamento {slug}", cantidad="10 tabletas", indicaciones="Una diaria", orden=0)
            plantilla = PlantillaReceta.objects.create(
                empresa=empresa, nombre=f"Plantilla {slug}", diagnostico="Diagnóstico de plantilla",
                indicaciones_generales="Hidratación según indicación", observaciones="Control en cinco días",
            )
            cls.plantillas[slug] = plantilla
            PlantillaRecetaDetalle.objects.create(plantilla=plantilla, medicamento_manual="Medicamento plantilla", cantidad="1 caja", indicaciones="Cada 12 horas")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.receta = cls.recetas[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Recetas móvil completo", codigo="mobile-recipes-all", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_ver_calendario=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
            puede_ver_recetas=True, puede_crear_recetas=True, puede_enviar_recetas=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-recipes-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.producto_spa = Producto.objects.create(empresa=cls.empresas["medical_spa"], nombre="Medicamento catálogo Spa", codigo="MR-SPA", precio=100)
        cls.producto_hospital = Producto.objects.create(empresa=cls.empresa, nombre="Producto no prescribible Hospital", codigo="MR-HM", precio=50)
        cls.producto_luque = Producto.objects.create(empresa=cls.empresas["luque_aestetic"], nombre="Producto no prescribible Luque", codigo="MR-LUQ", precio=90)
        cls.producto_inactivo = Producto.objects.create(empresa=cls.empresas["medical_spa"], nombre="Medicamento inactivo", codigo="MR-INACTIVO", precio=10, activo=False)
        PlantillaReceta.objects.create(empresa=cls.empresa, nombre="Plantilla inactiva", activa=False)

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="dv-mobile-recipes-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def url(self, nombre, receta=None, paciente=None, empresa=None, mobile=True):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug, "paciente_id": (paciente or self.paciente).pk}
        if receta is not None:
            kwargs["receta_id"] = receta.pk
        return reverse(nombre, kwargs=kwargs) + ("?app=1" if mobile else "")

    def datos(self, empresa=None, **overrides):
        empresa = empresa or self.empresa
        data = {
            "app": "1", "fecha": (timezone.localdate()-timedelta(days=2)).isoformat(),
            "profesional": self.profesionales[empresa.slug].pk,
            "diagnostico": "Diagnóstico escrito por el profesional", "indicaciones": "Indicaciones generales conservadas",
            "observaciones": "Observaciones conservadas", "medicamento_producto_id": ["", ""],
            "medicamento_manual": ["Medicamento externo uno", "Medicamento externo dos"],
            "medicamento_cantidad": ["1 caja", "10 tabletas"],
            "medicamento_indicaciones": ["Una cada 12 horas", "Una diaria"],
            "medicamento_observaciones": ["Con alimentos", "Por la noche"],
        }
        data.update(overrides)
        return data

    def asignar_rol(self, empresa=None, **permisos):
        rol = RolSistema.objects.create(
            nombre="Recetas móvil restringido", codigo=f"mobile-recipes-role-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def manual(self, empresa=None, **overrides):
        data = {"empresa": empresa or self.empresa, "titulo": "Manual receta protegido", "creado_por": self.usuario,
                "archivo": SimpleUploadedFile("manual-receta.pdf", b"%PDF-1.4 manual protegido", content_type="application/pdf")}
        data.update(overrides)
        return ManualReceta.objects.create(**data)

    def conteos(self):
        return tuple(model.objects.count() for model in (RecetaMedica, RecetaMedicaDetalle, Paciente, ProfesionalSalud, MovimientoInventario))

    def test_four_clinics_render_mobile_and_keep_original_web_recipe_templates(self):
        for empresa in self.empresas.values():
            paciente, receta = self.pacientes[empresa.slug], self.recetas[empresa.slug]
            for route, documento, mobile_template, web_template in (
                ("clinica_recetas_paciente", None, "mobile_recipe_list.html", "recetas_paciente.html"),
                ("clinica_crear_receta_paciente", None, "mobile_recipe_form.html", "receta_form_avanzada.html"),
                ("clinica_receta_imprimir", receta, "mobile_recipe_preview.html", "receta_imprimir.html"),
            ):
                with self.subTest(empresa=empresa.slug, route=route):
                    mobile = self.client.get(self.url(route, documento, paciente, empresa))
                    self.assertEqual(mobile.status_code, 200)
                    self.assertTemplateUsed(mobile, "clinica/"+mobile_template)
                    self.assertIn("no-store", mobile["Cache-Control"])
                    self.assertNotContains(mobile, "<table")
                    web = self.client.get(self.url(route, documento, paciente, empresa, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/"+web_template)
                    self.assertTemplateNotUsed(web, "clinica/"+mobile_template)

    def test_app_flag_does_not_replace_other_clinic_web_recipes(self):
        empresa = Empresa.objects.create(nombre="Otra clínica recetas", slug="otra_clinica_recetas", rtn="MR-OTRA", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente fuera de cuatro", expediente_codigo="MR-OTRO")
        self.usuario.empresas_acceso.add(empresa)
        legacy = RolSistema.objects.create(nombre="Recetas legado fuera", codigo="mobile-recipes-outside", puede_pacientes=True, puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=empresa, rol_sistema=legacy)
        response = self.client.get(self.url("clinica_crear_receta_paciente", paciente=paciente, empresa=empresa))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/receta_form_avanzada.html")
        self.assertFalse(response.context.get("modo_app", False))

    def test_viewer_actions_follow_exact_existing_preview_and_send_permission(self):
        self.asignar_rol(puede_ver_recetas=True)
        response = self.client.get(self.url("clinica_recetas_paciente"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_recipes"])
        self.assertFalse(response.context["can_create_recipe"])
        self.assertFalse(response.context["can_send_recipe"])
        for route, receta in (("clinica_crear_receta_paciente", None), ("clinica_receta_imprimir", self.receta),
                              ("clinica_receta_pdf", self.receta), ("clinica_enviar_receta_correo", self.receta), ("clinica_enviar_receta_whatsapp", self.receta)):
            self.assertNotContains(response, f'href="{self.url(route, receta)}"')
        factory = RequestFactory()
        for view, route, receta in (
            (views.crear_receta_paciente, "clinica_crear_receta_paciente", None),
            (views.imprimir_receta_paciente, "clinica_receta_imprimir", self.receta),
            (views.descargar_receta_pdf, "clinica_receta_pdf", self.receta),
            (views.enviar_receta_correo, "clinica_enviar_receta_correo", self.receta),
            (views.enviar_receta_whatsapp, "clinica_enviar_receta_whatsapp", self.receta),
        ):
            with self.subTest(route=route):
                request = factory.post(self.url(route, receta), {"app": "1"}) if "enviar" in route else factory.get(self.url(route, receta))
                request.user = Usuario.objects.get(pk=self.usuario.pk)
                kwargs = {"empresa_slug": self.empresa.slug, "paciente_id": self.paciente.pk}
                if receta:
                    kwargs["receta_id"] = receta.pk
                with self.assertRaises(PermissionDenied):
                    view(request, **kwargs)

    def test_company_role_module_and_membership_guard_direct_recipe_access(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        request = RequestFactory().get(self.url("clinica_recetas_paciente", paciente=self.pacientes["medical_spa"], empresa=self.empresas["medical_spa"]))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.recetas_paciente(request, empresa_slug="medical_spa", paciente_id=self.pacientes["medical_spa"].pk)
        request = RequestFactory().get(self.url("clinica_recetas_paciente"))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            views.recetas_paciente(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        request.user = Usuario.objects.create_user(username="mobile-recipes-no-access", rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            views.recetas_paciente(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk)

    def test_legacy_expediente_permission_remains_compatible(self):
        # Legacy middleware also requires pacientes; the mobile/menu guard keeps expediente mandatory.
        legacy = RolSistema.objects.create(nombre="Recetas legado", codigo="mobile-recipes-legacy", puede_pacientes=True, puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=legacy)
        for route, receta in (("clinica_recetas_paciente", None), ("clinica_crear_receta_paciente", None), ("clinica_receta_imprimir", self.receta)):
            with self.subTest(route=route):
                response = self.client.get(self.url(route, receta))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["modo_app"])

    def test_legacy_patients_only_cannot_use_recipe_actions_through_old_web_mapping_gap(self):
        legacy = RolSistema.objects.create(nombre="Pacientes legado sin recetas", codigo="mobile-recipes-legacy-patients-only", puede_pacientes=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=legacy)
        directory = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(directory.status_code, 200)
        before = self.conteos()
        factory = RequestFactory()
        with patch("clinica.views.HTML") as pdf, patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as email, \
             patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            for view, route, receta, method in (
                (views.recetas_paciente, "clinica_recetas_paciente", None, "get"),
                (views.crear_receta_paciente, "clinica_crear_receta_paciente", None, "post"),
                (views.imprimir_receta_paciente, "clinica_receta_imprimir", self.receta, "get"),
                (views.descargar_receta_pdf, "clinica_receta_pdf", self.receta, "get"),
                (views.enviar_receta_correo, "clinica_enviar_receta_correo", self.receta, "post"),
                (views.enviar_receta_whatsapp, "clinica_enviar_receta_whatsapp", self.receta, "post"),
            ):
                with self.subTest(route=route):
                    url = self.url(route, receta)
                    response = self.client.post(url, self.datos()) if method == "post" else self.client.get(url)
                    self.assertEqual(response.status_code, 403)
                    request = factory.post(url, {"app": "1"}) if method == "post" else factory.get(url)
                    request.user = Usuario.objects.get(pk=self.usuario.pk)
                    kwargs = {"empresa_slug": self.empresa.slug, "paciente_id": self.paciente.pk}
                    if receta:
                        kwargs["receta_id"] = receta.pk
                    with self.assertRaises(PermissionDenied):
                        view(request, **kwargs)
            pdf.assert_not_called()
            email.assert_not_called()
            upload.assert_not_called()
            send.assert_not_called()
        self.assertEqual(self.conteos(), before)
        legacy.refresh_from_db()
        self.assertFalse(legacy.puede_expediente_clinico)

    def test_original_form_catalog_template_and_repeated_line_contract_reused(self):
        manual = self.manual()
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                paciente = self.pacientes[empresa.slug]
                mobile = self.client.get(self.url("clinica_crear_receta_paciente", paciente=paciente, empresa=empresa))
                web = self.client.get(self.url("clinica_crear_receta_paciente", paciente=paciente, empresa=empresa, mobile=False))
                self.assertIsInstance(mobile.context["form"], RecetaMedicaForm)
                self.assertEqual(set(mobile.context["form"].fields), set(web.context["form"].fields))
                self.assertEqual(mobile.context["catalogo_medicamentos"], web.context["catalogo_medicamentos"])
                self.assertEqual(mobile.context["plantillas_json"], web.context["plantillas_json"])
                self.assertEqual({item["id"] for item in mobile.context["plantillas_json"]}, {self.plantillas[empresa.slug].pk})
                catalogo_ids = {item["id"] for item in mobile.context["catalogo_medicamentos"]}
                self.assertEqual(catalogo_ids, set() if empresa == self.empresa else {self.producto_spa.pk})
                self.assertContains(mobile, 'name="app" value="1"')
                self.assertTemplateUsed(mobile, "clinica/includes/receta_medicamentos_editor.html")
                for field in ("fecha", "profesional", "diagnostico", "indicaciones", "observaciones"):
                    self.assertContains(mobile, f'name="{field}"')
                self.assertContains(mobile, 'hiddenProduct.name = "medicamento_producto_id"')
                self.assertContains(mobile, 'hiddenManual.name = "medicamento_manual"')
        self.assertContains(self.client.get(self.url("clinica_crear_receta_paciente")), f'value="{manual.pk}"')

    def test_mobile_create_matches_web_storage_parser_m2m_and_audit_without_inventory_writes(self):
        manual = self.manual()
        web_data = self.datos(manuales=[manual.pk])
        web_data.pop("app")
        web_response = self.client.post(self.url("clinica_crear_receta_paciente", mobile=False), web_data)
        self.assertEqual(web_response.status_code, 302)
        web = RecetaMedica.objects.filter(empresa=self.empresa).latest("pk")
        with patch("clinica.views._extraer_lineas_receta", wraps=views._extraer_lineas_receta) as parse, \
             patch("clinica.views._guardar_detalles_receta", wraps=views._guardar_detalles_receta) as save_lines:
            response = self.client.post(self.url("clinica_crear_receta_paciente"), self.datos(manuales=[manual.pk]))
        parse.assert_called_once()
        save_lines.assert_called_once()
        receta = RecetaMedica.objects.filter(empresa=self.empresa).latest("pk")
        self.assertRedirects(response, self.url("clinica_receta_imprimir", receta), fetch_redirect_response=False)
        fields = ("paciente_id", "profesional_id", "fecha", "diagnostico", "indicaciones", "observaciones", "creada_por_id")
        self.assertEqual(tuple(getattr(receta, field) for field in fields), tuple(getattr(web, field) for field in fields))
        line_fields = ("producto_id", "medicamento_manual", "cantidad", "indicaciones", "observaciones", "orden")
        self.assertEqual(list(receta.detalles_medicamentos.values_list(*line_fields)), list(web.detalles_medicamentos.values_list(*line_fields)))
        self.assertEqual(list(receta.manuales.all()), [manual])
        self.assertFalse(receta.productos.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo="recetamedica", objeto_id=str(receta.pk), accion="crear", usuario=self.usuario).exists())

    def test_shared_medical_spa_catalog_is_kept_for_other_three_clinics(self):
        for slug in ("medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa, paciente = self.empresas[slug], self.pacientes[slug]
            data = self.datos(empresa, medicamento_producto_id=[str(self.producto_spa.pk), ""], medicamento_manual=["", "Externo sin catálogo"])
            response = self.client.post(self.url("clinica_crear_receta_paciente", paciente=paciente, empresa=empresa), data)
            self.assertEqual(response.status_code, 302)
            receta = RecetaMedica.objects.filter(empresa=empresa, paciente=paciente).latest("pk")
            self.assertEqual(list(receta.productos.values_list("pk", flat=True)), [self.producto_spa.pk])
            self.assertEqual(receta.detalles_medicamentos.get(orden=0).producto_id, self.producto_spa.pk)
            self.assertEqual(receta.detalles_medicamentos.get(orden=1).medicamento_manual, "Externo sin catálogo")
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_invalid_post_keeps_valid_medication_lines_diagnosis_and_notes_without_partial_save(self):
        before = self.conteos()
        response = self.client.post(self.url("clinica_crear_receta_paciente"), self.datos(fecha="fecha inválida"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_recipe_form.html")
        self.assertIn("fecha", response.context["form"].errors)
        self.assertEqual(response.context["form"]["diagnostico"].value(), "Diagnóstico escrito por el profesional")
        self.assertEqual([line["cantidad"] for line in response.context["lineas_iniciales"]], ["1 caja", "10 tabletas"])
        self.assertEqual([line["observaciones"] for line in response.context["lineas_iniciales"]], ["Con alimentos", "Por la noche"])
        self.assertContains(response, "Observaciones conservadas")
        self.assertEqual(self.conteos(), before)

    def test_foreign_professional_manual_and_unavailable_catalog_items_are_rejected(self):
        manual_ajeno = self.manual(self.empresas["medical_spa"], titulo="Manual de otra empresa")
        for field, value in (
            ("profesional", self.profesionales["medical_spa"].pk),
            ("manuales", [manual_ajeno.pk]),
            ("medicamento_producto_id", [str(self.producto_hospital.pk), ""]),
        ):
            with self.subTest(field=field):
                before = self.conteos()
                response = self.client.post(self.url("clinica_crear_receta_paciente"), self.datos(**{field: value}))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(self.conteos(), before)
        spa, paciente_spa = self.empresas["medical_spa"], self.pacientes["medical_spa"]
        for producto in (self.producto_luque, self.producto_inactivo):
            response = self.client.post(self.url("clinica_crear_receta_paciente", paciente=paciente_spa, empresa=spa), self.datos(
                spa, medicamento_producto_id=[str(producto.pk), ""],
            ))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "ya no está disponible")

    def test_list_and_document_routes_are_patient_and_company_isolated(self):
        response = self.client.get(self.url("clinica_recetas_paciente"))
        self.assertEqual([receta.pk for receta in response.context["recetas"]], [self.receta.pk])
        self.assertNotContains(response, self.pacientes["medical_spa"].nombre)
        otro_paciente = Paciente.objects.create(empresa=self.empresa, nombre="Otro paciente misma empresa", expediente_codigo="MR-OTRO-PAC")
        with patch("clinica.views.HTML") as pdf, patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as email, \
             patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            for route in ("clinica_receta_imprimir", "clinica_receta_pdf", "clinica_enviar_receta_correo", "clinica_enviar_receta_whatsapp"):
                for paciente, receta in ((self.pacientes["medical_spa"], self.recetas["medical_spa"]), (self.paciente, self.recetas["medical_spa"]), (otro_paciente, self.receta)):
                    with self.subTest(route=route, paciente=paciente.pk, receta=receta.pk):
                        url = self.url(route, receta, paciente)
                        response = self.client.post(url, {"app": "1"}) if "enviar" in route else self.client.get(url)
                        self.assertEqual(response.status_code, 404)
            pdf.assert_not_called()
            email.assert_not_called()
            upload.assert_not_called()
            send.assert_not_called()

    def test_pdf_reuses_canonical_generator_and_same_html_bytes_only_app_is_inline(self):
        payload = b"%PDF-1.4 canonical recipe bytes"
        with patch("clinica.views.HTML") as engine, patch("clinica.views._generar_receta_pdf_bytes", wraps=views._generar_receta_pdf_bytes) as generator:
            engine.return_value.write_pdf.return_value = payload
            mobile = self.client.get(self.url("clinica_receta_pdf", self.receta))
            web = self.client.get(self.url("clinica_receta_pdf", self.receta, mobile=False))
        self.assertEqual(mobile.status_code, 200)
        self.assertEqual(web.status_code, 200)
        self.assertEqual(mobile.content, web.content)
        self.assertEqual(mobile.content, payload)
        self.assertTrue(mobile["Content-Disposition"].startswith("inline;"))
        self.assertTrue(web["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(mobile["Content-Type"], "application/pdf")
        self.assertIn("no-store", mobile["Cache-Control"])
        self.assertEqual(generator.call_count, 2)
        self.assertEqual(engine.call_args_list[0].kwargs["string"], engine.call_args_list[1].kwargs["string"])
        self.assertIn(self.paciente.nombre, engine.call_args.kwargs["string"])
        self.assertIn("Medicamento hospital_mia", engine.call_args.kwargs["string"])

    def test_email_keeps_original_pdf_manual_attachments_patient_recipient_and_mobile_return(self):
        manual = self.manual()
        self.receta.manuales.add(manual)
        with patch("clinica.views.HTML") as engine, patch("clinica.views.EmailMultiAlternatives.send", autospec=True, return_value=1) as send:
            engine.return_value.write_pdf.return_value = b"%PDF-1.4 receta"
            response = self.client.post(self.url("clinica_enviar_receta_correo", self.receta), {"app": "1"})
        self.assertRedirects(response, self.url("clinica_receta_imprimir", self.receta), fetch_redirect_response=False)
        send.assert_called_once()
        message = send.call_args.args[0]
        self.assertEqual(message.to, [self.paciente.correo])
        self.assertEqual(len(message.attachments), 2)
        self.assertEqual(message.attachments[0][1:], (b"%PDF-1.4 receta", "application/pdf"))
        self.assertEqual(message.attachments[1][1:], (b"%PDF-1.4 manual protegido", "application/pdf"))
        self.assertIn(self.empresa.nombre, message.subject)

    def test_whatsapp_uses_current_company_config_patient_number_and_original_documents(self):
        config = ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        ConfiguracionCRM.objects.create(empresa=self.empresas["medical_spa"], whatsapp_activo=True)
        manual = self.manual()
        self.receta.manuales.add(manual)
        documents = []
        def upload_document(configuration, path, mime):
            documents.append((configuration.pk, Path(path).read_bytes(), mime))
            return f"media-{len(documents)}"
        with patch("clinica.views.HTML") as engine, patch("clinica.views.subir_documento_whatsapp", side_effect=upload_document) as upload, \
             patch("clinica.views.enviar_documento_whatsapp") as send:
            engine.return_value.write_pdf.return_value = b"%PDF-1.4 receta"
            response = self.client.post(self.url("clinica_enviar_receta_whatsapp", self.receta), {"app": "1"})
        self.assertRedirects(response, self.url("clinica_receta_imprimir", self.receta), fetch_redirect_response=False)
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(documents, [(config.pk, b"%PDF-1.4 receta", "application/pdf"), (config.pk, b"%PDF-1.4 manual protegido", "application/pdf")])
        self.assertTrue(all(call.args[0].pk == config.pk and call.args[1] == self.paciente.whatsapp for call in send.call_args_list))

    def test_send_failures_keep_mobile_return_and_do_not_use_foreign_config(self):
        ConfiguracionCRM.objects.create(empresa=self.empresas["medical_spa"], whatsapp_activo=True)
        with patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            absent = self.client.post(self.url("clinica_enviar_receta_whatsapp", self.receta), {"app": "1"})
        self.assertRedirects(absent, self.url("clinica_receta_imprimir", self.receta), fetch_redirect_response=False)
        upload.assert_not_called()
        send.assert_not_called()
        with patch("clinica.views.HTML") as engine, patch("clinica.views.EmailMultiAlternatives.send", autospec=True, side_effect=RuntimeError("Transporte de correo no disponible")):
            engine.return_value.write_pdf.return_value = b"%PDF-receta"
            failed_email = self.client.post(self.url("clinica_enviar_receta_correo", self.receta), {"app": "1"}, follow=True)
        self.assertContains(failed_email, "No se pudo enviar la receta por correo")
        self.assertEqual(failed_email.redirect_chain[0][0], self.url("clinica_receta_imprimir", self.receta))
        ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        with patch("clinica.views.HTML") as engine, patch("clinica.views.subir_documento_whatsapp", side_effect=WhatsAppAPIError("Transporte de prueba no disponible")), \
             patch("clinica.views.enviar_documento_whatsapp") as send:
            engine.return_value.write_pdf.return_value = b"%PDF-receta"
            failed_whatsapp = self.client.post(self.url("clinica_enviar_receta_whatsapp", self.receta), {"app": "1"}, follow=True)
        self.assertContains(failed_whatsapp, "Transporte de prueba no disponible")
        self.assertEqual(failed_whatsapp.redirect_chain[0][0], self.url("clinica_receta_imprimir", self.receta))
        send.assert_not_called()

    def test_missing_patient_contacts_and_get_send_requests_do_not_send_anything(self):
        self.paciente.correo, self.paciente.whatsapp, self.paciente.telefono = "", "", ""
        self.paciente.save(update_fields=["correo", "whatsapp", "telefono"])
        ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        with patch("clinica.views.HTML") as pdf, patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as email, \
             patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            for route in ("clinica_enviar_receta_correo", "clinica_enviar_receta_whatsapp"):
                self.assertEqual(self.client.get(self.url(route, self.receta)).status_code, 405)
                response = self.client.post(self.url(route, self.receta), {"app": "1"})
                self.assertRedirects(response, self.url("clinica_receta_imprimir", self.receta), fetch_redirect_response=False)
            pdf.assert_not_called()
            email.assert_not_called()
            upload.assert_not_called()
            send.assert_not_called()

    def test_creator_without_preview_permission_stays_in_authorized_mobile_create_flow(self):
        self.asignar_rol(puede_crear_recetas=True)
        response = self.client.post(self.url("clinica_crear_receta_paciente", mobile=False), self.datos())
        self.assertRedirects(response, self.url("clinica_crear_receta_paciente"), fetch_redirect_response=False)
        screen = self.client.get(self.url("clinica_crear_receta_paciente"))
        self.assertEqual(screen.status_code, 200)
        self.assertFalse(screen.context["can_send_recipe"])
        self.assertFalse(screen.context["app_home_disponible"])
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        self.assertEqual(screen.context["app_modules_url"], hub)
        self.assertContains(screen, f'href="{hub}"')
        self.assertNotContains(screen, f'href="{reverse("agenda_mobile", args=[self.empresa.slug])}#inicio"')

    def test_recipe_patient_entry_is_additive_and_passive_gets_do_not_mutate_records(self):
        before = self.conteos()
        detail = self.client.get(reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, f'href="{self.url("clinica_recetas_paciente")}"')
        for route, receta in (("clinica_recetas_paciente", None), ("clinica_crear_receta_paciente", None), ("clinica_receta_imprimir", self.receta)):
            self.assertEqual(self.client.get(self.url(route, receta)).status_code, 200)
        self.assertEqual(self.conteos(), before)
        home = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertContains(home, 'href="#pacientes-app"')
        self.assertContains(home, 'id="patient-profile-app"')
        self.assertContains(home, 'id="factura-app"')
        self.assertContains(home, 'id="agenda-app"')

    def test_recipes_selector_uses_authorized_patient_directory_without_general_data_permission(self):
        rol = self.asignar_rol(puede_ver_pacientes=True, puede_ver_recetas=True)
        selector = reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1&destino=recetas"
        hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertEqual(hub.status_code, 200)
        module = next(item for item in hub.context["modulos_app"] if item["key"] == "recetas")
        self.assertEqual(module["url"], selector)
        self.assertEqual(module["estado"], "disponible")
        response = self.client.get(selector)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mobile_patient_recipe_mode"], "recetas")
        self.assertFalse(response.context["can_view_patient"])
        self.assertContains(response, f'href="{self.url("clinica_recetas_paciente")}"')
        general_url = reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
        self.assertNotContains(response, f'href="{general_url}"')
        recipes = self.client.get(self.url("clinica_recetas_paciente"))
        self.assertEqual(recipes.status_code, 200)
        self.assertFalse(recipes.context["can_send_recipe"])
        rol.refresh_from_db()
        self.assertFalse(rol.puede_ver_datos_generales_paciente)
        self.assertFalse(rol.puede_enviar_recetas)
        self.assertFalse(rol.puede_ver_calendario)

    def test_recipe_selector_search_and_pagination_keep_mode_and_current_company(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_recetas=True)
        Paciente.objects.bulk_create([
            Paciente(empresa=self.empresa, nombre=f"Selector receta {indice:02d}", expediente_codigo=f"MR-SELECTOR-{indice}")
            for indice in range(26)
        ])
        before = self.conteos()
        route = reverse("clinica_pacientes", args=[self.empresa.slug])
        first = self.client.get(route, {"app": "1", "destino": "recetas", "q": "Selector receta"})
        second = self.client.get(route, {"app": "1", "destino": "recetas", "q": "Selector receta", "page": "2"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context["pacientes"].paginator.count, 26)
        self.assertEqual(len(first.context["pacientes"]), 25)
        self.assertEqual(len(second.context["pacientes"]), 1)
        self.assertEqual(first.context["mobile_patient_recipe_mode"], "recetas")
        self.assertEqual(second.context["mobile_patient_recipe_mode"], "recetas")
        self.assertContains(first, 'name="destino" value="recetas"')
        self.assertContains(first, "page=2&amp;destino=recetas")
        self.assertContains(first, f'href="{route}?app=1&amp;destino=recetas"')
        self.assertNotContains(first, self.pacientes["medical_spa"].nombre)
        self.assertEqual(self.conteos(), before)

    def test_selector_create_mode_and_unavailable_modes_cannot_grant_recipe_permissions(self):
        rol = self.asignar_rol(puede_ver_pacientes=True, puede_crear_recetas=True)
        route = reverse("clinica_pacientes", args=[self.empresa.slug])
        screen = self.client.get(route, {"app": "1", "destino": "crear_receta"})
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(screen.context["mobile_patient_recipe_mode"], "crear_receta")
        self.assertContains(screen, f'href="{self.url("clinica_crear_receta_paciente")}"')
        self.assertNotContains(screen, f'href="{self.url("clinica_recetas_paciente")}"')
        module = next(item for item in self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])).context["modulos_app"] if item["key"] == "recetas")
        self.assertEqual(module["url"], f"{route}?app=1&destino=crear_receta")
        for destino in ("recetas", "otro_destino"):
            ignored = self.client.get(route, {"app": "1", "destino": destino})
            self.assertEqual(ignored.status_code, 200)
            self.assertFalse(ignored.context.get("mobile_patient_recipe_mode"))
            self.assertNotContains(ignored, f'href="{self.url("clinica_recetas_paciente")}"')
        self.assertIn(self.client.get(self.url("clinica_recetas_paciente")).status_code, (302, 403))
        rol.refresh_from_db()
        self.assertFalse(rol.puede_ver_recetas)
        self.assertFalse(rol.puede_enviar_recetas)
        self.asignar_rol(puede_ver_recetas=True)
        hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        module = next(item for item in hub.context["modulos_app"] if item["key"] == "recetas")
        self.assertEqual(module["estado"], "pendiente")
        self.assertFalse(module["url"])
        self.assertIn(self.client.get(route, {"app": "1", "destino": "recetas"}).status_code, (302, 403))
