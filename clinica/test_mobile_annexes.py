from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from .forms import DocumentoClinicoPacienteForm, ExamenPacienteForm, IncapacidadClinicaForm, PacienteFotoEvolucionForm, PlanConsentimientoPDFForm
from .models import ConsentimientoClinico, DocumentoClinicoPaciente, ExamenPaciente, Paciente, PacienteFotoEvolucion, ProfesionalSalud
from . import views


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAnnexesIntegrationTests(TestCase):
    PDF = b"%PDF-1.4\nanexo clinico protegido\n%%EOF"
    VIDEO = b"video-test-existing-validation"
    CATEGORIAS = tuple(value for value, _ in DocumentoClinicoPaciente.CATEGORIA_CHOICES)

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales = {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Anexos clínica {slug}", slug=slug, rtn=f"MA-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=cls.modulo, defaults={"activo": True})
            cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente anexo {slug}", expediente_codigo=f"MA-{indice}", identidad=f"080119996500{indice}")
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional anexo {slug}", especialidad="Medicina")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes["hospital_mia"]
        cls.otro_paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Otro paciente anexos", expediente_codigo="MA-OTRO")
        cls.rol = RolSistema.objects.create(
            nombre="Anexos móvil completo", codigo="mobile-annexes-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True, puede_ver_anexos_clinicos=True, puede_subir_anexos_clinicos=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-annexes-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        for transport in ("EmailMultiAlternatives.send", "subir_documento_whatsapp", "enviar_documento_whatsapp"):
            patcher = patch("clinica.views."+transport)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mocked.assert_not_called)
        media = TemporaryDirectory(prefix="dv-mobile-annexes-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.examen = ExamenPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, titulo="Hemograma propio", archivo=self.pdf(), subido_por=self.usuario)
        self.foto = PacienteFotoEvolucion.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="control", titulo="Foto control propia", imagen=self.imagen(), creado_por=self.usuario)
        self.video = PacienteFotoEvolucion.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="evolucion", titulo="Video control propio", video=self.video_file(), creado_por=self.usuario)
        self.consentimiento = ConsentimientoClinico.objects.create(empresa=self.empresa, paciente=self.paciente, titulo="Consentimiento propio", contenido="Plan firmado", estado="firmado", archivo=self.pdf())
        self.documentos = {
            categoria: DocumentoClinicoPaciente.objects.create(
                empresa=self.empresa, paciente=self.paciente, categoria=categoria, titulo=f"Documento propio {categoria}",
                descripcion="Nota del documento", archivo=self.pdf(), creado_por=self.usuario,
                profesional=self.profesionales["hospital_mia"] if categoria == "incapacidad" else None,
                fecha_inicio="2026-10-10" if categoria == "incapacidad" else None,
                fecha_fin="2026-10-12" if categoria == "incapacidad" else None, dias=3 if categoria == "incapacidad" else None,
            ) for categoria in self.CATEGORIAS
        }
        extranjera, paciente_extranjero = self.empresas["medical_spa"], self.pacientes["medical_spa"]
        self.examen_extranjero = ExamenPaciente.objects.create(empresa=extranjera, paciente=paciente_extranjero, titulo="Examen otra empresa secreto", archivo=self.pdf())
        self.documento_extranjero = DocumentoClinicoPaciente.objects.create(empresa=extranjera, paciente=paciente_extranjero, categoria="documento", titulo="Documento otra empresa secreto", archivo=self.pdf())

    def pdf(self, nombre="anexo.pdf", mime="application/pdf"):
        return SimpleUploadedFile(nombre, self.PDF, content_type=mime)

    def imagen(self, formato="PNG", mime=None):
        buffer = BytesIO()
        Image.new("RGB", (24, 24), color=(24, 130, 160)).save(buffer, format=formato)
        extension = "jpg" if formato == "JPEG" else formato.lower()
        return SimpleUploadedFile("evolucion."+extension, buffer.getvalue(), content_type=mime or {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}[formato])

    def video_file(self, mime="video/mp4"):
        return SimpleUploadedFile("evolucion.mp4", self.VIDEO, content_type=mime)

    def url(self, route, *, empresa=None, paciente=None, categoria=None, documento=None, mobile=True):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug, "paciente_id": (paciente or self.paciente).pk}
        if categoria is not None:
            kwargs["categoria"] = categoria
        if documento is not None:
            kwargs["documento_id"] = documento.pk
        return reverse(route, kwargs=kwargs) + ("?app=1" if mobile else "")

    def file_url(self, objeto=None, clase="examen", parte="archivo", *, empresa=None, paciente=None, mobile=True, anexo_id=None):
        return reverse("clinica_archivo_anexo_app", kwargs={
            "empresa_slug": (empresa or self.empresa).slug, "paciente_id": (paciente or self.paciente).pk,
            "clase": clase, "registro_id": anexo_id if anexo_id is not None else (objeto or self.examen).pk, "parte": parte,
        }) + ("?app=1" if mobile else "")

    def asignar_rol(self, empresa=None, **permisos):
        rol = RolSistema.objects.create(nombre="Anexos móvil restringido", codigo=f"mobile-annexes-role-{RolSistema.objects.count()}", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **permisos)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def conteos(self):
        return tuple(model.objects.count() for model in (ExamenPaciente, PacienteFotoEvolucion, ConsentimientoClinico, DocumentoClinicoPaciente, Paciente, RegistroAuditoria))

    def cuerpo(self, response):
        try:
            return b"".join(response.streaming_content) if response.streaming else response.content
        finally:
            response.close()

    def datos_examen(self, **overrides):
        data = {"app": "1", "titulo": "Hemograma móvil", "tipo": "laboratorio", "fecha_examen": "2026-10-10", "laboratorio": "Lab de prueba", "descripcion": "Resultado conservado", "archivo": self.pdf()}
        data.update(overrides)
        return data

    def datos_evolucion(self, **overrides):
        data = {"app": "1", "titulo": "Evolución móvil", "tipo": "control", "fecha": "2026-10-10T09:30", "descripcion": "Control conservado", "imagen": self.imagen()}
        data.update(overrides)
        return data

    def datos_consentimiento(self, **overrides):
        data = {"app": "1", "titulo": "Plan firmado móvil", "version": "2026-10", "firmado_por": "Paciente firmante", "fecha_firma": "2026-10-10T09:30", "estado": "firmado", "archivo": self.pdf()}
        data.update(overrides)
        return data

    def datos_documento(self, categoria="documento", **overrides):
        data = {"app": "1", "titulo": "Documento móvil", "fecha_documento": "2026-10-10", "entidad": "Centro de prueba", "descripcion": "Nota conservada", "archivo": self.pdf()}
        if categoria == "incapacidad":
            data.update(fecha_inicio="2026-10-10", fecha_fin="2026-10-12", dias="", profesional=self.profesionales["hospital_mia"].pk)
        data.update(overrides)
        return data

    def assert_app_return(self, response, route, **kwargs):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, urlsplit(self.url(route, **kwargs)).path)
        self.assertEqual(parse_qs(urlsplit(response.url).query).get("app"), ["1"])

    def assert_audit(self, objeto):
        audit = RegistroAuditoria.objects.get(empresa=objeto.empresa, app_label="clinica", modelo=objeto._meta.model_name, objeto_id=str(objeto.pk), accion="crear")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertEqual(audit.metodo_http, "POST")

    def test_four_clinics_render_annex_lists_and_original_web_templates_remain_separate(self):
        for empresa in self.empresas.values():
            paciente = self.pacientes[empresa.slug]
            for route, categoria, web_template in (
                ("clinica_examenes_paciente", None, "examenes_paciente.html"),
                ("clinica_evolucion_paciente", None, "evolucion_paciente.html"),
                ("clinica_consentimientos_paciente", None, "consentimientos_paciente.html"),
                *(("clinica_documentos_categoria_paciente", categoria, "documentos_clinicos_paciente.html") for categoria in self.CATEGORIAS),
            ):
                with self.subTest(empresa=empresa.slug, route=route, categoria=categoria):
                    response = self.client.get(self.url(route, empresa=empresa, paciente=paciente, categoria=categoria))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["modo_app"])
                    self.assertTemplateUsed(response, "clinica/mobile_annex_list.html")
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotContains(response, "<table")
                    self.assertTemplateNotUsed(response, "clinica/"+web_template)
                    web = self.client.get(self.url(route, empresa=empresa, paciente=paciente, categoria=categoria, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/"+web_template)

    def test_mobile_uploads_reuse_exact_forms_multipart_and_fields_from_web(self):
        for route, categoria, form_class in (
            ("clinica_subir_examen_paciente", None, ExamenPacienteForm),
            ("clinica_registrar_foto_evolucion", None, PacienteFotoEvolucionForm),
            ("clinica_subir_consentimiento_paciente", None, PlanConsentimientoPDFForm),
            *(("clinica_subir_documento_categoria_paciente", categoria, IncapacidadClinicaForm if categoria == "incapacidad" else DocumentoClinicoPacienteForm) for categoria in self.CATEGORIAS),
        ):
            with self.subTest(route=route, categoria=categoria):
                response = self.client.get(self.url(route, categoria=categoria))
                web = self.client.get(self.url(route, categoria=categoria, mobile=False))
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.context["form"], form_class)
                self.assertTemplateUsed(response, "clinica/mobile_annex_form.html")
                self.assertEqual(set(response.context["form"].fields), set(web.context["form"].fields))
                self.assertContains(response, 'enctype="multipart/form-data"')
                self.assertContains(response, 'name="app" value="1"')
                for name in response.context["form"].fields:
                    self.assertContains(response, f'name="{name}"')
                self.assertTemplateUsed(web, "clinica/form.html")
                self.assertTemplateNotUsed(response, "clinica/form.html")

    def test_examination_upload_in_each_clinic_preserves_canonical_save_and_audit(self):
        for empresa in self.empresas.values():
            paciente = self.pacientes[empresa.slug]
            response = self.client.post(self.url("clinica_subir_examen_paciente", empresa=empresa, paciente=paciente), self.datos_examen(titulo="Hemograma móvil "+empresa.slug))
            self.assert_app_return(response, "clinica_examenes_paciente", empresa=empresa, paciente=paciente)
            examen = ExamenPaciente.objects.get(empresa=empresa, paciente=paciente, titulo="Hemograma móvil "+empresa.slug)
            self.assertEqual(examen.subido_por, self.usuario)
            self.assertEqual(examen.laboratorio, "Lab de prueba")
            self.assertEqual(examen.fecha_examen.isoformat(), "2026-10-10")
            self.assertEqual(Path(examen.archivo.path).read_bytes(), self.PDF)
            self.assert_audit(examen)

    def test_evolution_photo_and_video_use_original_independent_uploads_and_actor(self):
        response = self.client.post(self.url("clinica_registrar_foto_evolucion"), self.datos_evolucion())
        self.assert_app_return(response, "clinica_evolucion_paciente")
        foto = PacienteFotoEvolucion.objects.get(titulo="Evolución móvil")
        self.assertTrue(foto.imagen)
        self.assertFalse(foto.video)
        self.assertEqual(foto.creado_por, self.usuario)
        with Image.open(foto.imagen.path) as actual:
            self.assertEqual(actual.size, (24, 24))
        self.assert_audit(foto)
        data = self.datos_evolucion(titulo="Video móvil", video=self.video_file())
        del data["imagen"]
        response = self.client.post(self.url("clinica_registrar_foto_evolucion"), data)
        self.assert_app_return(response, "clinica_evolucion_paciente")
        video = PacienteFotoEvolucion.objects.get(titulo="Video móvil")
        self.assertTrue(video.video)
        self.assertFalse(video.imagen)
        self.assertEqual(video.creado_por, self.usuario)
        self.assertEqual(Path(video.video.path).read_bytes(), self.VIDEO)
        self.assert_audit(video)

    def test_consent_upload_uses_existing_content_default_and_signature_data_audit(self):
        response = self.client.post(self.url("clinica_subir_consentimiento_paciente"), self.datos_consentimiento())
        self.assert_app_return(response, "clinica_consentimientos_paciente")
        consent = ConsentimientoClinico.objects.get(titulo="Plan firmado móvil")
        self.assertEqual((consent.empresa_id, consent.paciente_id, consent.estado, consent.version, consent.firmado_por), (self.empresa.pk, self.paciente.pk, "firmado", "2026-10", "Paciente firmante"))
        self.assertEqual(consent.contenido, "Plan de consentimiento firmado cargado en PDF.")
        self.assertIsNotNone(consent.fecha_firma)
        self.assertEqual(Path(consent.archivo.path).read_bytes(), self.PDF)
        self.assert_audit(consent)

    def test_all_document_categories_save_existing_model_without_duplicate_logic(self):
        for categoria in self.CATEGORIAS:
            data = self.datos_documento(categoria, titulo="Guardado móvil "+categoria)
            if categoria == "incapacidad":
                del data["archivo"]
            response = self.client.post(self.url("clinica_subir_documento_categoria_paciente", categoria=categoria), data)
            self.assert_app_return(response, "clinica_documentos_categoria_paciente", categoria=categoria)
            documento = DocumentoClinicoPaciente.objects.get(titulo="Guardado móvil "+categoria)
            self.assertEqual((documento.empresa_id, documento.paciente_id, documento.categoria, documento.creado_por_id), (self.empresa.pk, self.paciente.pk, categoria, self.usuario.pk))
            self.assertEqual(documento.descripcion, "Nota conservada")
            if categoria == "incapacidad":
                self.assertEqual(documento.dias, 3)
                self.assertEqual(documento.profesional_id, self.profesionales["hospital_mia"].pk)
                self.assertFalse(documento.archivo)
                self.assertEqual(self.client.get(self.file_url(documento, "documento")).status_code, 404)
            else:
                self.assertEqual(Path(documento.archivo.path).read_bytes(), self.PDF)
            self.assert_audit(documento)

    def test_invalid_uploads_keep_current_form_values_and_write_no_annex_or_audit(self):
        before = self.conteos()
        for route, categoria, data, field in (
            ("clinica_subir_examen_paciente", None, self.datos_examen(archivo=self.pdf(mime="text/html")), "archivo"),
            ("clinica_subir_consentimiento_paciente", None, self.datos_consentimiento(archivo=self.pdf(mime="image/png")), "archivo"),
            ("clinica_registrar_foto_evolucion", None, self.datos_evolucion(imagen=self.imagen(formato="GIF")), "imagen"),
            ("clinica_registrar_foto_evolucion", None, self.datos_evolucion(imagen=SimpleUploadedFile("rota.png", b"invalid image", content_type="image/png")), "imagen"),
            ("clinica_subir_documento_categoria_paciente", "documento", self.datos_documento(archivo=self.pdf(nombre="archivo.exe")), "archivo"),
        ):
            with self.subTest(route=route, field=field):
                response = self.client.post(self.url(route, categoria=categoria), data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assertEqual(response.context["form"]["titulo"].value(), data["titulo"])
                self.assertContains(response, data["titulo"])
                self.assertEqual(self.conteos(), before)

    def test_evolution_requires_exactly_one_file_and_keeps_notes_and_date_on_error(self):
        before = self.conteos()
        no_file = self.datos_evolucion()
        del no_file["imagen"]
        both = self.datos_evolucion(video=self.video_file())
        bad_video = self.datos_evolucion(video=self.video_file(mime="text/html"))
        del bad_video["imagen"]
        for data in (no_file, both, bad_video):
            response = self.client.post(self.url("clinica_registrar_foto_evolucion"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertContains(response, "Control conservado")
            self.assertEqual(response.context["form"]["fecha"].value(), "2026-10-10T09:30")
            self.assertEqual(self.conteos(), before)

    def test_original_upload_size_boundaries_are_preserved_without_allocating_large_payloads(self):
        cases = (
            (ExamenPacienteForm, self.datos_examen, "archivo", self.pdf, 50),
            (PlanConsentimientoPDFForm, self.datos_consentimiento, "archivo", self.pdf, 15),
            (DocumentoClinicoPacienteForm, self.datos_documento, "archivo", self.pdf, 50),
            (PacienteFotoEvolucionForm, self.datos_evolucion, "imagen", self.imagen, 10),
            (PacienteFotoEvolucionForm, self.datos_evolucion, "video", self.video_file, 120),
        )
        for form_class, factory, field, file_factory, megabytes in cases:
            for extra, expected in ((0, True), (1, False)):
                with self.subTest(form=form_class.__name__, field=field, extra=extra):
                    data = factory()
                    files = {field: file_factory()}
                    files[field].size = megabytes*1024*1024+extra
                    for name in ("archivo", "imagen", "video"):
                        data.pop(name, None)
                    form = form_class(data, files, empresa=self.empresa)
                    self.assertEqual(form.is_valid(), expected, form.errors)
                    if not expected:
                        self.assertIn(field, form.errors)

    def test_incapacity_original_date_and_professional_validation_preserves_work(self):
        inactive = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Profesional inactivo anexos", activo=False)
        before = self.conteos()
        for overrides, error_field in (
            ({"fecha_fin": "2026-10-09"}, "__all__"),
            ({"profesional": self.profesionales["medical_spa"].pk}, "profesional"),
            ({"profesional": inactive.pk}, "profesional"),
        ):
            data = self.datos_documento("incapacidad", **overrides)
            response = self.client.post(self.url("clinica_subir_documento_categoria_paciente", categoria="incapacidad"), data)
            self.assertEqual(response.status_code, 200)
            self.assertIn(error_field, response.context["form"].errors)
            self.assertContains(response, "Nota conservada")
            self.assertEqual(response.context["form"]["fecha_inicio"].value(), "2026-10-10")
            self.assertEqual(self.conteos(), before)

    def test_reader_cannot_upload_and_upload_forms_are_backend_denied_independently(self):
        self.asignar_rol(puede_ver_anexos_clinicos=True)
        before = self.conteos()
        response = self.client.get(self.url("clinica_examenes_paciente"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_annexes"])
        for route, categoria, view, data in (
            ("clinica_subir_examen_paciente", None, views.subir_examen_paciente, self.datos_examen()),
            ("clinica_registrar_foto_evolucion", None, views.registrar_foto_evolucion, self.datos_evolucion()),
            ("clinica_subir_consentimiento_paciente", None, views.subir_consentimiento_paciente, self.datos_consentimiento()),
            ("clinica_subir_documento_categoria_paciente", "documento", views.subir_documento_clinico_paciente, self.datos_documento()),
        ):
            self.assertNotContains(response, f'href="{self.url(route, categoria=categoria)}"')
            self.assertIn(self.client.post(self.url(route, categoria=categoria), data).status_code, (302, 403))
            request = RequestFactory().get(self.url(route, categoria=categoria))
            request.user = Usuario.objects.get(pk=self.usuario.pk)
            kwargs = {"empresa_slug": self.empresa.slug, "paciente_id": self.paciente.pk}
            if categoria:
                kwargs["categoria"] = categoria
            with self.assertRaises(PermissionDenied):
                view(request, **kwargs)
        self.assertEqual(self.conteos(), before)

    def test_upload_only_user_cannot_read_previous_lists_or_protected_files(self):
        rol = self.asignar_rol(puede_subir_anexos_clinicos=True)
        before = self.conteos()
        for route, categoria, view in (
            ("clinica_examenes_paciente", None, views.examenes_paciente),
            ("clinica_evolucion_paciente", None, views.evolucion_paciente),
            ("clinica_consentimientos_paciente", None, views.consentimientos_paciente),
            ("clinica_documentos_categoria_paciente", "documento", views.documentos_clinicos_paciente),
        ):
            self.assertIn(self.client.get(self.url(route, categoria=categoria)).status_code, (302, 403))
            request = RequestFactory().get(self.url(route, categoria=categoria))
            request.user = Usuario.objects.get(pk=self.usuario.pk)
            kwargs = {"empresa_slug": self.empresa.slug, "paciente_id": self.paciente.pk}
            if categoria:
                kwargs["categoria"] = categoria
            with self.assertRaises(PermissionDenied):
                view(request, **kwargs)
        self.assertIn(self.client.get(self.file_url()).status_code, (302, 403))
        self.assertEqual(self.conteos(), before)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_ver_anexos_clinicos)
        self.assertFalse(rol.puede_ver_datos_generales_paciente)

    def test_upload_only_success_and_cancel_never_expose_read_or_general_profile_urls(self):
        self.asignar_rol(puede_subir_anexos_clinicos=True)
        for route, categoria, data in (
            ("clinica_subir_examen_paciente", None, self.datos_examen()),
            ("clinica_registrar_foto_evolucion", None, self.datos_evolucion()),
            ("clinica_subir_consentimiento_paciente", None, self.datos_consentimiento()),
            ("clinica_subir_documento_categoria_paciente", "documento", self.datos_documento()),
        ):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url(route, categoria=categoria))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["can_view_annexes"])
            self.assertFalse(response.context["can_view_patient"])
            for query in queries.captured_queries:
                if query["sql"].lstrip().upper().startswith("SELECT"):
                    self.assertFalse(any(model._meta.db_table in query["sql"] for model in (ExamenPaciente, PacienteFotoEvolucion, ConsentimientoClinico, DocumentoClinicoPaciente)), query["sql"])
            for forbidden in (self.url("clinica_examenes_paciente"), self.url("clinica_evolucion_paciente"), self.url("clinica_consentimientos_paciente"), self.url("clinica_documentos_categoria_paciente", categoria="documento"), self.url("clinica_paciente_detalle"), self.file_url()):
                self.assertNotContains(response, f'href="{forbidden}"')
            self.assertNotContains(response, self.examen.titulo)
            self.assertNotContains(response, self.foto.titulo)
            saved = self.client.post(self.url(route, categoria=categoria), data)
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(parse_qs(urlsplit(saved.url).query).get("app"), ["1"])
            returned = self.client.get(saved.url)
            self.assertEqual(returned.status_code, 200)
            self.assertFalse(returned.context.get("can_view_annexes", False))

    def test_company_override_without_role_and_absent_company_access_cannot_fall_back(self):
        empresa = self.empresas["medical_spa"]
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=empresa, rol_sistema=None)
        request = RequestFactory().get(self.url("clinica_subir_examen_paciente", empresa=empresa, paciente=self.pacientes[empresa.slug]))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.subir_examen_paciente(request, empresa_slug=empresa.slug, paciente_id=self.pacientes[empresa.slug].pk)
        request = RequestFactory().get(self.url("clinica_examenes_paciente"))
        request.user = Usuario.objects.create_user(username="mobile-annexes-no-company", rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            views.examenes_paciente(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk)

    def test_foreign_patients_categories_and_forged_hidden_tenants_write_nothing(self):
        before = self.conteos()
        extranjero = self.pacientes["medical_spa"]
        for route in ("clinica_examenes_paciente", "clinica_subir_examen_paciente", "clinica_evolucion_paciente", "clinica_consentimientos_paciente"):
            self.assertEqual(self.client.get(self.url(route, paciente=extranjero)).status_code, 404)
        invalid = self.client.post(self.url("clinica_subir_examen_paciente", paciente=extranjero), self.datos_examen())
        self.assertEqual(invalid.status_code, 404)
        for categoria in ("categoria-no-valida", "incapacidades"):
            self.assertEqual(self.client.get(self.url("clinica_documentos_categoria_paciente", categoria=categoria)).status_code, 404)
        self.assertEqual(self.conteos(), before)
        response = self.client.post(self.url("clinica_subir_examen_paciente"), self.datos_examen(empresa=self.empresas["medical_spa"].pk, paciente=extranjero.pk))
        self.assert_app_return(response, "clinica_examenes_paciente")
        examen = ExamenPaciente.objects.get(titulo="Hemograma móvil")
        self.assertEqual((examen.empresa_id, examen.paciente_id), (self.empresa.pk, self.paciente.pk))

    def test_protected_file_bytes_mime_security_headers_and_parts_match_original_fieldfiles(self):
        for objeto, clase, parte, field, mime in (
            (self.examen, "examen", "archivo", "archivo", "application/pdf"),
            (self.foto, "evolucion", "imagen", "imagen", "image/png"),
            (self.video, "evolucion", "video", "video", "video/mp4"),
            (self.consentimiento, "consentimiento", "archivo", "archivo", "application/pdf"),
            (self.documentos["documento"], "documento", "archivo", "archivo", "application/pdf"),
        ):
            with self.subTest(clase=clase, parte=parte):
                response = self.client.get(self.file_url(objeto, clase, parte))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response["Content-Type"], mime)
                self.assertEqual(self.cuerpo(response), Path(getattr(objeto, field).path).read_bytes())

    def test_protected_files_scope_company_patient_record_and_whitelist_without_500(self):
        before = self.conteos()
        for url in (
            self.file_url(self.examen_extranjero),
            self.file_url(paciente=self.otro_paciente),
            self.file_url(paciente=self.pacientes["medical_spa"]),
            self.file_url(self.documento_extranjero, "documento"),
            self.file_url(clase="clase-no-valida"),
            self.file_url(parte="imagen"),
            self.file_url(self.foto, "evolucion", "archivo"),
            self.file_url(self.foto, "evolucion", "video"),
            self.file_url(parte="nombre"),
            self.file_url(anexo_id=0),
            self.file_url(anexo_id=2**63),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.conteos(), before)

    def test_protected_file_is_get_only_app_only_and_module_or_view_permission_cannot_be_bypassed(self):
        self.assertEqual(self.client.get(self.file_url(mobile=False)).status_code, 404)
        self.assertIn(self.client.post(self.file_url(), {"app": "1"}).status_code, (302, 403, 405))
        post = RequestFactory().post(self.file_url(), {"app": "1"})
        post.user = Usuario.objects.get(pk=self.usuario.pk)
        denied = views.archivo_anexo_app(post, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk, clase="examen", registro_id=self.examen.pk, parte="archivo")
        self.assertEqual(denied.status_code, 405)
        head = self.client.head(self.file_url())
        self.assertEqual(head.status_code, 200)
        self.assertEqual(self.cuerpo(head), b"")
        self.asignar_rol(puede_subir_anexos_clinicos=True)
        request = RequestFactory().get(self.file_url())
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.archivo_anexo_app(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk, clase="examen", registro_id=self.examen.pk, parte="archivo")
        self.asignar_rol(puede_ver_anexos_clinicos=True)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(Http404):
            views.archivo_anexo_app(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk, clase="examen", registro_id=self.examen.pk, parte="archivo")

    def test_new_lists_and_profile_never_publish_raw_attachment_media_urls(self):
        pairs = (
            ("clinica_examenes_paciente", None, self.examen.archivo),
            ("clinica_evolucion_paciente", None, self.foto.imagen),
            ("clinica_evolucion_paciente", None, self.video.video),
            ("clinica_consentimientos_paciente", None, self.consentimiento.archivo),
            ("clinica_documentos_categoria_paciente", "documento", self.documentos["documento"].archivo),
        )
        for route, categoria, archivo in pairs:
            response = self.client.get(self.url(route, categoria=categoria))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, archivo.url)
        detail = self.client.get(self.url("clinica_paciente_detalle"))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, self.foto.imagen.url)
        self.assertNotContains(detail, self.video.video.url)
        self.assertContains(detail, self.file_url(self.foto, "evolucion", "imagen"))
        web = self.client.get(self.url("clinica_examenes_paciente", mobile=False))
        self.assertContains(web, self.examen.archivo.url)

    def test_mobile_read_post_is_denied_without_mutation_while_web_original_behavior_remains(self):
        before = self.conteos()
        for route, categoria in (("clinica_examenes_paciente", None), ("clinica_evolucion_paciente", None), ("clinica_consentimientos_paciente", None), ("clinica_documentos_categoria_paciente", "documento")):
            with self.subTest(route=route):
                response = self.client.post(self.url(route, categoria=categoria), {"app": "1", "titulo": "No debe crear"})
                self.assertIn(response.status_code, (403, 405))
                web = self.client.post(self.url(route, categoria=categoria, mobile=False), {"titulo": "No debe crear"})
                self.assertEqual(web.status_code, 200)
        self.assertEqual(self.conteos(), before)

    def test_passive_reads_keep_records_permissions_and_no_calendar_navigation_unchanged(self):
        before = self.conteos()
        for route, categoria in (("clinica_examenes_paciente", None), ("clinica_evolucion_paciente", None), ("clinica_consentimientos_paciente", None), ("clinica_documentos_categoria_paciente", "incapacidad"), ("clinica_subir_examen_paciente", None)):
            response = self.client.get(self.url(route, categoria=categoria))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            self.assertNotContains(response, f'href="{reverse("agenda_mobile", args=[self.empresa.slug])}#inicio"')
        file = self.client.get(self.file_url())
        self.assertEqual(self.cuerpo(file), self.PDF)
        self.assertEqual(self.conteos(), before)
        self.rol.refresh_from_db()
        self.assertFalse(self.rol.puede_ver_calendario)
        self.assertFalse(self.rol.puede_eliminar_anexos_clinicos)
        self.assertFalse(self.rol.puede_ver_historia_clinica)

    def test_protected_video_ranges_preserve_bytes_and_reject_invalid_or_multiple_ranges(self):
        url = self.file_url(self.video, "evolucion", "video")
        for header, expected, content_range in (
            ("bytes=2-8", self.VIDEO[2:9], f"bytes 2-8/{len(self.VIDEO)}"),
            ("bytes=-4", self.VIDEO[-4:], f"bytes {len(self.VIDEO)-4}-{len(self.VIDEO)-1}/{len(self.VIDEO)}"),
            ("bytes=4-", self.VIDEO[4:], f"bytes 4-{len(self.VIDEO)-1}/{len(self.VIDEO)}"),
        ):
            with self.subTest(header=header):
                response = self.client.get(url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response["Content-Range"], content_range)
                self.assertEqual(int(response["Content-Length"]), len(expected))
                self.assertEqual(self.cuerpo(response), expected)
        for header in ("bytes=99999-", "bytes=8-2", "bytes=0-1,3-4", "bytes=not-valid"):
            response = self.client.get(url, HTTP_RANGE=header)
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response["Content-Range"], f"bytes */{len(self.VIDEO)}")

    def test_non_inline_document_types_are_downloaded_and_explicit_download_keeps_original_bytes(self):
        office = DocumentoClinicoPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, categoria="documento", titulo="Documento Word propio", archivo=SimpleUploadedFile("documento.docx", b"PK-documento-word", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        old_unknown = DocumentoClinicoPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, categoria="incapacidad", titulo="Adjunto de registro anterior", archivo=SimpleUploadedFile("anterior.html", b"<script>contenido anterior</script>", content_type="text/html"))
        for documento in (office, old_unknown):
            response = self.client.get(self.file_url(documento, "documento"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/octet-stream")
            self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(self.cuerpo(response), Path(documento.archivo.path).read_bytes())
        response = self.client.get(self.file_url()+"&descargar=1")
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(self.cuerpo(response), self.PDF)

    def test_legacy_annex_permissions_require_original_patient_and_expediente_flags_without_grants(self):
        before = self.conteos()
        for permisos in ({"puede_pacientes": True}, {"puede_expediente_clinico": True}):
            rol = RolSistema.objects.create(nombre="Anexos legado restringido", codigo=f"mobile-annexes-legacy-{RolSistema.objects.count()}", **permisos)
            UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=self.empresa, defaults={"rol_sistema": rol})
            request = RequestFactory().get(self.url("clinica_examenes_paciente"))
            request.user = Usuario.objects.get(pk=self.usuario.pk)
            with self.assertRaises(PermissionDenied):
                views.examenes_paciente(request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk)
            self.assertIn(self.client.get(self.url("clinica_examenes_paciente")).status_code, (302, 403))
            file_request = RequestFactory().get(self.file_url())
            file_request.user = Usuario.objects.get(pk=self.usuario.pk)
            with self.assertRaises(PermissionDenied):
                views.archivo_anexo_app(file_request, empresa_slug=self.empresa.slug, paciente_id=self.paciente.pk, clase="examen", registro_id=self.examen.pk, parte="archivo")
        legacy = RolSistema.objects.create(nombre="Anexos legado autorizado", codigo="mobile-annexes-legacy-allowed", puede_pacientes=True, puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=self.empresa, defaults={"rol_sistema": legacy})
        response = self.client.get(self.url("clinica_examenes_paciente"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_annexes"])
        self.assertTrue(response.context["can_upload_annexes"])
        legacy.refresh_from_db()
        self.assertFalse(legacy.puede_ver_anexos_clinicos)
        self.assertFalse(legacy.puede_subir_anexos_clinicos)
        # Las únicas escrituras de este caso son los fixtures de roles.
        self.assertEqual(self.conteos()[:-1], before[:-1])

    def test_app_flag_does_not_replace_other_clinic_web_annexes_and_protected_endpoint_is_exact_four(self):
        empresa = Empresa.objects.create(nombre="Otra clínica anexos", slug="otra_clinica_mobile_anexos", rtn="MA-OTRA", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Otro paciente clínica no app", expediente_codigo="MA-NOAPP")
        self.usuario.empresas_acceso.add(empresa)
        legacy = RolSistema.objects.create(nombre="Anexos legado fuera", codigo="mobile-annexes-outside", puede_pacientes=True, puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=empresa, rol_sistema=legacy)
        response = self.client.get(self.url("clinica_examenes_paciente", empresa=empresa, paciente=paciente))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/examenes_paciente.html")
        self.assertFalse(response.context.get("modo_app", False))
        self.assertEqual(self.client.get(self.file_url(empresa=empresa, paciente=paciente)).status_code, 404)

    def test_incapacity_print_keeps_original_body_scoping_and_mobile_no_store(self):
        documento = self.documentos["incapacidad"]
        response = self.client.get(self.url("clinica_incapacidad_imprimir", documento=documento))
        web = self.client.get(self.url("clinica_incapacidad_imprimir", documento=documento, mobile=False))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(response, "clinica/incapacidad_imprimir.html")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.content, web.content)
        self.assertEqual(self.client.get(self.url("clinica_incapacidad_imprimir", documento=self.documento_extranjero)).status_code, 404)
        self.asignar_rol(puede_subir_anexos_clinicos=True)
        self.assertIn(self.client.get(self.url("clinica_incapacidad_imprimir", documento=documento)).status_code, (302, 403))

    def test_patient_picker_uses_existing_directory_without_general_data_and_keeps_annex_intent(self):
        for permisos, destino, route in (
            ({"puede_ver_pacientes": True, "puede_ver_anexos_clinicos": True}, "anexos", "clinica_examenes_paciente"),
            ({"puede_ver_pacientes": True, "puede_subir_anexos_clinicos": True}, "crear_anexo", "clinica_subir_examen_paciente"),
        ):
            with self.subTest(destino=destino):
                rol = self.asignar_rol(**permisos)
                selector = reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1&destino="+destino
                hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
                self.assertEqual(hub.status_code, 200)
                entry = next(item for item in hub.context["modulos_app"] if item["key"] == "anexos")
                self.assertEqual((entry["estado"], entry["url"]), ("disponible", selector))
                response = self.client.get(selector)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["can_view_patient"])
                self.assertContains(response, f'href="{self.url(route)}"')
                self.assertNotContains(response, f'href="{self.url("clinica_paciente_detalle")}"')
                self.assertEqual(self.client.get(self.url(route)).status_code, 200)
                rol.refresh_from_db()
                self.assertFalse(rol.puede_ver_datos_generales_paciente)
                self.assertFalse(rol.puede_ver_calendario)

    def test_picker_requires_patient_directory_and_cannot_grant_annexes_from_querystring(self):
        self.asignar_rol(puede_ver_pacientes=True)
        url = reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1&destino=anexos"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{self.url("clinica_examenes_paciente")}"')
        self.assertNotContains(response, f'href="{self.url("clinica_subir_examen_paciente")}"')
        self.asignar_rol(puede_subir_anexos_clinicos=True)
        self.assertIn(self.client.get(url).status_code, (302, 403))
        hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        entry = next(item for item in hub.context["modulos_app"] if item["key"] == "anexos")
        self.assertEqual((entry["estado"], entry["url"]), ("pendiente", ""))

    def test_picker_search_and_pagination_preserve_mode_scope_and_passive_records(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_anexos_clinicos=True)
        Paciente.objects.bulk_create([Paciente(empresa=self.empresa, nombre=f"Selector anexo {i:02d}", expediente_codigo=f"MA-SELECT-{i}") for i in range(26)])
        before = self.conteos()
        url = reverse("clinica_pacientes", args=[self.empresa.slug])
        response = self.client.get(url, {"app": "1", "destino": "anexos", "q": "Selector anexo", "page": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["pacientes"]), 25)
        self.assertContains(response, 'name="destino" value="anexos"')
        self.assertContains(response, "destino=anexos")
        next_page = self.client.get(url, {"app": "1", "destino": "anexos", "q": "Selector anexo", "page": "2"})
        self.assertEqual(len(next_page.context["pacientes"]), 1)
        self.assertNotContains(next_page, self.pacientes["medical_spa"].nombre)
        self.assertEqual(self.conteos(), before)
