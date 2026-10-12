from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, RegistroAuditoria, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Factura, MovimientoInventario
from . import views
from .forms import FUNCIONES_ORGANICAS_SISTEMAS, EvaluacionClinicaIntegralForm, TricopigmentacionForm
from .models import AntecedenteAdicionalHistorial, AntecedenteAdicionalPaciente, ClasificacionAlopecia, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica
from . import test_mobile_histories as history_fixtures


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileSpecialHistoriesIntegrationTests(TestCase):
    # Reuse synthetic fixtures, not the earlier test class (no inherited tests).
    TIPOS = history_fixtures.MobileHistoriesIntegrationTests.TIPOS
    FORMULARIOS = {
        "evaluacion_integral": ("guardar_evaluacion_integral", "evaluacion", None),
        "funciones_organicas": ("guardar_funciones_organicas", "funciones", "funciones_organicas"),
        "examen_fisico": ("guardar_examen_fisico", "examen", "examen_fisico"),
        "tricopigmentacion": ("guardar_tricopigmentacion", "tricopigmentacion", None),
    }
    asignar_rol = history_fixtures.MobileHistoriesIntegrationTests.asignar_rol
    preconsulta = history_fixtures.MobileHistoriesIntegrationTests.preconsulta

    @classmethod
    def setUpTestData(cls):
        history_fixtures.MobileHistoriesIntegrationTests.setUpTestData.__func__(cls)

    def setUp(self):
        history_fixtures.MobileHistoriesIntegrationTests.setUp(self)

    def url(self, formulario="evaluacion_integral", empresa=None, paciente=None, mobile=True):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        params = {}
        if mobile:
            params["app"] = "1"
        if formulario is not None:
            params["formulario"] = formulario
        return reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])+("?"+urlencode(params) if params else "")

    def datos(self, codigo="evaluacion_integral", empresa=None, **overrides):
        empresa = empresa or self.empresa
        accion, prefix, section = self.FORMULARIOS[codigo]
        data = {"app": "1", "formulario": codigo, "accion": accion,
                f"{prefix}-profesional": self.profesionales[empresa.slug].pk,
                f"{prefix}-fecha_atencion": "2026-10-11T10:15", f"{prefix}-estado": "borrador"}
        if codigo == "tricopigmentacion":
            data.update({f"{prefix}-{name}": value for name, value in {
                "numero_sesion": "Sesión sintética", "zona_tratada": "Zona de prueba", "pigmento": "Color de prueba", "marca_lote": "Referencia de prueba",
                "tecnica": "Texto de técnica sintético", "aguja_cartucho": "Referencia sintética", "evaluacion": "Evaluación de prueba\nSegunda línea",
                "procedimiento": "Procedimiento de prueba", "indicaciones": "Texto de prueba", "observaciones": "Nota de prueba",
                "proxima_sesion": "2026-10-20",
            }.items()})
        else:
            if section != "examen_fisico":
                data.update({f"{prefix}-funcion_{codigo}": "normal" for codigo, _ in FUNCIONES_ORGANICAS_SISTEMAS})
                data.update({f"{prefix}-funciones_generales": "Texto general de funciones de prueba",
                             f"{prefix}-funcion_ojos": "alterada", f"{prefix}-funcion_ojos_detalle": "Detalle sintético conservado"})
            if section != "funciones_organicas":
                data.update({f"{prefix}-examen_peso": "68 kg", f"{prefix}-examen_estatura": "165 cm", f"{prefix}-examen_saturacion_spo2": "99%",
                             f"{prefix}-examen_fisico": "Texto de examen físico sintético\nSegunda línea conservada"})
        data.update(overrides)
        return data

    def conteos(self):
        return tuple(model.objects.count() for model in (
            HistoriaClinicaEspecialidad, ClasificacionAlopecia, PreconsultaClinica, AntecedenteAdicionalPaciente, AntecedenteAdicionalHistorial,
            Paciente, ProfesionalSalud, RecetaMedica, MovimientoInventario, Factura, RegistroAuditoria,
        ))

    def directo(self, formulario="evaluacion_integral", empresa=None, paciente=None, method="get", data=None, user=None):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        factory = RequestFactory()
        request = factory.get(self.url(formulario, empresa, paciente)) if method == "get" else factory.post(self.url(formulario, empresa, paciente), data or {"app": "1"})
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        return views.historial_clinico_consolidado(request, empresa_slug=empresa.slug, paciente_id=paciente.pk)

    def assert_no_prior_selects(self, captured):
        selects = [q["sql"] for q in captured if q["sql"].lstrip().upper().startswith("SELECT")]
        for model in (PreconsultaClinica, HistoriaClinicaEspecialidad, ClasificacionAlopecia, AntecedenteAdicionalPaciente, AntecedenteAdicionalHistorial):
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects), model._meta.label)

    def test_four_clinics_render_single_mobile_composers_web_and_old_mobile_unchanged(self):
        for empresa in self.empresas.values():
            for formulario in self.FORMULARIOS:
                with self.subTest(empresa=empresa.slug, formulario=formulario):
                    response = self.client.get(self.url(formulario, empresa))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "clinica/mobile_special_history_form.html")
                    self.assertNotContains(response, "<table")
                    self.assertIn("no-store", response["Cache-Control"])
                    web = self.client.get(self.url(formulario, empresa, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/historial_clinico_consolidado.html")
                    self.assertTemplateNotUsed(web, "clinica/mobile_special_history_form.html")
            original_mobile = self.client.get(self.url(None, empresa))
            self.assertTemplateUsed(original_mobile, "clinica/mobile_patient_history.html")
            self.assertNotContains(original_mobile, '<form method="post"')

    def test_both_read_and_edit_are_required_no_editor_only_or_creator_grants(self):
        for permisos in ({"puede_ver_historia_clinica": True}, {"puede_editar_historia_clinica": True},
                         {"puede_crear_historia_clinica": True}, {"puede_ver_historia_clinica": True, "puede_crear_historia_clinica": True}):
            self.asignar_rol(**permisos)
            before = self.conteos()
            for formulario in self.FORMULARIOS:
                with self.subTest(permisos=permisos, formulario=formulario):
                    self.assertIn(self.client.get(self.url(formulario)).status_code, (302, 403))
                    self.assertIn(self.client.post(self.url(formulario), self.datos(formulario)).status_code, (302, 403))
                    with self.assertRaises(PermissionDenied):
                        self.directo(formulario)
                    with self.assertRaises(PermissionDenied):
                        self.directo(formulario, method="post", data=self.datos(formulario))
            self.assertEqual(self.conteos(), before)

    def test_legacy_expediente_alone_preserved_but_patients_or_configuration_do_not_authorize(self):
        rol = self.asignar_rol(granular=False, puede_expediente_clinico=True)
        for formulario in self.FORMULARIOS:
            self.assertEqual(self.client.get(self.url(formulario)).status_code, 200)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_pacientes)
        for permisos in ({"puede_pacientes": True}, {"puede_configuracion_clinica": True}):
            self.asignar_rol(granular=False, **permisos)
            with self.assertRaises(PermissionDenied):
                self.directo()

    def test_direct_module_company_role_active_company_and_membership_are_enforced(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        with self.assertRaises(PermissionDenied):
            self.directo(empresa=self.empresas["medical_spa"])
        with self.assertRaises(PermissionDenied):
            self.directo(user=Usuario.objects.create_user(username="mobile-special-no-company", rol_sistema=self.rol))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            self.directo()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        with self.assertRaises(Http404):
            self.directo()

    def test_other_clinic_app_flag_does_not_replace_web_consolidated_interface(self):
        empresa = Empresa.objects.create(nombre="Otra clínica especial", slug="otra_clinica_special", rtn="MHS-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente fuera de cuatro", expediente_codigo="MHS-OUT")
        self.asignar_rol(empresa=empresa, granular=False, puede_expediente_clinico=True)
        response = self.client.get(self.url(empresa=empresa, paciente=paciente))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/historial_clinico_consolidado.html")
        self.assertFalse(response.context.get("modo_app", False))

    def test_passive_composer_get_does_not_query_prior_data_or_create_defaults(self):
        self.preconsulta()
        AntecedenteAdicionalPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, contenido="Antecedente previo confidencial")
        before = self.conteos()
        for formulario in self.FORMULARIOS:
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(self.url(formulario))
            self.assertEqual(response.status_code, 200)
            self.assert_no_prior_selects(captured)
            self.assertNotContains(response, "Antecedente previo confidencial")
        self.assertEqual(self.conteos(), before)

    def test_original_form_fields_prefixes_choices_labels_validators_and_metadata_preserved(self):
        for formulario, (accion, prefix, section) in self.FORMULARIOS.items():
            response = self.client.get(self.url(formulario))
            form = response.context["form"]
            canonical = TricopigmentacionForm(empresa=self.empresa, prefix=prefix) if formulario == "tricopigmentacion" else EvaluacionClinicaIntegralForm(empresa=self.empresa, prefix=prefix, seccion=section)
            self.assertIsInstance(form, type(canonical))
            self.assertEqual(form.prefix, prefix)
            self.assertEqual(list(form.fields), list(canonical.fields))
            for name, field in canonical.fields.items():
                self.assertIs(type(form.fields[name]), type(field))
                self.assertEqual(form.fields[name].required, field.required)
                self.assertEqual(form.fields[name].label, field.label)
                self.assertEqual(form.fields[name].input_formats if hasattr(field, "input_formats") else None, field.input_formats if hasattr(field, "input_formats") else None)
                if hasattr(field, "choices") and name != "profesional":
                    self.assertEqual(list(form.fields[name].choices), list(field.choices))
            self.assertContains(response, f'name="formulario" value="{formulario}"', count=1)
            self.assertContains(response, f'name="accion" value="{accion}"', count=1)
            self.assertContains(response, f'name="{prefix}-fecha_atencion"', count=1)
            self.assertEqual(list(form.fields["profesional"].queryset.values_list("pk", flat=True)), [self.profesionales[self.empresa.slug].pk])

    def test_all_four_actions_use_original_structured_serializer_save_actor_and_audit(self):
        before = self.conteos()
        for formulario, (accion, prefix, section) in self.FORMULARIOS.items():
            data = self.datos(formulario)
            canonical = TricopigmentacionForm(data, empresa=self.empresa, prefix=prefix) if formulario == "tricopigmentacion" else EvaluacionClinicaIntegralForm(data, empresa=self.empresa, prefix=prefix, seccion=section)
            self.assertTrue(canonical.is_valid(), canonical.errors)
            expected = canonical.datos_estructurados()
            response = self.client.post(self.url(formulario), data)
            self.assertEqual(response.status_code, 302)
            self.assertIn("app=1", response.url)
            self.assertEqual(self.client.get(response.url).status_code, 200)
            historia = HistoriaClinicaEspecialidad.objects.filter(paciente=self.paciente).latest("pk")
            self.assertEqual(historia.tipo, "tricopigmentacion" if formulario == "tricopigmentacion" else "evaluacion_integral")
            self.assertEqual(historia.datos_especialidad, expected)
            self.assertEqual((historia.empresa_id, historia.creado_por_id, historia.actualizado_por_id, historia.profesional_id),
                             (self.empresa.pk, self.usuario.pk, self.usuario.pk, self.profesionales[self.empresa.slug].pk))
            self.assertEqual(timezone.localtime(historia.fecha_atencion).strftime("%Y-%m-%dT%H:%M"), "2026-10-11T10:15")
            audit = RegistroAuditoria.objects.get(modelo="historiaclinicaespecialidad", objeto_id=str(historia.pk), accion="crear", metodo_http="POST")
            self.assertEqual((audit.usuario_id, audit.empresa_id), (self.usuario.pk, self.empresa.pk))
            if formulario == "funciones_organicas":
                self.assertEqual(historia.examen_fisico, "")
                self.assertFalse(any(historia.datos_especialidad["examen_fisico"].values()))
            elif formulario == "examen_fisico":
                self.assertFalse(any(item["estado"] or item["detalle"] for item in historia.datos_especialidad["funciones_organicas"].values()))
                self.assertIn("Peso (kg): 68 kg", historia.signos_vitales)
            elif formulario == "tricopigmentacion":
                self.assertEqual(historia.datos_especialidad["proxima_sesion"], "2026-10-20")
                self.assertEqual(historia.observaciones, "Nota de prueba")
        self.assertEqual(self.conteos()[1:-1], before[1:-1])

    def test_invalid_evaluation_required_state_preserves_work_without_prior_queries_or_writes(self):
        for value in (None, "forged"):
            data = self.datos()
            if value is None:
                del data["evaluacion-funcion_cabeza"]
            else:
                data["evaluacion-funcion_cabeza"] = value
            before = self.conteos()
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(self.url(), data)
            self.assertEqual(response.status_code, 200)
            self.assertIn("funcion_cabeza", response.context["form"].errors)
            self.assertEqual(response.context["form"]["funcion_ojos_detalle"].value(), "Detalle sintético conservado")
            self.assertEqual(response.context["form"]["examen_peso"].value(), "68 kg")
            self.assert_no_prior_selects(captured)
            self.assertEqual(self.conteos(), before)

    def test_original_optional_detail_does_not_gain_new_clinical_validation(self):
        response = self.client.post(self.url("funciones_organicas"), self.datos("funciones_organicas", **{"funciones-funcion_ojos_detalle": ""}))
        self.assertEqual(response.status_code, 302)
        historia = HistoriaClinicaEspecialidad.objects.filter(paciente=self.paciente).latest("pk")
        self.assertEqual(historia.datos_especialidad["funciones_organicas"]["ojos"], {"estado": "alterada", "detalle": ""})

    def test_original_exam_text_values_keep_units_and_do_not_add_numeric_ranges(self):
        response = self.client.post(self.url("examen_fisico"), self.datos("examen_fisico", **{"examen-examen_peso": "Texto libre de prueba", "examen-examen_imc": "Referencia sin cálculo", "examen-examen_saturacion_spo2": "99%"}))
        self.assertEqual(response.status_code, 302)
        historia = HistoriaClinicaEspecialidad.objects.filter(paciente=self.paciente).latest("pk")
        self.assertEqual(historia.datos_especialidad["examen_fisico"]["peso"], "Texto libre de prueba")
        self.assertEqual(historia.datos_especialidad["examen_fisico"]["imc"], "Referencia sin cálculo")
        self.assertEqual(historia.datos_especialidad["examen_fisico"]["saturacion_spo2"], "99%")

    def test_original_tricopigmentation_session_is_free_text_and_optional_date_uses_iso(self):
        response = self.client.post(self.url("tricopigmentacion"), self.datos("tricopigmentacion", **{"tricopigmentacion-numero_sesion": "Control externo", "tricopigmentacion-proxima_sesion": ""}))
        self.assertEqual(response.status_code, 302)
        historia = HistoriaClinicaEspecialidad.objects.filter(paciente=self.paciente).latest("pk")
        self.assertEqual(historia.datos_especialidad["numero_sesion"], "Control externo")
        self.assertIsNone(historia.datos_especialidad["proxima_sesion"])

    def test_invalid_date_or_status_preserves_all_tricopigmentation_fields_and_no_prior_reads(self):
        for overrides in ({"tricopigmentacion-proxima_sesion": "fecha-inválida"}, {"tricopigmentacion-fecha_atencion": "fecha-inválida"}, {"tricopigmentacion-estado": "forged"}):
            before, data = self.conteos(), self.datos("tricopigmentacion", **overrides)
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(self.url("tricopigmentacion"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            for field in ("numero_sesion", "marca_lote", "evaluacion", "procedimiento", "observaciones"):
                self.assertEqual(response.context["form"][field].value(), data["tricopigmentacion-"+field])
            self.assert_no_prior_selects(captured)
            self.assertEqual(self.conteos(), before)

    def test_foreign_patient_and_professional_scope_prevents_invalid_writes(self):
        before = self.conteos()
        self.assertEqual(self.client.get(self.url(paciente=self.pacientes["medical_spa"])).status_code, 404)
        self.assertEqual(self.client.post(self.url(paciente=self.pacientes["medical_spa"]), self.datos()).status_code, 404)
        response = self.client.post(self.url("examen_fisico"), self.datos("examen_fisico", **{"examen-profesional": self.profesionales["medical_spa"].pk}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("profesional", response.context["form"].errors)
        self.assertEqual(self.conteos(), before)

    def test_old_mobile_post_without_composer_remains_blocked_for_all_other_actions(self):
        before = self.conteos()
        for accion in ("guardar_antecedente_adicional", "editar_texto_historia", "eliminar_historia", "guardar_historia_especialidad", "forged"):
            response = self.client.post(self.url(None), {"app": "1", "accion": accion, "historia_id": self.historias[(self.empresa.slug, "capilar")].pk, "tipo_historia": "capilar"})
            self.assertIn(response.status_code, (403, 405))
        for formulario in self.FORMULARIOS:
            data = self.datos(formulario)
            del data["formulario"]
            response = self.client.post(self.url(None), data)
            self.assertIn(response.status_code, (403, 405))
        self.assertEqual(self.conteos(), before)

    def test_mixed_inline_type_or_old_action_never_executes_other_original_branch(self):
        before = self.conteos()
        for overrides in ({"tipo_historia": "capilar"}, {"accion": "guardar_antecedente_adicional"}, {"accion": "eliminar_historia"}, {"accion": "editar_texto_historia"}, {"accion": "guardar_tricopigmentacion"}):
            data = self.datos(**overrides)
            data["historia_id"] = self.historias[(self.empresa.slug, "capilar")].pk
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(self.url(), data)
            self.assertIn(response.status_code, (403, 404, 405))
            self.assert_no_prior_selects(captured)
            self.assertEqual(self.conteos(), before)

    def test_duplicate_action_or_formulario_metadata_is_rejected_not_last_value_selected(self):
        before = self.conteos()
        for overrides in ({"accion": ["eliminar_historia", "guardar_evaluacion_integral"]},
                          {"accion": ["guardar_evaluacion_integral", "guardar_evaluacion_integral"]},
                          {"formulario": ["examen_fisico", "evaluacion_integral"]}):
            response = self.client.post(self.url(), self.datos(**overrides))
            self.assertIn(response.status_code, (403, 404, 405))
            self.assertEqual(self.conteos(), before)

    def test_unknown_composer_or_body_app_flag_is_not_a_generic_write_entry(self):
        before = self.conteos()
        self.assertIn(self.client.get(self.url("forged")).status_code, (403, 404))
        for overrides in ({"formulario": "forged"}, {"app": "0"}):
            response = self.client.post(self.url(), self.datos(**overrides))
            self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.conteos(), before)

    def test_forged_company_patient_actor_metadata_is_rejected_without_mutation(self):
        before = self.conteos()
        data = self.datos("examen_fisico")
        data.update({"empresa": self.empresas["medical_spa"].pk, "paciente": self.pacientes["medical_spa"].pk, "creado_por": 999999, "actualizado_por": 999999})
        response = self.client.post(self.url("examen_fisico"), data)
        self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.conteos(), before)

    def test_query_body_composer_mismatch_and_foreign_prefix_never_execute_a_save(self):
        before = self.conteos()
        for url, data in (
            (self.url("examen_fisico"), self.datos("evaluacion_integral")),
            (self.url(), self.datos(**{"tricopigmentacion-evaluacion": "No debe guardar otro formulario"})),
        ):
            response = self.client.post(url, data)
            self.assertIn(response.status_code, (403, 404, 405))
            self.assertEqual(self.conteos(), before)

    def test_existing_create_route_redirect_requires_old_create_and_both_special_permissions(self):
        for tipo in ("evaluacion_integral", "tricopigmentacion"):
            entry = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, tipo])+"?app=1"
            response = self.client.get(entry)
            self.assertEqual(response.status_code, 302)
            self.assertIn("app=1", response.url)
            self.assertIn("formulario="+tipo, response.url)
            self.assertEqual(self.client.get(response.url).status_code, 200)
            before = self.conteos()
            self.assertIn(self.client.post(entry, {"app": "1"}).status_code, (403, 405))
            self.assertEqual(self.conteos(), before)
        for permisos in ({"puede_crear_historia_clinica": True}, {"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True}):
            self.asignar_rol(**permisos)
            for tipo in ("evaluacion_integral", "tricopigmentacion"):
                entry = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, tipo])+"?app=1"
                self.assertIn(self.client.get(entry).status_code, (302, 403))

    def test_no_calendar_or_patient_general_access_is_implied_by_composer_navigation(self):
        self.asignar_rol(puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["app_home_disponible"])
        self.assertFalse(response.context["can_view_patient"])
        self.assertFalse(response.context["can_view_patients"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertNotContains(response, f'href="{reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])}?app=1"')

    def test_original_web_saves_keep_original_action_prefix_and_redirects(self):
        for formulario in self.FORMULARIOS:
            data = self.datos(formulario)
            del data["app"]
            del data["formulario"]
            response = self.client.post(self.url(None, mobile=False), data)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("app=1", response.url)
            self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_special_patient_selector_requires_list_and_both_permissions_no_create_flag(self):
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        for formulario in self.FORMULARIOS:
            response = self.client.get(picker, {"app": "1", "destino": "evaluacion_clinica", "formulario": formulario})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_patient_special_history_mode"])
            self.assertEqual(response.context["mobile_patient_special_history_form"]["codigo"], formulario)
            self.assertFalse(response.context["can_create_history"])
            self.assertFalse(response.context["can_view_patient"])
            self.assertContains(response, self.url(formulario).replace("&", "&amp;"))
        response = self.client.get(picker, {"app": "1", "destino": "evaluacion_clinica", "formulario": "forged"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("mobile_patient_special_history_mode"))
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True)
        response = self.client.get(picker, {"app": "1", "destino": "evaluacion_clinica"})
        self.assertFalse(response.context.get("mobile_patient_special_history_mode"))
        self.asignar_rol(puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        self.assertIn(self.client.get(picker, {"app": "1", "destino": "evaluacion_clinica"}).status_code, (302, 403))
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_special_selector_query_pagination_and_form_switch_preserve_intent(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        Paciente.objects.bulk_create([
            Paciente(empresa=self.empresa, nombre=f"Selector especial {index:02d}", expediente_codigo=f"MHS-SEL-{index:02d}") for index in range(27)
        ])
        before = self.conteos()
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        for page in ("1", "2"):
            response = self.client.get(picker, {"app": "1", "destino": "evaluacion_clinica", "formulario": "examen_fisico", "q": "Selector especial", "page": page})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_patient_special_history_mode"])
            self.assertEqual(response.context["mobile_patient_special_history_form"]["codigo"], "examen_fisico")
            self.assertEqual(response.context["pacientes"].paginator.count, 27)
            self.assertContains(response, 'name="destino" value="evaluacion_clinica"')
            self.assertContains(response, 'name="formulario" value="examen_fisico"')
            self.assertContains(response, "destino=evaluacion_clinica&amp;formulario=examen_fisico")
            self.assertContains(response, "q=Selector%20especial")
            self.assertNotContains(response, self.pacientes["medical_spa"].nombre)
            for paciente in response.context["pacientes"]:
                self.assertContains(response, self.url("examen_fisico", paciente=paciente).replace("&", "&amp;"))
        self.assertEqual(self.conteos(), before)

    def test_special_hub_card_requires_both_permissions_and_patient_list_for_url(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for permisos, state in (
            ({"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True, "puede_ver_pacientes": True}, "parcial"),
            ({"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True}, "pendiente"),
            ({"puede_ver_historia_clinica": True}, None),
            ({"puede_editar_historia_clinica": True}, None),
            ({"puede_crear_historia_clinica": True, "puede_ver_pacientes": True}, None),
        ):
            with self.subTest(permisos=permisos):
                self.asignar_rol(**permisos)
                response = self.client.get(hub)
                self.assertEqual(response.status_code, 200)
                card = next((m for m in response.context["modulos_app"] if m["key"] == "evaluaciones"), None)
                self.assertEqual(card is not None, state is not None)
                if card:
                    self.assertEqual(card["estado"], state)
                    self.assertEqual(bool(card["url"]), state == "parcial")
                    if card["url"]:
                        self.assertIn("app=1&destino=evaluacion_clinica", card["url"])
                        self.assertEqual(self.client.get(card["url"]).status_code, 200)
