from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import re

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM
from crm.services import WhatsAppAPIError
from .forms import ManualRecetaForm
from .models import EnvioManualPDF, ManualReceta, Paciente
from . import views


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileManualsIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes = {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Manuales clínica {slug}", slug=slug, rtn=f"MM-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=cls.modulo, defaults={"activo": True})
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, manuales_pdf_habilitados=True)
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente manual {slug}", expediente_codigo=f"MM-{indice}", identidad=f"080119997400{indice}",
                correo=f"mm-{indice}@example.com", telefono=f"9999400{indice}", prefijo_telefono="504",
            )
        cls.empresa, cls.paciente = cls.empresas["hospital_mia"], cls.pacientes["hospital_mia"]
        cls.rol = RolSistema.objects.create(
            nombre="Manuales móvil completo", codigo="mobile-manuals-all", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_ver_manuales_pdf=True, puede_enviar_manuales_pdf=True,
            puede_administrar_manuales_pdf=True, puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-manuals-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="dv-mobile-manuals-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.manuales = {
            empresa.slug: self.crear_manual(empresa=empresa, titulo=f"Manual {empresa.slug}")
            for empresa in self.empresas.values()
        }
        self.manual = self.manuales[self.empresa.slug]
        self.inactivo = self.crear_manual(titulo="Manual inactivo", activo=False)

    def crear_manual(self, empresa=None, **overrides):
        data = {
            "empresa": empresa or self.empresa, "titulo": "Manual de prueba", "descripcion": "Descripción clínica del manual",
            "archivo": self.archivo(), "creado_por": self.usuario,
        }
        data.update(overrides)
        return ManualReceta.objects.create(**data)

    def archivo(self, nombre="manual.pdf", contenido=b"%PDF-1.4\nmanual protegido\n%%EOF", content_type="application/pdf"):
        return SimpleUploadedFile(nombre, contenido, content_type=content_type)

    def url(self, route, manual=None, empresa=None, mobile=True):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if manual is not None:
            kwargs["manual_id"] = manual.pk
        return reverse(route, kwargs=kwargs) + ("?app=1" if mobile else "")

    def composer(self, **params):
        return self.client.get(self.url("clinica_manuales_pdf_app_enviar"), {"app": "1", **params})

    def datos(self, **overrides):
        data = {"app": "1", "manuales": [self.manual.pk], "paciente_id": self.paciente.pk, "canal": "correo"}
        data.update(overrides)
        return data

    def asignar_rol(self, empresa=None, **permisos):
        rol = RolSistema.objects.create(
            nombre="Manuales móvil restringido", codigo=f"mobile-manuals-role-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def cuerpo_archivo(self, response):
        try:
            return b"".join(response.streaming_content)
        finally:
            response.close()

    def assert_return_app(self, response, route="clinica_manuales_pdf"):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse(route, args=[self.empresa.slug]))
        self.assertEqual(parse_qs(urlsplit(response.url).query).get("app"), ["1"])

    def test_four_clinics_render_mobile_manual_screens_and_keep_web_templates(self):
        for empresa in self.empresas.values():
            for route, manual, mobile_template, web_template in (
                ("clinica_manuales_pdf", None, "mobile_manual_list.html", "manuales_pdf_envio.html"),
                ("clinica_manuales_recetas", None, "mobile_manual_admin_list.html", "manuales_recetas.html"),
                ("clinica_crear_manual_receta", None, "mobile_manual_form.html", "manual_receta_form.html"),
                ("clinica_editar_manual_receta", self.manuales[empresa.slug], "mobile_manual_form.html", "manual_receta_form.html"),
            ):
                with self.subTest(empresa=empresa.slug, route=route):
                    mobile = self.client.get(self.url(route, manual, empresa))
                    self.assertEqual(mobile.status_code, 200)
                    self.assertTemplateUsed(mobile, "clinica/"+mobile_template)
                    self.assertIn("no-store", mobile["Cache-Control"])
                    self.assertNotContains(mobile, "<table")
                    web = self.client.get(self.url(route, manual, empresa, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/"+web_template)
                    self.assertTemplateNotUsed(web, "clinica/"+mobile_template)
            composer = self.client.get(self.url("clinica_manuales_pdf_app_enviar", empresa=empresa))
            self.assertEqual(composer.status_code, 200)
            self.assertTemplateUsed(composer, "clinica/mobile_manual_send.html")
            self.assertIn("no-store", composer["Cache-Control"])
            self.assertEqual(self.client.get(self.url("clinica_enviar_manuales_pdf", empresa=empresa, mobile=False)).status_code, 405)

    def test_app_flag_keeps_other_clinic_web_and_new_composer_is_four_clinic_only(self):
        empresa = Empresa.objects.create(nombre="Otra clínica manuales", slug="otra_clinica_mobile_manuales", rtn="MM-OTRA", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, manuales_pdf_habilitados=True)
        self.usuario.empresas_acceso.add(empresa)
        legacy = RolSistema.objects.create(nombre="Manuales legado fuera", codigo="mobile-manuals-outside", puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=empresa, rol_sistema=legacy)
        response = self.client.get(self.url("clinica_manuales_pdf", empresa=empresa))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/manuales_pdf_envio.html")
        self.assertFalse(response.context.get("modo_app", False))
        self.assertEqual(self.client.get(self.url("clinica_manuales_pdf_app_enviar", empresa=empresa)).status_code, 404)

    def test_manual_reader_has_no_send_or_admin_actions_and_direct_routes_are_denied(self):
        self.asignar_rol(puede_ver_manuales_pdf=True)
        response = self.client.get(self.url("clinica_manuales_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_manuals"])
        self.assertFalse(response.context["can_send_manuals"])
        self.assertFalse(response.context["can_admin_manuals"])
        for route in ("clinica_crear_manual_receta", "clinica_manuales_recetas", "clinica_manuales_pdf_app_enviar"):
            self.assertNotContains(response, f'href="{self.url(route)}"')
        factory = RequestFactory()
        for view, route, manual, post in (
            (views.manuales_recetas, "clinica_manuales_recetas", None, False),
            (views.crear_manual_receta, "clinica_crear_manual_receta", None, False),
            (views.editar_manual_receta, "clinica_editar_manual_receta", self.manual, False),
            (views.enviar_manuales_pdf, "clinica_enviar_manuales_pdf", None, True),
            (views.manuales_pdf_app_enviar, "clinica_manuales_pdf_app_enviar", None, False),
        ):
            with self.subTest(route=route):
                request = factory.post(self.url(route, manual), self.datos()) if post else factory.get(self.url(route, manual))
                request.user = Usuario.objects.get(pk=self.usuario.pk)
                kwargs = {"empresa_slug": self.empresa.slug}
                if manual:
                    kwargs["manual_id"] = manual.pk
                with self.assertRaises(PermissionDenied):
                    view(request, **kwargs)
        self.assertFalse(EnvioManualPDF.objects.exists())

    def test_admin_originally_implies_view_but_does_not_grant_sending(self):
        rol = self.asignar_rol(puede_administrar_manuales_pdf=True)
        response = self.client.get(self.url("clinica_manuales_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_manuals"])
        self.assertTrue(response.context["can_admin_manuals"])
        self.assertFalse(response.context["can_send_manuals"])
        admin = self.client.get(self.url("clinica_manuales_recetas"))
        self.assertContains(admin, self.inactivo.titulo)
        self.assertEqual(self.client.get(self.url("clinica_crear_manual_receta")).status_code, 200)
        self.assertIn(self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos()).status_code, (302, 403))
        rol.refresh_from_db()
        self.assertFalse(rol.puede_enviar_manuales_pdf)
        self.assertFalse(rol.puede_ver_calendario)

    def test_sender_only_composer_exposes_no_pdf_history_or_unpermitted_patient_metadata(self):
        self.asignar_rol(puede_enviar_manuales_pdf=True)
        log = EnvioManualPDF.objects.create(empresa=self.empresa, paciente=self.paciente, destinatario=self.paciente.correo, canal="correo")
        log.manuales.add(self.manual)
        response = self.composer(manuales=[self.manual.pk], paciente_id=self.paciente.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_manuals"])
        self.assertTrue(response.context["can_send_manuals"])
        self.assertFalse(response.context["can_search_manual_patients"])
        self.assertContains(response, self.manual.titulo)
        self.assertNotContains(response, self.manual.descripcion)
        self.assertEqual(response.context["mobile_manual_items"][0]["manual"], {"id": self.manual.pk, "titulo": self.manual.titulo})
        self.assertEqual(response.context["mobile_manual_items"][0]["pdf_url"], "")
        self.assertNotIn("mobile_manual_history", response.context)
        self.assertNotContains(response, self.paciente.nombre)
        self.assertNotContains(response, self.paciente.correo)
        self.assertNotContains(response, self.paciente.telefono)
        self.assertNotContains(response, f'href="{self.url("clinica_archivo_manual_receta", self.manual)}"')
        self.assertNotContains(response, "Historial de envíos")
        self.assertIn(self.client.get(self.url("clinica_manuales_pdf")).status_code, (302, 403))
        request = RequestFactory().get(self.url("clinica_archivo_manual_receta", self.manual))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.archivo_manual_receta(request, empresa_slug=self.empresa.slug, manual_id=self.manual.pk)

    def test_existing_module_config_company_roles_and_direct_guard_prevent_fallback(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        request = RequestFactory().get(self.url("clinica_manuales_pdf", empresa=self.empresas["medical_spa"]))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.manuales_pdf(request, empresa_slug="medical_spa")
        request = RequestFactory().get(self.url("clinica_manuales_pdf"))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            views.manuales_pdf(request, empresa_slug=self.empresa.slug)
        self.assertIn(self.client.get(self.url("clinica_manuales_pdf")).status_code, (302, 404))
        self.assertIn(self.client.get(self.url("clinica_manuales_pdf", mobile=False)).status_code, (302, 404))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(manuales_pdf_habilitados=False)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(Http404):
            views.manuales_pdf(request, empresa_slug=self.empresa.slug)
        self.assertEqual(self.client.get(self.url("clinica_manuales_pdf")).status_code, 404)
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(manuales_pdf_habilitados=True)
        request.user = Usuario.objects.create_user(username="mobile-manuals-no-access", rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            views.manuales_pdf(request, empresa_slug=self.empresa.slug)

    def test_active_catalog_admin_and_file_ids_remain_company_scoped(self):
        before_audit = RegistroAuditoria.objects.count()
        response = self.client.get(self.url("clinica_manuales_pdf"))
        self.assertEqual([manual.pk for manual in response.context["manuales"]], [self.manual.pk])
        self.assertNotContains(response, self.inactivo.titulo)
        self.assertNotContains(response, self.manuales["medical_spa"].titulo)
        extranjero = self.manuales["medical_spa"]
        for route in ("clinica_editar_manual_receta", "clinica_archivo_manual_receta"):
            self.assertEqual(self.client.get(self.url(route, extranjero)).status_code, 404)
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as email, patch("clinica.views.subir_documento_whatsapp") as upload:
            for manual_ids in ([self.manual.pk, extranjero.pk], [self.inactivo.pk], ["no-entero"], [], [self.manual.pk, 2**63], [self.manual.pk, -(2**63)-1], [0]):
                response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(manuales=manual_ids))
                self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
                self.assertEqual(self.client.get(response.url).status_code, 200)
                self.assertFalse(EnvioManualPDF.objects.exists())
            email.assert_not_called()
            upload.assert_not_called()
        self.assertEqual(RegistroAuditoria.objects.count(), before_audit)

    def test_protected_files_reuse_original_bytes_download_mode_and_mobile_no_store(self):
        mobile = self.client.get(self.url("clinica_archivo_manual_receta", self.manual))
        web = self.client.get(self.url("clinica_archivo_manual_receta", self.manual, mobile=False))
        self.assertEqual(mobile.status_code, 200)
        self.assertEqual(web.status_code, 200)
        self.assertIn("no-store", mobile["Cache-Control"])
        self.assertTrue(mobile["Content-Disposition"].startswith("inline;"))
        self.assertTrue(web["Content-Disposition"].startswith("inline;"))
        self.assertEqual(self.cuerpo_archivo(mobile), self.cuerpo_archivo(web))
        download = self.client.get(self.url("clinica_archivo_manual_receta", self.manual), {"app": "1", "descargar": "1"})
        self.assertTrue(download["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(self.cuerpo_archivo(download), b"%PDF-1.4\nmanual protegido\n%%EOF")

    def test_create_uses_original_upload_form_and_file_validation_twenty_mb_boundary(self):
        mobile = self.client.get(self.url("clinica_crear_manual_receta"))
        web = self.client.get(self.url("clinica_crear_manual_receta", mobile=False))
        self.assertIsInstance(mobile.context["form"], ManualRecetaForm)
        self.assertEqual(set(mobile.context["form"].fields), set(web.context["form"].fields))
        self.assertContains(mobile, 'enctype="multipart/form-data"')
        self.assertContains(mobile, 'name="app" value="1"')
        for field in ("titulo", "descripcion", "archivo", "activo"):
            self.assertContains(mobile, f'name="{field}"')
        response = self.client.post(self.url("clinica_crear_manual_receta"), {
            "app": "1", "titulo": "Manual subido móvil", "descripcion": "Descripción guardada", "archivo": self.archivo(), "activo": "on",
        })
        self.assert_return_app(response, "clinica_manuales_recetas")
        manual = ManualReceta.objects.get(empresa=self.empresa, titulo="Manual subido móvil")
        self.assertEqual(manual.creado_por, self.usuario)
        self.assertEqual(Path(manual.archivo.path).read_bytes(), b"%PDF-1.4\nmanual protegido\n%%EOF")
        audit = RegistroAuditoria.objects.get(empresa=self.empresa, app_label="clinica", modelo="manualreceta", objeto_id=str(manual.pk), accion="crear")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertEqual(audit.metodo_http, "POST")
        valid = ManualRecetaForm({"titulo": "PDF límite", "activo": "on"}, {"archivo": self.archivo(contenido=b"%PDF-"+b"x"*(20*1024*1024-5))}, empresa=self.empresa)
        self.assertTrue(valid.is_valid(), valid.errors)
        invalid = ManualRecetaForm({"titulo": "PDF demasiado grande"}, {"archivo": self.archivo(contenido=b"%PDF-"+b"x"*(20*1024*1024-4))}, empresa=self.empresa)
        self.assertFalse(invalid.is_valid())
        self.assertIn("archivo", invalid.errors)

    def test_invalid_uploads_keep_form_values_and_create_no_partial_manual(self):
        before = ManualReceta.objects.count()
        for archivo in (
            self.archivo(nombre="manual.jpg"), self.archivo(contenido=b"no es un PDF"), self.archivo(content_type="image/png"),
        ):
            with self.subTest(nombre=archivo.name, tipo=archivo.content_type):
                response = self.client.post(self.url("clinica_crear_manual_receta"), {
                    "app": "1", "titulo": "Título conservado", "descripcion": "Texto conservado", "archivo": archivo, "activo": "on",
                })
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "clinica/mobile_manual_form.html")
                self.assertIn("archivo", response.context["form"].errors)
                self.assertEqual(response.context["form"]["titulo"].value(), "Título conservado")
                self.assertContains(response, "Texto conservado")
                self.assertEqual(ManualReceta.objects.count(), before)

    def test_edit_preserves_previous_file_without_upload_and_on_invalid_replacement(self):
        previous = self.manual.archivo.name
        response = self.client.post(self.url("clinica_editar_manual_receta", self.manual), {
            "app": "1", "titulo": "Manual editado sin reemplazo", "descripcion": "Texto actualizado", "activo": "on",
        })
        self.assert_return_app(response, "clinica_manuales_recetas")
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.archivo.name, previous)
        self.assertEqual(self.manual.descripcion, "Texto actualizado")
        invalid = self.client.post(self.url("clinica_editar_manual_receta", self.manual), {
            "app": "1", "titulo": "Trabajo no perdido", "descripcion": "Nota no perdida", "archivo": self.archivo(contenido=b"PDF invalido"), "activo": "on",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("archivo", invalid.context["form"].errors)
        self.assertContains(invalid, "Trabajo no perdido")
        self.assertContains(invalid, "Nota no perdida")
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.archivo.name, previous)
        self.assertEqual(self.manual.titulo, "Manual editado sin reemplazo")
        self.assertTrue(Path(self.manual.archivo.path).exists())

    def test_composer_recipient_read_requires_general_or_directory_permission_and_search_requires_directory(self):
        for can_read, permisos in ((False, {}), (True, {"puede_ver_datos_generales_paciente": True}), (True, {"puede_ver_pacientes": True})):
            with self.subTest(permisos=permisos):
                self.asignar_rol(puede_ver_manuales_pdf=True, puede_enviar_manuales_pdf=True, **permisos)
                response = self.composer(manuales=[self.manual.pk], paciente_id=self.paciente.pk)
                self.assertEqual(response.status_code, 200)
                if can_read:
                    self.assertContains(response, self.paciente.nombre)
                    self.assertContains(response, self.paciente.correo)
                else:
                    self.assertNotContains(response, self.paciente.nombre)
                    self.assertNotContains(response, self.paciente.correo)
                self.assertEqual(response.context["can_preselect_manual_patient"], can_read)
                self.assertEqual(response.context["can_search_manual_patients"], bool(permisos.get("puede_ver_pacientes")))
                suggestions = self.client.get(reverse("clinica_pacientes_sugerencias", args=[self.empresa.slug]), {"app": "1", "q": "Paciente"})
                if permisos.get("puede_ver_pacientes"):
                    self.assertEqual(suggestions.status_code, 200)
                    self.assertEqual({item["id"] for item in suggestions.json()["results"]}, {self.paciente.pk})
                else:
                    self.assertIn(suggestions.status_code, (302, 403))
        foreign = self.composer(manuales=[self.manual.pk], paciente_id=self.pacientes["medical_spa"].pk)
        self.assertIn(foreign.status_code, (200, 404))
        self.assertNotIn(self.pacientes["medical_spa"].nombre.encode(), foreign.content)
        self.assertFalse(EnvioManualPDF.objects.exists())

    def test_malformed_patient_ids_invalid_channels_and_missing_contacts_do_not_send_or_500(self):
        self.paciente.correo = ""
        self.paciente.save(update_fields=["correo"])
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as email, patch("clinica.views.subir_documento_whatsapp") as upload, \
             patch("clinica.views.enviar_documento_whatsapp") as send:
            for overrides in ({"paciente_id": "no-entero"}, {"paciente_id": 2**63}, {"paciente_id": -(2**63)-1}, {"paciente_id": self.pacientes["medical_spa"].pk}, {"canal": "canal-inválido"}, {}):
                with self.subTest(overrides=overrides):
                    response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(**overrides))
                    self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
                    self.assertEqual(self.client.get(response.url).status_code, 200)
            for invalid_id in ("no-entero", str(2**63), str(-(2**63)-1)):
                malformed_get = self.composer(manuales=[self.manual.pk, invalid_id], paciente_id=invalid_id)
                self.assertEqual(malformed_get.status_code, 200)
                self.assertIsNone(malformed_get.context["mobile_manual_patient"])
            self.assertFalse(EnvioManualPDF.objects.exists())
            email.assert_not_called()
            upload.assert_not_called()
            send.assert_not_called()

    def test_email_uses_existing_attachments_and_records_user_channel_status_and_manuals(self):
        second = self.crear_manual(titulo="Segundo manual clínico")
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True, return_value=1) as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(manuales=[self.manual.pk, second.pk]))
        self.assert_return_app(response)
        send.assert_called_once()
        message = send.call_args.args[0]
        self.assertEqual(message.to, [self.paciente.correo])
        self.assertEqual(len(message.attachments), 2)
        self.assertTrue(all(attachment[1:] == (b"%PDF-1.4\nmanual protegido\n%%EOF", "application/pdf") for attachment in message.attachments))
        envio = EnvioManualPDF.objects.get()
        self.assertEqual((envio.empresa_id, envio.paciente_id, envio.enviado_por_id, envio.canal, envio.estado, envio.destinatario),
                         (self.empresa.pk, self.paciente.pk, self.usuario.pk, "correo", "enviado", self.paciente.correo))
        self.assertEqual(set(envio.manuales.values_list("pk", flat=True)), {self.manual.pk, second.pk})
        self.assertEqual(len(mail.outbox), 0)

    def test_whatsapp_original_transport_uses_company_config_number_and_document_bytes(self):
        config = ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        ConfiguracionCRM.objects.create(empresa=self.empresas["medical_spa"], whatsapp_activo=True)
        second = self.crear_manual(titulo="Segundo manual clínico")
        documents = []
        def upload_document(configuration, path, mime):
            documents.append((configuration.pk, Path(path).read_bytes(), mime))
            return f"media-{len(documents)}"
        with patch("clinica.views.subir_documento_whatsapp", side_effect=upload_document), patch("clinica.views.enviar_documento_whatsapp") as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(manuales=[self.manual.pk, second.pk], canal="whatsapp"))
        self.assert_return_app(response)
        self.assertEqual(documents, [(config.pk, b"%PDF-1.4\nmanual protegido\n%%EOF", "application/pdf")]*2)
        self.assertEqual(send.call_count, 2)
        self.assertTrue(all(call.args[0].pk == config.pk and call.args[1] == "50499994001" for call in send.call_args_list))
        envio = EnvioManualPDF.objects.get()
        self.assertEqual(envio.estado, "enviado")
        self.assertEqual(envio.enviado_por, self.usuario)

    def test_partial_and_failed_api_email_states_reuse_existing_history_rules(self):
        second = self.crear_manual(titulo="Segundo manual clínico")
        ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        with patch("clinica.views.subir_documento_whatsapp", side_effect=["media-uno", WhatsAppAPIError("Error simulado API")]), \
             patch("clinica.views.enviar_documento_whatsapp") as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(manuales=[self.manual.pk, second.pk], canal="whatsapp"))
        self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
        self.assertEqual(parse_qs(urlsplit(response.url).query)["manuales"], [str(second.pk)])
        self.assertEqual(send.call_count, 1)
        parcial = EnvioManualPDF.objects.get()
        self.assertEqual(parcial.estado, "parcial")
        self.assertIn("Error simulado API", parcial.detalle_error)
        self.assertEqual(parcial.manuales.count(), 2)
        retry = self.client.get(response.url)
        retry_sheet = re.search(r'<dialog\b[^>]*id="manualSendSheet".*?</dialog>', retry.content.decode(), re.S).group(0)
        self.assertIn("data-manual-composer-messages", retry_sheet)
        self.assertIn("Se enviaron 1 de 2 manuales", retry_sheet)
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True, side_effect=RuntimeError("Correo simulado no disponible")):
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos())
        self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
        self.assertEqual(parse_qs(urlsplit(response.url).query)["manuales"], [str(self.manual.pk)])
        fallido = EnvioManualPDF.objects.latest("pk")
        self.assertEqual(fallido.estado, "fallido")
        self.assertEqual(fallido.enviado_por, self.usuario)
        self.assertIn("Correo simulado no disponible", fallido.detalle_error)
        log = self.client.get(self.url("clinica_manuales_pdf"))
        self.assertContains(log, "Envío parcial")
        self.assertContains(log, "Fallido")

    def test_missing_current_company_api_config_never_uses_other_company_and_logs_failure(self):
        ConfiguracionCRM.objects.create(empresa=self.empresas["medical_spa"], whatsapp_activo=True)
        with patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(canal="whatsapp"))
        self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
        upload.assert_not_called()
        send.assert_not_called()
        envio = EnvioManualPDF.objects.get()
        self.assertEqual(envio.estado, "fallido")
        self.assertIn("no está activo", envio.detalle_error)

    def test_manual_whatsapp_links_keep_original_token_security_and_prepared_history(self):
        with patch("clinica.views.subir_documento_whatsapp") as upload, patch("clinica.views.enviar_documento_whatsapp") as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(canal="whatsapp_manual"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://wa.me/50499994001?text="))
        link = re.search(r"https?://[^\s]+/formularios/clinica/manuales/[^\s]+/descargar/", unquote(response.url)).group(0)
        public_path = urlsplit(link).path
        token = public_path.split("/")[-3]
        self.client.logout()
        download = self.client.get(public_path)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(self.cuerpo_archivo(download), b"%PDF-1.4\nmanual protegido\n%%EOF")
        tampered = token[:-1]+("a" if token[-1] != "a" else "b")
        self.assertEqual(self.client.get(reverse("clinica_descargar_manual_pdf_publico", args=[tampered])).status_code, 404)
        with override_settings(MANUAL_PDF_LINK_MAX_AGE=-1):
            self.assertEqual(self.client.get(public_path).status_code, 404)
        self.manual.activo = False
        self.manual.save(update_fields=["activo"])
        self.assertEqual(self.client.get(public_path).status_code, 404)
        upload.assert_not_called()
        send.assert_not_called()
        envio = EnvioManualPDF.objects.get()
        self.assertEqual((envio.canal, envio.estado, envio.enviado_por_id), ("whatsapp_manual", "preparado", self.usuario.pk))

    def test_legacy_manual_read_union_and_distinct_configuration_and_expediente_permissions(self):
        for flag, can_admin, can_send in (("puede_pacientes", False, False), ("puede_configuracion_clinica", True, False), ("puede_expediente_clinico", False, True)):
            with self.subTest(flag=flag):
                rol = RolSistema.objects.create(nombre=f"Manuales legado {flag}", codigo=f"mobile-manuals-legacy-{flag}", **{flag: True})
                UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=self.empresa, defaults={"rol_sistema": rol})
                response = self.client.get(self.url("clinica_manuales_pdf"))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["can_view_manuals"])
                self.assertEqual(response.context["can_admin_manuals"], can_admin)
                self.assertEqual(response.context["can_send_manuals"], can_send)
                if can_admin:
                    hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
                    self.assertEqual(hub.status_code, 200)
                    entry = next(item for item in hub.context["modulos_app"] if item["key"] == "manuales")
                    self.assertEqual((entry["estado"], entry["url"]), ("disponible", self.url("clinica_manuales_pdf")))
                    destination = self.client.get(entry["url"])
                    self.assertEqual(destination.status_code, 200)
                    self.assertTemplateUsed(destination, "clinica/mobile_manual_list.html")
                    self.assertFalse(destination.context["can_send_manuals"])
                    rol.refresh_from_db()
                    self.assertFalse(rol.puede_enviar_manuales_pdf)
                    self.assertFalse(rol.puede_ver_manuales_pdf)
        self.assertFalse(EnvioManualPDF.objects.exists())

    def test_mobile_channels_no_calendar_navigation_and_passive_reads_preserve_records(self):
        before = (ManualReceta.objects.count(), EnvioManualPDF.objects.count(), Paciente.objects.count(), RegistroAuditoria.objects.count())
        composer = self.composer(manuales=[self.manual.pk], paciente_id=self.paciente.pk)
        self.assertEqual(composer.status_code, 200)
        self.assertContains(composer, 'name="manuales"')
        self.assertContains(composer, 'name="paciente_id"')
        self.assertContains(composer, 'type="hidden" name="canal"')
        self.assertContains(composer, 'name="app" value="1"')
        self.assertFalse(composer.context["app_home_disponible"])
        self.assertNotContains(composer, 'rel="manifest"')
        self.assertContains(composer, f'href="{reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])}"')
        self.assertNotContains(composer, f'href="{reverse("agenda_mobile", args=[self.empresa.slug])}#inicio"')
        for route, manual in (("clinica_manuales_pdf", None), ("clinica_manuales_recetas", None), ("clinica_crear_manual_receta", None), ("clinica_editar_manual_receta", self.manual)):
            self.assertEqual(self.client.get(self.url(route, manual)).status_code, 200)
        self.assertEqual((ManualReceta.objects.count(), EnvioManualPDF.objects.count(), Paciente.objects.count(), RegistroAuditoria.objects.count()), before)
        self.rol.refresh_from_db()
        self.assertFalse(self.rol.puede_ver_calendario)
        self.assertFalse(self.rol.puede_enviar_recetas)

    def test_hidden_app_post_preserves_sender_only_return_without_granting_manual_or_patient_read(self):
        rol = self.asignar_rol(puede_enviar_manuales_pdf=True)
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True, return_value=1) as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf", mobile=False), self.datos())
        self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
        send.assert_called_once()
        self.assertEqual(EnvioManualPDF.objects.get().estado, "enviado")
        composer = self.client.get(response.url)
        self.assertEqual(composer.status_code, 200)
        # El servicio original confirma el destinatario por messages; esto no
        # convierte al compositor en una consulta de datos/contactos del paciente.
        self.assertNotContains(composer, self.paciente.correo)
        self.assertNotContains(composer, self.paciente.telefono)
        self.assertNotContains(composer, self.manual.descripcion)
        self.assertFalse(composer.context["can_view_manuals"])
        self.assertIsNone(composer.context["mobile_manual_patient"])
        passive = self.client.get(response.url)
        self.assertNotContains(passive, self.paciente.nombre)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_ver_manuales_pdf)
        self.assertFalse(rol.puede_ver_pacientes)
        self.assertFalse(rol.puede_ver_datos_generales_paciente)

    def test_failed_send_preserves_valid_selected_ids_patient_channel_and_escaped_values(self):
        second = self.crear_manual(titulo="Manual segundo para reintentar")
        self.paciente.correo = ""
        self.paciente.save(update_fields=["correo"])
        with patch("clinica.views.EmailMultiAlternatives.send", autospec=True) as send:
            response = self.client.post(self.url("clinica_enviar_manuales_pdf"), self.datos(manuales=[self.manual.pk, second.pk]))
        self.assert_return_app(response, "clinica_manuales_pdf_app_enviar")
        params = parse_qs(urlsplit(response.url).query)
        self.assertEqual(params["manuales"], [str(self.manual.pk), str(second.pk)])
        self.assertEqual(params["paciente_id"], [str(self.paciente.pk)])
        self.assertEqual(params["canal"], ["correo"])
        self.assertNotIn(self.paciente.nombre, response.url)
        composer = self.client.get(response.url)
        self.assertEqual(composer.status_code, 200)
        self.assertEqual({row["manual"].pk for row in composer.context["mobile_manual_items"] if row["selected"]}, {self.manual.pk, second.pk})
        self.assertEqual(composer.context["mobile_manual_patient"]["id"], self.paciente.pk)
        self.assertEqual(composer.context["mobile_manual_canal"], "correo")
        error_sheet = re.search(r'<dialog\b[^>]*id="manualSendSheet".*?</dialog>', composer.content.decode(), re.S).group(0)
        self.assertIn("data-manual-composer-messages", error_sheet)
        self.assertIn("no tiene correo electrónico registrado", error_sheet)
        send.assert_not_called()
        self.assertFalse(EnvioManualPDF.objects.exists())

    def test_history_is_tenant_scoped_and_handles_deleted_actor_without_exposing_foreign_manuals(self):
        own = EnvioManualPDF.objects.create(empresa=self.empresa, paciente=self.paciente, enviado_por=None, destinatario=self.paciente.correo, canal="correo")
        own.manuales.add(self.manual)
        foreign = EnvioManualPDF.objects.create(empresa=self.empresas["medical_spa"], paciente=self.pacientes["medical_spa"], enviado_por=self.usuario, destinatario="solo-otra-empresa@example.com", canal="correo")
        foreign.manuales.add(self.manuales["medical_spa"])
        response = self.client.get(self.url("clinica_manuales_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context["historial"]], [own.pk])
        self.assertEqual(response.context["mobile_manual_history"][0]["usuario"], "Usuario no disponible")
        self.assertContains(response, "Usuario no disponible")
        self.assertNotContains(response, foreign.destinatario)
        self.assertNotContains(response, self.manuales["medical_spa"].titulo)

    def test_additive_hub_manual_entry_respects_permissions_feature_and_module_without_recipe_or_calendar_grants(self):
        hub_url = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for permisos, route in (
            ({"puede_ver_manuales_pdf": True}, "clinica_manuales_pdf"),
            ({"puede_enviar_manuales_pdf": True}, "clinica_manuales_pdf_app_enviar"),
            ({"puede_administrar_manuales_pdf": True}, "clinica_manuales_pdf"),
        ):
            with self.subTest(permisos=permisos):
                rol = self.asignar_rol(**permisos)
                hub = self.client.get(hub_url)
                self.assertEqual(hub.status_code, 200)
                entry = next(item for item in hub.context["modulos_app"] if item["key"] == "manuales")
                self.assertEqual((entry["estado"], entry["url"]), ("disponible", self.url(route)))
                self.assertEqual(self.client.get(entry["url"]).status_code, 200)
                rol.refresh_from_db()
                self.assertFalse(rol.puede_ver_calendario)
                self.assertFalse(rol.puede_enviar_recetas)
                self.assertFalse(rol.puede_ver_recetas)
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(manuales_pdf_habilitados=False)
        disabled = self.client.get(hub_url)
        self.assertEqual(disabled.status_code, 200)
        self.assertNotIn("manuales", {item["key"] for item in disabled.context["modulos_app"]})
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).update(manuales_pdf_habilitados=True)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        module_disabled = self.client.get(hub_url)
        self.assertEqual(module_disabled.status_code, 200)
        self.assertNotIn("manuales", {item["key"] for item in module_disabled.context["modulos_app"]})

    def test_mobile_composer_is_get_only_original_sender_stays_post_only_and_web_whatsapp_link_unchanged(self):
        self.assertEqual(self.client.post(self.url("clinica_manuales_pdf_app_enviar"), self.datos()).status_code, 405)
        self.assertEqual(self.client.get(self.url("clinica_enviar_manuales_pdf")).status_code, 405)
        self.assertEqual(self.client.get(self.url("clinica_manuales_pdf_app_enviar", mobile=False)).status_code, 404)
        data = self.datos(canal="whatsapp_manual")
        del data["app"]
        response = self.client.post(self.url("clinica_enviar_manuales_pdf", mobile=False), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://web.whatsapp.com/send?phone=50499994001"))
        self.assertEqual(EnvioManualPDF.objects.get().estado, "preparado")
