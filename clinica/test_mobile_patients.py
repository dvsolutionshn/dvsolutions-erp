from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Cliente
from .forms import ExpedienteEventoForm, PreconsultaClinicaPublicaForm
from .models import ExpedienteEvento, HistoriaClinicaEspecialidad, Paciente, PacienteFotoEvolucion, PreconsultaClinica, ProfesionalSalud
from . import views


class MobilePatientsIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes = {}, {}
        cls.modulos = {}
        for codigo in ("clinica_medica", "agenda_citas", "facturacion"):
            cls.modulos[codigo], _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Clínica pacientes {slug}", slug=slug, rtn=f"MP-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            for modulo in cls.modulos.values():
                EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente {slug}", identidad=f"080119999200{indice}",
                expediente_codigo=f"MP-{indice}", correo=f"mp-{indice}@example.com", telefono=f"5049999200{indice}",
            )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Pacientes móvil completo", codigo="mobile-patients-all", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_clinica=True, puede_citas=True,
            puede_ver_calendario=True, puede_pacientes=True, puede_expediente_clinico=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True, puede_crear_pacientes=True,
            puede_editar_pacientes=True, puede_ver_historia_clinica=True, puede_crear_historia_clinica=True,
            puede_editar_historia_clinica=True, puede_facturas=True, puede_ver_facturas=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-patients-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        self.media = TemporaryDirectory(prefix="dv-mobile-patients-")
        self.addCleanup(self.media.cleanup)
        settings = override_settings(MEDIA_ROOT=self.media.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def url(self, nombre, paciente=None, empresa=None, mobile=True):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if paciente is not None:
            kwargs["paciente_id"] = paciente.pk
        return reverse(nombre, kwargs=kwargs) + ("?app=1" if mobile else "")

    def datos(self, **overrides):
        data = {
            "app": "1", "nombres": "Ana", "apellidos": "Mejia", "identidad": "0801199912345",
            "fecha_nacimiento": "1999-08-12", "sexo": "femenino", "estado_civil": "soltero",
            "correo": "ana@example.com", "telefono_codigo_area": "504", "telefono": "99998888",
            "direccion": "Colonia de prueba", "lugar_nacimiento": "Tegucigalpa", "ocupacion": "Administradora",
            "lugar_trabajo": "No aplica", "informante": "yo_mismo", "referido_por": "facebook",
            "contacto_emergencia_completo": "Maria Perez - 9999-9999", "motivo_categoria": ["no_aplica"],
            "procedimientos_interes": [], "procedimientos_interes_otros": "No aplica", "consentimiento_datos": "on",
        }
        data.update(overrides)
        return data

    def foto(self, nombre="paciente.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), (24, 130, 160)).save(buffer, format="JPEG")
        return SimpleUploadedFile(nombre, buffer.getvalue(), content_type="image/jpeg")

    def asignar_rol(self, empresa=None, **permisos):
        empresa = empresa or self.empresa
        rol = RolSistema.objects.create(
            nombre="Rol móvil restringido", codigo=f"mobile-patients-role-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa, defaults={"rol_sistema": rol})
        return rol

    def preconsulta(self, paciente=None, **overrides):
        paciente = paciente or self.paciente
        data = {
            "empresa": paciente.empresa, "paciente": paciente, "tipo": "general", "estado": "completada",
            "token_hash": f"mobile-patient-{paciente.pk}-{PreconsultaClinica.objects.count()}", "token_preview": "MP",
            "fecha_expiracion": timezone.now()+timedelta(days=30), "fecha_completada": timezone.now(),
            "creada_por": self.usuario,
        }
        data.update(overrides)
        return PreconsultaClinica.objects.create(**data)

    def conteos(self):
        return tuple(model.objects.count() for model in (
            Paciente, Cliente, PreconsultaClinica, HistoriaClinicaEspecialidad, ExpedienteEvento, ProfesionalSalud, PacienteFotoEvolucion,
        ))

    def test_four_clinics_render_mobile_screens_and_preserve_web_templates(self):
        routes = (
            ("clinica_pacientes", False, "mobile_patient_list.html", "pacientes.html"),
            ("clinica_crear_paciente", False, "mobile_patient_form.html", "preconsulta_publica.html"),
            ("clinica_editar_paciente", True, "mobile_patient_form.html", "preconsulta_publica.html"),
            ("clinica_paciente_detalle", True, "mobile_patient_detail.html", "paciente_detalle.html"),
            ("clinica_historias_especialidad", True, "mobile_patient_histories.html", "historias_especialidad.html"),
            ("clinica_historial_clinico_consolidado", True, "mobile_patient_history.html", "historial_clinico_consolidado.html"),
            ("clinica_crear_evento_paciente", True, "mobile_patient_event_form.html", "form.html"),
        )
        for empresa in self.empresas.values():
            for route, with_patient, mobile_template, web_template in routes:
                with self.subTest(empresa=empresa.slug, route=route):
                    paciente = self.pacientes[empresa.slug] if with_patient else None
                    mobile = self.client.get(self.url(route, paciente, empresa))
                    self.assertEqual(mobile.status_code, 200)
                    self.assertTemplateUsed(mobile, "clinica/"+mobile_template)
                    self.assertIn("no-store", mobile["Cache-Control"])
                    web = self.client.get(self.url(route, paciente, empresa, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/"+web_template)
                    self.assertTemplateNotUsed(web, "clinica/"+mobile_template)

    def test_app_flag_does_not_replace_other_company_web_interface(self):
        empresa = Empresa.objects.create(nombre="Otra clínica", slug="clinica_otro_mobile", rtn="MP-OTRO", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulos["clinica_medica"])
        self.usuario.empresas_acceso.add(empresa)
        response = self.client.get(self.url("clinica_pacientes", empresa=empresa))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/pacientes.html")
        self.assertTemplateNotUsed(response, "clinica/mobile_patient_list.html")
        self.assertFalse(response.context.get("modo_app", False))

    def test_search_and_direct_patient_routes_remain_tenant_scoped(self):
        extranjero = self.pacientes["medical_spa"]
        response = self.client.get(self.url("clinica_pacientes"), {"app": "1", "q": self.paciente.identidad})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([paciente.pk for paciente in response.context["pacientes"]], [self.paciente.pk])
        self.assertNotContains(response, extranjero.nombre)
        suggestion_response = self.client.get(self.url("clinica_pacientes_sugerencias"), {"app": "1", "q": "Paciente"})
        self.assertIn("no-store", suggestion_response["Cache-Control"])
        suggestions = suggestion_response.json()["results"]
        self.assertEqual({item["id"] for item in suggestions}, {self.paciente.pk})
        self.assertTrue(all("?app=1" in item["url"] and self.empresa.slug in item["url"] for item in suggestions))
        for route in ("clinica_paciente_detalle", "clinica_editar_paciente", "clinica_historias_especialidad", "clinica_historial_clinico_consolidado", "clinica_crear_evento_paciente"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(self.url(route, extranjero)).status_code, 404)

    def test_mobile_pagination_preserves_search_and_original_web_list(self):
        Paciente.objects.bulk_create([
            Paciente(empresa=self.empresa, nombre=f"Paginación móvil {indice:02d}", expediente_codigo=f"MP-PAGE-{indice}")
            for indice in range(26)
        ])
        before = self.conteos()
        first = self.client.get(self.url("clinica_pacientes"), {"app": "1", "q": "Paginación móvil"})
        second = self.client.get(self.url("clinica_pacientes"), {"app": "1", "q": "Paginación móvil", "page": "2"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context["pacientes"].paginator.count, 26)
        self.assertEqual(len(first.context["pacientes"]), 25)
        self.assertEqual(len(second.context["pacientes"]), 1)
        self.assertEqual(second.context["pacientes"].number, 2)
        self.assertEqual(first.context["q"], "Paginación móvil")
        self.assertContains(first, "&amp;page=2")
        self.assertContains(first, "?app=1&amp;q=Paginaci")
        self.assertNotContains(first, self.pacientes["medical_spa"].nombre)
        self.assertTrue({paciente.pk for paciente in first.context["pacientes"]}.isdisjoint(
            {paciente.pk for paciente in second.context["pacientes"]},
        ))
        web = self.client.get(self.url("clinica_pacientes", mobile=False), {"q": "Paginación móvil"})
        self.assertIsInstance(web.context["pacientes"], list)
        self.assertEqual(len(web.context["pacientes"]), 26)
        self.assertEqual(self.conteos(), before)

    def test_general_reader_cannot_see_or_enter_sensitive_actions_even_directly(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True)
        ExpedienteEvento.objects.create(empresa=self.empresa, paciente=self.paciente, titulo="Diagnóstico privado", descripcion="Nota clínica confidencial")
        detail = self.client.get(self.url("clinica_paciente_detalle", self.paciente))
        self.assertEqual(detail.status_code, 200)
        for flag in ("can_edit_patient", "can_create_patient", "can_view_history", "can_create_history"):
            self.assertFalse(detail.context[flag])
        self.assertNotContains(detail, "Nota clínica confidencial")
        for route in ("clinica_editar_paciente", "clinica_historias_especialidad", "clinica_historial_clinico_consolidado", "clinica_crear_evento_paciente"):
            self.assertNotContains(detail, self.url(route, self.paciente))
        factory = RequestFactory()
        for view, route, with_patient in (
            (views.crear_paciente, "clinica_crear_paciente", False),
            (views.editar_paciente, "clinica_editar_paciente", True),
            (views.historias_especialidad, "clinica_historias_especialidad", True),
            (views.historial_clinico_consolidado, "clinica_historial_clinico_consolidado", True),
            (views.crear_evento_expediente, "clinica_crear_evento_paciente", True),
        ):
            with self.subTest(route=route):
                paciente = self.paciente if with_patient else None
                request = factory.get(self.url(route, paciente))
                request.user = Usuario.objects.get(pk=self.usuario.pk)
                kwargs = {"empresa_slug": self.empresa.slug}
                if with_patient:
                    kwargs["paciente_id"] = self.paciente.pk
                with self.assertRaises(PermissionDenied):
                    view(request, **kwargs)
                self.assertIn(self.client.get(self.url(route, paciente)).status_code, (302, 403))

    def test_company_role_and_module_checks_cannot_fall_back_to_global_role(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        request = RequestFactory().get(self.url("clinica_pacientes", empresa=self.empresas["medical_spa"]))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(PermissionDenied):
            views.pacientes(request, empresa_slug="medical_spa")
        self.assertEqual(self.client.get(self.url("clinica_pacientes")).status_code, 200)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulos["clinica_medica"]).update(activo=False)
        request = RequestFactory().get(self.url("clinica_pacientes"))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        with self.assertRaises(Http404):
            views.pacientes(request, empresa_slug=self.empresa.slug)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulos["clinica_medica"]).update(activo=True)
        request.user = Usuario.objects.create_user(username="mobile-patients-outside", password="test", rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            views.pacientes(request, empresa_slug=self.empresa.slug)

    def test_legacy_patient_and_history_permissions_still_apply(self):
        legacy = RolSistema.objects.create(nombre="Clínica legado móvil", codigo="mobile-patients-legacy", puede_clinica=True, puede_pacientes=True, puede_expediente_clinico=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=legacy)
        for route in ("clinica_paciente_detalle", "clinica_editar_paciente", "clinica_historial_clinico_consolidado", "clinica_crear_evento_paciente"):
            with self.subTest(route=route):
                response = self.client.get(self.url(route, self.paciente))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["modo_app"])

    def test_mobile_basic_form_reuses_original_fields_uploads_and_hidden_contract(self):
        mobile = self.client.get(self.url("clinica_crear_paciente"))
        web = self.client.get(self.url("clinica_crear_paciente", mobile=False))
        self.assertIsInstance(mobile.context["form"], PreconsultaClinicaPublicaForm)
        self.assertEqual(set(mobile.context["form"].fields), set(web.context["form"].fields))
        self.assertNotIn("alergias_detalle", mobile.context["form"].fields)
        self.assertContains(mobile, 'enctype="multipart/form-data"')
        self.assertContains(mobile, 'name="app" value="1"')
        for field in mobile.context["form"].hidden_fields():
            self.assertContains(mobile, f'name="{field.name}"')
        for section in mobile.context["mobile_patient_form_sections"]:
            for field in section["campos"]:
                self.assertContains(mobile, f'name="{field.name}"')

    def test_create_uses_existing_service_client_sync_photo_and_audit(self):
        with patch("clinica.views._crear_paciente_desde_formulario_general", wraps=views._crear_paciente_desde_formulario_general) as create:
            response = self.client.post(self.url("clinica_crear_paciente"), self.datos(
                foto_perfil=self.foto(), alergias_respuesta="si", alergias_detalle="No debe ingresar desde formulario básico",
            ))
        self.assertEqual(response.status_code, 302)
        create.assert_called_once()
        paciente = Paciente.objects.get(empresa=self.empresa, identidad="0801199912345")
        self.assertRedirects(response, self.url("clinica_paciente_detalle", paciente), fetch_redirect_response=False)
        self.assertEqual(paciente.nombre, "Ana Mejia")
        self.assertEqual(paciente.creado_por, self.usuario)
        self.assertTrue(paciente.cliente_id)
        self.assertEqual(paciente.cliente.empresa_id, self.empresa.pk)
        self.assertEqual(paciente.cliente.rtn, paciente.identidad)
        self.assertFalse(paciente.es_alergico)
        self.assertFalse(paciente.alergias)
        self.assertTrue(paciente.foto_perfil)
        self.assertEqual(paciente.preconsultas.get(tipo="general").estado, "completada")
        self.assertEqual(PacienteFotoEvolucion.objects.get(paciente=paciente).creado_por, self.usuario)
        self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo="paciente", objeto_id=str(paciente.pk), accion="crear", usuario=self.usuario).exists())

    def test_invalid_create_keeps_bound_values_and_no_partial_patient(self):
        before = self.conteos()
        response = self.client.post(self.url("clinica_crear_paciente"), self.datos(
            identidad=self.paciente.identidad, nombres="Trabajo no perdido", direccion="Datos conservados", telefono="88887777",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_patient_form.html")
        self.assertIn("identidad", response.context["form"].errors)
        self.assertEqual(response.context["form"]["nombres"].value(), "Trabajo no perdido")
        self.assertContains(response, "Datos conservados")
        self.assertEqual(self.conteos(), before)
        with patch("clinica.views._sincronizar_cliente_facturacion_paciente", side_effect=ValidationError("Sincronización no disponible")):
            failed = self.client.post(self.url("clinica_crear_paciente"), self.datos(nombres="Reintento conservado"))
        self.assertEqual(failed.status_code, 200)
        self.assertContains(failed, "Sincronización no disponible")
        self.assertContains(failed, "Reintento conservado")
        self.assertEqual(self.conteos(), before)

    def test_edit_reuses_basic_update_preserves_clinical_data_and_client_sync(self):
        preconsulta = self.preconsulta(
            motivo_consulta="Motivo clínico conservado", alergias="Penicilina",
            datos_generales={"legacy_data": "conservar", "formulario_general": {"nota_clinica_prev": "No borrar"}},
        )
        with patch("clinica.views._actualizar_paciente_basico_desde_preconsulta", wraps=views._actualizar_paciente_basico_desde_preconsulta) as update:
            response = self.client.post(self.url("clinica_editar_paciente", self.paciente), self.datos(
                identidad=self.paciente.identidad, nombres="Paciente", apellidos="Actualizado", foto_perfil=self.foto("actualizado.jpg"),
                motivo_consulta="No sobrescribir", alergias="No sobrescribir",
            ))
        self.assertRedirects(response, self.url("clinica_paciente_detalle", self.paciente), fetch_redirect_response=False)
        update.assert_called_once()
        self.paciente.refresh_from_db()
        preconsulta.refresh_from_db()
        self.assertEqual(self.paciente.nombre, "Paciente Actualizado")
        self.assertEqual(self.paciente.cliente.nombre, self.paciente.nombre)
        self.assertEqual(preconsulta.motivo_consulta, "Motivo clínico conservado")
        self.assertEqual(preconsulta.alergias, "Penicilina")
        self.assertEqual(preconsulta.datos_generales["legacy_data"], "conservar")
        self.assertEqual(preconsulta.datos_generales["formulario_general"]["nota_clinica_prev"], "No borrar")
        self.assertEqual(self.paciente.preconsultas.count(), 1)
        self.assertTrue(PacienteFotoEvolucion.objects.filter(paciente=self.paciente, tipo="evolucion", creado_por=self.usuario).exists())

    def test_invalid_edit_preserves_input_and_existing_record(self):
        before = self.conteos()
        response = self.client.post(self.url("clinica_editar_paciente", self.paciente), self.datos(
            identidad="", nombres="Nombre no perdido", telefono="88886666",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn("identidad", response.context["form"].errors)
        self.assertEqual(response.context["form"]["nombres"].value(), "Nombre no perdido")
        self.assertContains(response, "88886666")
        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.nombre, "Paciente hospital_mia")
        self.assertEqual(self.conteos(), before)

    def test_passive_mobile_gets_do_not_create_records_or_seed_professionals(self):
        before = self.conteos()
        with patch("clinica.views.asegurar_profesionales_agenda_base", wraps=views.asegurar_profesionales_agenda_base) as seed:
            for route in ("clinica_pacientes", "clinica_crear_paciente", "clinica_editar_paciente", "clinica_paciente_detalle", "clinica_historias_especialidad", "clinica_historial_clinico_consolidado", "clinica_crear_evento_paciente"):
                with self.subTest(route=route):
                    paciente = None if route in ("clinica_pacientes", "clinica_crear_paciente") else self.paciente
                    self.assertEqual(self.client.get(self.url(route, paciente)).status_code, 200)
                    self.assertEqual(self.conteos(), before)
        seed.assert_not_called()

    def test_history_is_read_only_shows_existing_notes_and_blocks_all_consolidated_post_actions(self):
        HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa, paciente=self.paciente, tipo="capilar", motivo_consulta="Consulta capilar existente",
            diagnostico="Diagnóstico capilar existente", plan_tratamiento="Plan capilar existente", creado_por=self.usuario,
            notas_privadas_doctor="Apunte privado del doctor no publicable",
        )
        HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa, paciente=self.paciente, tipo="evaluacion_integral",
            datos_especialidad={"funciones_generales": "Apetito conservado",
                                "funciones_organicas": {"ojos": {"estado": "alterada", "detalle": "Visión borrosa ocasional"}},
                                "examen_fisico": {"peso": "68"}},
        )
        HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa, paciente=self.paciente, tipo="tricopigmentacion",
            datos_especialidad={"zona_tratada": "Línea frontal", "marca_lote": "Lote TC-2026"},
        )
        self.preconsulta(motivo_consulta="Motivo general existente")
        ExpedienteEvento.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="nota", titulo="Evolución existente", descripcion="Nota de evolución existente")
        response = self.client.get(self.url("clinica_historial_clinico_consolidado", self.paciente))
        self.assertEqual(response.status_code, 200)
        for text in ("Consulta capilar existente", "Diagnóstico capilar existente", "Plan capilar existente",
                     "Apetito conservado", "Visión borrosa ocasional", "Línea frontal", "Lote TC-2026"):
            self.assertContains(response, text)
        self.assertNotContains(response, "Apunte privado del doctor no publicable")
        self.assertContains(self.client.get(self.url("clinica_paciente_detalle", self.paciente)), "Nota de evolución existente")
        self.assertNotContains(response, '<form method="post"')
        self.assertNotContains(response, reverse("clinica_completar_historia_clinica_paciente", args=[self.empresa.slug, self.paciente.pk]))
        self.assertNotContains(response, reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, "capilar"]))
        before = self.conteos()
        for accion in ("guardar_historia_especialidad", "guardar_antecedente_adicional", "guardar_evaluacion_integral", "guardar_tricopigmentacion"):
            with self.subTest(accion=accion):
                denied = self.client.post(self.url("clinica_historial_clinico_consolidado", self.paciente), {
                    "app": "1", "accion": accion, "tipo_historia": "capilar", "motivo_consulta": "No debe guardar",
                })
                self.assertIn(denied.status_code, (403, 405))
                self.assertEqual(self.conteos(), before)
        self.assertEqual(HistoriaClinicaEspecialidad.objects.get(paciente=self.paciente, tipo="capilar").motivo_consulta, "Consulta capilar existente")

    def test_event_uses_existing_form_scoped_relations_and_mobile_redirect(self):
        profesional = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Profesional autorizado")
        extranjero = ProfesionalSalud.objects.create(empresa=self.empresas["medical_spa"], nombre="Profesional otra empresa")
        route = self.url("clinica_crear_evento_paciente", self.paciente)
        screen = self.client.get(route)
        self.assertIsInstance(screen.context["form"], ExpedienteEventoForm)
        self.assertEqual(list(screen.context["form"].fields["paciente"].queryset), [self.paciente])
        self.assertEqual(list(screen.context["form"].fields["profesional"].queryset), [profesional])
        data = {"app": "1", "paciente": self.paciente.pk, "profesional": extranjero.pk, "tipo": "nota",
                "fecha": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "titulo": "Nota móvil", "descripcion": "Trabajo clínico conservado"}
        invalid = self.client.post(route, data)
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("profesional", invalid.context["form"].errors)
        self.assertContains(invalid, "Trabajo clínico conservado")
        self.assertFalse(ExpedienteEvento.objects.exists())
        data["profesional"] = profesional.pk
        data["paciente"] = self.pacientes["medical_spa"].pk
        foreign_patient = self.client.post(route, data)
        self.assertEqual(foreign_patient.status_code, 200)
        self.assertIn("paciente", foreign_patient.context["form"].errors)
        self.assertFalse(ExpedienteEvento.objects.exists())
        data["paciente"] = self.paciente.pk
        response = self.client.post(route, data)
        self.assertRedirects(response, self.url("clinica_paciente_detalle", self.paciente), fetch_redirect_response=False)
        evento = ExpedienteEvento.objects.get()
        self.assertEqual((evento.empresa_id, evento.paciente_id, evento.profesional_id, evento.creado_por_id),
                         (self.empresa.pk, self.paciente.pk, profesional.pk, self.usuario.pk))
        self.assertEqual(evento.descripcion, "Trabajo clínico conservado")

    def test_creator_only_success_stays_in_authorized_mobile_flow(self):
        self.asignar_rol(puede_crear_pacientes=True)
        screen = self.client.get(self.url("clinica_crear_paciente"))
        self.assertEqual(screen.status_code, 200)
        self.assertFalse(screen.context["app_home_disponible"])
        hub_url = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        self.assertEqual(screen.context["app_modules_url"], hub_url)
        self.assertContains(screen, f'href="{hub_url}"')
        self.assertNotContains(screen, f'href="{reverse("agenda_mobile", args=[self.empresa.slug])}#inicio"')
        hub = self.client.get(hub_url)
        self.assertEqual(hub.status_code, 200)
        self.assertTemplateUsed(hub, "crm/mobile_modules.html")
        self.assertNotContains(hub, self.paciente.identidad)
        self.assertNotContains(hub, self.paciente.correo)
        self.assertEqual({module["key"] for module in hub.context["modulos_app"]}, {"pacientes"})
        self.assertEqual(hub.context["modulos_app"][0]["url"], self.url("clinica_crear_paciente"))
        response = self.client.post(self.url("clinica_crear_paciente", mobile=False), self.datos())
        self.assertRedirects(response, self.url("clinica_crear_paciente"), fetch_redirect_response=False)
        self.assertTrue(Paciente.objects.filter(empresa=self.empresa, identidad="0801199912345").exists())

    def test_new_hub_patient_entry_is_additive_and_does_not_replace_trained_home(self):
        response = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="#pacientes-app"')
        self.assertContains(response, 'id="pacientes-app"')
        self.assertContains(response, 'id="appMoreModulesEntry"')
        self.assertContains(response, 'id="mas-funciones-app"')
        for panel in ("factura-app", "facturas-app", "agenda-app"):
            self.assertContains(response, f'id="{panel}"')
        module = next(item for item in response.context["modulos_app"] if item["key"] == "pacientes")
        self.assertEqual(module["url"], self.url("clinica_pacientes"))
        self.assertEqual(module["panel"], "")
        registry = self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug])).json()
        self.assertEqual(next(item for item in registry["modulos"] if item["key"] == "pacientes")["url"], self.url("clinica_pacientes"))
