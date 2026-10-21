from copy import deepcopy
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, RegistroAuditoria, Usuario
from crm.models import CitaCliente
from facturacion.models import Factura, MovimientoInventario
from . import test_mobile_histories as history_fixtures
from . import views
from .forms import AntecedenteAdicionalPacienteForm, HistoriaClinicaEspecialidadForm
from .models import AntecedenteAdicionalHistorial, AntecedenteAdicionalPaciente, ClasificacionAlopecia, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAntecedentsIntegrationTests(TestCase):
    # Borrow synthetic fixtures/helpers, never inherit/import another TestCase.
    TIPOS = history_fixtures.MobileHistoriesIntegrationTests.TIPOS
    ESPECIALES = ("evaluacion_integral", "tricopigmentacion")
    asignar_rol = history_fixtures.MobileHistoriesIntegrationTests.asignar_rol

    @classmethod
    def setUpTestData(cls):
        history_fixtures.MobileHistoriesIntegrationTests.setUpTestData.__func__(cls)
        cls.antecedentes, cls.versiones, cls.especiales = {}, {}, {}
        for empresa in cls.empresas.values():
            paciente = cls.pacientes[empresa.slug]
            profesional = cls.profesionales[empresa.slug]
            antecedente = AntecedenteAdicionalPaciente.objects.create(
                empresa=empresa, paciente=paciente, contenido=f"Antecedente sintético anterior {empresa.slug}\nSegunda línea conservada",
                profesional=profesional, actualizado_por=cls.usuario,
            )
            cls.antecedentes[empresa.slug] = antecedente
            cls.versiones[empresa.slug] = AntecedenteAdicionalHistorial.objects.create(
                antecedente=antecedente, empresa=empresa, paciente=paciente, texto_anterior="",
                texto_nuevo=antecedente.contenido, profesional=profesional, usuario=cls.usuario,
            )
            for tipo in cls.ESPECIALES:
                data = {"unknown_retired_key": {"nested": ["antiguo", {"codigo": 7}]}, "legacy_flag": True}
                if tipo == "evaluacion_integral":
                    data.update({"funciones_generales": "Texto histórico general", "funciones_organicas": {"ojos": {"estado": "alterada", "detalle": "Detalle antiguo", "old_subkey": "preservar"}, "retired_system": {"estado": "valor antiguo"}}, "examen_fisico": {"peso": "68 kg", "retired_measurement": "Texto antiguo"}})
                else:
                    data.update({"numero_sesion": "Sesión histórica libre", "zona_tratada": "Zona sintética", "pigmento": "Referencia antigua", "marca_lote": "Lote histórico", "tecnica": "Texto antiguo", "aguja_cartucho": "Referencia antigua", "proxima_sesion": "2026-11-20"})
                cls.especiales[(empresa.slug, tipo)] = HistoriaClinicaEspecialidad.objects.create(
                    empresa=empresa, paciente=paciente, profesional=profesional, tipo=tipo,
                    fecha_atencion=timezone.now(), creado_por=cls.usuario, actualizado_por=cls.usuario,
                    datos_especialidad=data, motivo_consulta="Motivo histórico", antecedentes="Antecedentes de nota conservados",
                    historia_enfermedad_actual="Enfermedad actual histórica", signos_vitales="Mediciones de texto conservadas", examen_fisico="Examen histórico",
                    evaluacion_clinica="Evaluación textual histórica", diagnostico="Texto diagnóstico anterior", analisis_clinico="Análisis previo",
                    procedimiento="Procedimiento histórico", conducta="Conducta histórica", plan_tratamiento="Plan previo", indicaciones="Indicaciones previas",
                    observaciones="Observación histórica", notas_privadas_doctor="Nota interna autorizada", estado="finalizada",
                )

    def setUp(self):
        history_fixtures.MobileHistoriesIntegrationTests.setUp(self)

    def url(self, empresa=None, paciente=None, mobile=True, formulario="antecedentes"):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        params = {"app": "1"} if mobile else {}
        if formulario is not None:
            params["formulario"] = formulario
        return reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])+("?"+urlencode(params) if params else "")

    def datos(self, texto="  Antecedente actualizado sintético\nSegunda línea conservada  ", **overrides):
        data = {"app": "1", "formulario": "antecedentes", "accion": "guardar_antecedente_adicional", "antecedente_adicional-contenido": texto}
        data.update(overrides)
        return data

    def edit_url(self, tipo="evaluacion_integral", empresa=None, paciente=None, historia=None, mobile=True):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        historia = historia or self.especiales[(empresa.slug, tipo)]
        return reverse("clinica_editar_historia_especialidad", args=[empresa.slug, paciente.pk, historia.pk])+("?app=1" if mobile else "")

    def edit_data(self, codigo="evaluacion_integral", empresa=None, historia=None, **overrides):
        empresa = empresa or self.empresa
        historia = historia or self.especiales[(empresa.slug, codigo)]
        data = {name: getattr(historia, name) for name in HistoriaClinicaEspecialidadForm.Meta.fields if name not in {"profesional", "fecha_atencion"}}
        data.update({"app": "1", "profesional": historia.profesional_id or "", "fecha_atencion": "2026-10-03T14:25", "plan_tratamiento": "Plan textual actualizado sin tocar JSON"})
        data.update(overrides)
        return data

    def conteos(self):
        return tuple(model.objects.count() for model in (
            AntecedenteAdicionalPaciente, AntecedenteAdicionalHistorial, HistoriaClinicaEspecialidad, ClasificacionAlopecia,
            PreconsultaClinica, Paciente, ProfesionalSalud, RecetaMedica, MovimientoInventario, Factura, CitaCliente, RegistroAuditoria,
        ))

    def directo(self, empresa=None, paciente=None, method="get", data=None, user=None, edit=False, tipo="evaluacion_integral"):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        url = self.edit_url(tipo, empresa, paciente) if edit else self.url(empresa, paciente)
        request = RequestFactory().get(url) if method == "get" else RequestFactory().post(url, data or self.datos())
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        kwargs = {"empresa_slug": empresa.slug, "paciente_id": paciente.pk}
        if edit:
            kwargs["historia_id"] = self.especiales[(empresa.slug, tipo)].pk
        return (views.editar_historia_especialidad if edit else views.historial_clinico_consolidado)(request, **kwargs)

    def assert_no_prior_queries(self, queries, allow_current_history=False):
        selects = [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith("SELECT")]
        forbidden = [PreconsultaClinica, ClasificacionAlopecia]
        if not allow_current_history:
            forbidden.append(HistoriaClinicaEspecialidad)
        for model in forbidden:
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects), model._meta.label)

    def test_four_clinics_composer_keeps_original_web_and_no_passive_writes(self):
        before = self.conteos()
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_antecedents_form.html")
            self.assertIn("no-store", response["Cache-Control"])
            self.assertNotContains(response, "<table")
            web = self.client.get(self.url(empresa, mobile=False))
            self.assertEqual(web.status_code, 200)
            self.assertTemplateUsed(web, "clinica/historial_clinico_consolidado.html")
            self.assertTemplateNotUsed(web, "clinica/mobile_antecedents_form.html")
        self.assertEqual(self.conteos(), before)

    def test_composer_uses_exact_original_single_field_prefix_and_existing_content(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        self.assertIsInstance(form, AntecedenteAdicionalPacienteForm)
        self.assertEqual(list(form.fields), ["contenido"])
        self.assertEqual(form.prefix, "antecedente_adicional")
        self.assertEqual(form["contenido"].value(), self.antecedentes[self.empresa.slug].contenido)
        self.assertContains(response, 'name="antecedente_adicional-contenido"', count=1)
        for name, value in (("app", "1"), ("formulario", "antecedentes"), ("accion", "guardar_antecedente_adicional")):
            self.assertContains(response, f'name="{name}" value="{value}"', count=1)

    def test_composer_requires_read_and_edit_independently_even_without_middleware(self):
        for grants in ({"puede_ver_historia_clinica": True}, {"puede_editar_historia_clinica": True}, {"puede_crear_historia_clinica": True}):
            self.asignar_rol(**grants)
            before = self.conteos()
            self.assertIn(self.client.get(self.url()).status_code, (302, 403))
            with self.assertRaises(PermissionDenied):
                self.directo()
            with self.assertRaises(PermissionDenied):
                self.directo(method="post", data=self.datos())
            if not grants.get("puede_editar_historia_clinica"):
                with self.assertRaises(PermissionDenied):
                    self.directo(edit=True)
                self.assertIn(self.client.get(self.edit_url()).status_code, (302, 403))
            else:
                self.assertEqual(self.client.get(self.edit_url()).status_code, 200)
            self.assertEqual(self.conteos(), before)
        self.asignar_rol(puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_patient"])
        self.assertFalse(response.context["can_view_patients"])
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')

    def test_module_membership_active_company_and_patient_scope_are_enforced_directly(self):
        before = self.conteos()
        self.assertEqual(self.client.get(self.url(paciente=self.pacientes["medical_spa"])).status_code, 404)
        self.assertEqual(self.client.post(self.url(paciente=self.pacientes["medical_spa"]), self.datos()).status_code, 404)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            self.directo()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        other = Usuario.objects.create_user(username="antecedents-outsider", password="test", empresa=self.empresas["medical_spa"], rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            self.directo(user=other)
        self.empresa.activa = False
        self.empresa.save(update_fields=["activa"])
        with self.assertRaises(Http404):
            self.directo()
        self.assertEqual(self.conteos()[:-1], before[:-1])

    def test_outside_four_companies_app_flag_keeps_original_web_composer(self):
        empresa = Empresa.objects.create(nombre="Otra clínica antecedentes", slug="otra_clinica_antecedents", rtn="MA-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente clínica adicional", expediente_codigo="MA-OUT")
        self.asignar_rol(empresa=empresa, granular=False, puede_clinica=True, puede_expediente_clinico=True)
        response = self.client.get(self.url(empresa, paciente))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/historial_clinico_consolidado.html")
        self.assertFalse(response.context.get("modo_app", False))
        for tipo in self.ESPECIALES:
            historia = HistoriaClinicaEspecialidad.objects.create(empresa=empresa, paciente=paciente, tipo=tipo, datos_especialidad={"legacy": "preservar"})
            response = self.client.get(self.edit_url(tipo, empresa, paciente, historia=historia))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/historia_especialidad_form.html")
            self.assertFalse(response.context.get("mobile_history_generic_only", False))

    def test_get_composer_reads_only_scoped_current_versions_not_preconsultations_or_histories(self):
        before = self.conteos()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assert_no_prior_queries(captured)
        self.assertNotContains(response, self.antecedentes["medical_spa"].contenido)
        rows = response.context["mobile_antecedent_history"]
        self.assertEqual([row["registro"].pk for row in rows], [self.versiones[self.empresa.slug].pk])
        self.assertEqual(self.conteos(), before)

    def test_invalid_original_form_returns_bound_text_without_prior_queries_or_writes(self):
        content = "Texto sintético con carácter inválido\x00 que conserva el trabajo"
        before = self.conteos()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.post(self.url(), self.datos(content))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_antecedents_form.html")
        self.assertIsInstance(response.context["form"], AntecedenteAdicionalPacienteForm)
        self.assertTrue(response.context["form"].is_bound)
        self.assertIn("contenido", response.context["form"].errors)
        self.assertEqual(response.context["form"]["contenido"].value(), content)
        self.assert_no_prior_queries(captured)
        self.assertEqual(self.conteos(), before)

    def test_strict_action_prefix_and_foreign_metadata_do_not_reach_other_save_branches(self):
        before = self.conteos()
        for extra in ({"accion": "editar_texto_historia"}, {"accion": "eliminar_historia"}, {"accion": "guardar_evaluacion_integral"},
                      {"formulario": "tricopigmentacion"}, {"tipo_historia": "capilar"}, {"historia_id": self.especiales[(self.empresa.slug, self.ESPECIALES[0])].pk},
                      {"paciente_id": self.pacientes["medical_spa"].pk}, {"antecedente_id": self.antecedentes["medical_spa"].pk},
                      {"version_id": self.versiones["medical_spa"].pk}, {"contenido": "Campo sin prefijo"}, {"app": "0"}):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(self.url(), self.datos(**extra))
            self.assertIn(response.status_code, (403, 404))
            self.assert_no_prior_queries(captured)
        self.assertEqual(self.conteos(), before)

    def test_duplicate_metadata_or_content_and_mismatched_query_are_not_last_value_selected(self):
        before = self.conteos()
        base = QueryDict(mutable=True)
        for key, value in self.datos().items():
            base.setlist(key, [value])
        for key in ("app", "formulario", "accion", "antecedente_adicional-contenido"):
            data = base.copy()
            data.setlist(key, [base[key], base[key]])
            self.assertIn(self.client.post(self.url(), data.urlencode(), content_type="application/x-www-form-urlencoded").status_code, (403, 404))
        self.assertIn(self.client.post(self.url(formulario="evaluacion_integral"), self.datos()).status_code, (403, 404))
        self.assertIn(self.client.get(self.url()+"&formulario=antecedentes").status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_old_mobile_post_without_explicit_antecedent_composer_remains_denied(self):
        before = self.conteos()
        data = self.datos()
        del data["formulario"]
        self.assertEqual(self.client.post(self.url(formulario=None), data).status_code, 403)
        self.assertEqual(self.conteos(), before)

    def test_inconsistent_current_and_version_company_patient_relationships_are_denied(self):
        current = self.antecedentes[self.empresa.slug]
        version = self.versiones[self.empresa.slug]
        for extra in ({"empresa": self.empresas["medical_spa"]}, {"paciente": self.pacientes["medical_spa"]}, {"profesional": self.profesionales["medical_spa"]}):
            AntecedenteAdicionalHistorial.objects.filter(pk=version.pk).update(**extra)
            before = self.conteos()
            response = self.client.get(self.url())
            self.assertIn(response.status_code, (403, 404))
            self.assertNotContains(response, current.contenido, status_code=response.status_code)
            self.assertIn(self.client.post(self.url(), self.datos()).status_code, (403, 404))
            self.assertEqual(self.conteos(), before)
            AntecedenteAdicionalHistorial.objects.filter(pk=version.pk).update(empresa=self.empresa, paciente=self.paciente, profesional=self.profesionales[self.empresa.slug])
        for extra in ({"empresa": self.empresas["medical_spa"]}, {"profesional": self.profesionales["medical_spa"]}):
            AntecedenteAdicionalPaciente.objects.filter(pk=current.pk).update(**extra)
            before = self.conteos()
            self.assertIn(self.client.get(self.url()).status_code, (403, 404))
            self.assertIn(self.client.post(self.url(), self.datos()).status_code, (403, 404))
            self.assertEqual(self.conteos(), before)
            AntecedenteAdicionalPaciente.objects.filter(pk=current.pk).update(empresa=self.empresa, profesional=self.profesionales[self.empresa.slug])

    def test_first_save_creates_original_current_version_actor_professional_and_audit(self):
        started = timezone.now()
        response = self.client.post(self.url(paciente=self.otro_paciente), self.datos())
        self.assertEqual(response.status_code, 302)
        current = AntecedenteAdicionalPaciente.objects.get(paciente=self.otro_paciente)
        version = current.historial.get()
        self.assertEqual(current.contenido, self.datos()["antecedente_adicional-contenido"].strip())
        self.assertEqual(version.texto_anterior, "")
        self.assertEqual(version.texto_nuevo, current.contenido)
        self.assertEqual((current.actualizado_por_id, version.usuario_id), (self.usuario.pk, self.usuario.pk))
        self.assertEqual((current.profesional_id, version.profesional_id), (self.profesionales[self.empresa.slug].pk,)*2)
        self.assertEqual(version.responsable_nombre, self.profesionales[self.empresa.slug].nombre)
        self.assertGreaterEqual(version.fecha, started)
        self.assertTrue(RegistroAuditoria.objects.filter(modelo="antecedenteadicionalpaciente", objeto_id=str(current.pk), usuario=self.usuario, accion="crear", metodo_http="POST").exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modelo="antecedenteadicionalhistorial", objeto_id=str(version.pk), usuario=self.usuario, accion="crear", metodo_http="POST").exists())
        self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])

    def test_update_retains_current_identity_and_prior_versions_same_atomic_branch(self):
        current = self.antecedentes[self.empresa.slug]
        original_content, original_created = current.contenido, current.fecha_creacion
        response = self.client.post(self.url(), self.datos())
        self.assertEqual(response.status_code, 302)
        current.refresh_from_db()
        latest = current.historial.first()
        self.assertEqual(current.fecha_creacion, original_created)
        self.assertEqual(AntecedenteAdicionalPaciente.objects.filter(paciente=self.paciente).count(), 1)
        self.assertEqual(current.historial.count(), 2)
        self.assertEqual(latest.texto_anterior, original_content)
        self.assertEqual(latest.texto_nuevo, self.datos()["antecedente_adicional-contenido"].strip())
        self.assertEqual(latest.usuario_id, self.usuario.pk)
        self.assertTrue(RegistroAuditoria.objects.filter(modelo="antecedenteadicionalpaciente", objeto_id=str(current.pk), accion="modificar", usuario=self.usuario).exists())

    def test_trimmed_noop_does_not_create_version_change_actor_timestamp_or_audit(self):
        current = self.antecedentes[self.empresa.slug]
        timestamp, actor = current.fecha_actualizacion, current.actualizado_por_id
        before = self.conteos()
        response = self.client.post(self.url(), self.datos(" \n"+current.contenido+" \n"))
        self.assertEqual(response.status_code, 302)
        current.refresh_from_db()
        self.assertEqual(current.fecha_actualizacion, timestamp)
        self.assertEqual(current.actualizado_por_id, actor)
        self.assertEqual(self.conteos(), before)

    def test_blank_first_save_is_original_noop_and_does_not_create_empty_record(self):
        before = self.conteos()
        self.assertEqual(self.client.post(self.url(paciente=self.otro_paciente), self.datos(" \n ")).status_code, 302)
        self.assertEqual(self.conteos(), before)

    def test_clear_existing_content_keeps_current_and_records_empty_new_version_not_deletion(self):
        current = self.antecedentes[self.empresa.slug]
        previous = current.contenido
        response = self.client.post(self.url(), self.datos(" \n "))
        self.assertEqual(response.status_code, 302)
        current.refresh_from_db()
        self.assertEqual(current.contenido, "")
        version = current.historial.first()
        self.assertEqual((version.texto_anterior, version.texto_nuevo), (previous, ""))
        self.assertEqual(current.historial.count(), 2)
        self.assertTrue(AntecedenteAdicionalPaciente.objects.filter(pk=current.pk).exists())
        self.assertFalse(RegistroAuditoria.objects.filter(modelo="antecedenteadicionalpaciente", objeto_id=str(current.pk), accion="eliminar").exists())

    def test_version_failure_rolls_back_current_text_actor_and_global_audit(self):
        current = self.antecedentes[self.empresa.slug]
        previous = current.contenido
        before = self.conteos()
        with patch.object(AntecedenteAdicionalHistorial, "save", side_effect=RuntimeError("Fallo sintético de historial")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url(), self.datos())
        current.refresh_from_db()
        self.assertEqual(current.contenido, previous)
        self.assertEqual(self.conteos(), before)

    def test_original_web_antecedent_post_keeps_prefix_service_and_web_redirect(self):
        data = self.datos()
        del data["app"]
        del data["formulario"]
        response = self.client.post(self.url(mobile=False, formulario=None), data)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("app=1", response.url)
        self.assertIn("seccion=antecedentes", response.url)
        current = self.antecedentes[self.empresa.slug]
        current.refresh_from_db()
        self.assertEqual(current.contenido, data["antecedente_adicional-contenido"].strip())
        self.assertEqual(current.historial.count(), 2)

    def test_generic_edit_two_special_types_in_four_clinics_keeps_original_web_forms(self):
        before = self.conteos()
        for empresa in self.empresas.values():
            for tipo in self.ESPECIALES:
                response = self.client.get(self.edit_url(tipo, empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "clinica/mobile_history_form.html")
                self.assertTrue(response.context["mobile_history_generic_only"])
                self.assertIn("no-store", response["Cache-Control"])
                web = self.client.get(self.edit_url(tipo, empresa, mobile=False))
                self.assertEqual(web.status_code, 200)
                self.assertTemplateUsed(web, "clinica/historia_especialidad_form.html")
        self.assertEqual(self.conteos(), before)

    def test_generic_edit_exposes_all_original_seventeen_fields_not_json_or_structured_inputs(self):
        for tipo in self.ESPECIALES:
            response = self.client.get(self.edit_url(tipo))
            form = response.context["form"]
            self.assertIsInstance(form, HistoriaClinicaEspecialidadForm)
            self.assertEqual(list(form.fields), HistoriaClinicaEspecialidadForm.Meta.fields)
            self.assertEqual(len(form.fields), 17)
            self.assertEqual(form.campos_estructurados, [])
            self.assertEqual(form.fields["fecha_atencion"].input_formats, ["%Y-%m-%dT%H:%M"])
            for field in form.fields:
                self.assertContains(response, f'name="{field}"', count=1)
            for field in ("datos_especialidad", "funcion_ojos", "examen_peso", "numero_sesion", "marca_lote"):
                self.assertNotContains(response, f'name="{field}"')

    def test_generic_edit_date_text_professional_state_actor_keep_exact_historical_json(self):
        for tipo in self.ESPECIALES:
            record = self.especiales[(self.empresa.slug, tipo)]
            original_json, created, creator = deepcopy(record.datos_especialidad), record.fecha_creacion, record.creado_por_id
            response = self.client.post(self.edit_url(tipo), self.edit_data(tipo, estado="borrador", observaciones="Observación textual actualizada"))
            self.assertEqual(response.status_code, 302)
            record.refresh_from_db()
            self.assertEqual(record.datos_especialidad, original_json)
            self.assertEqual(timezone.localtime(record.fecha_atencion).strftime("%Y-%m-%dT%H:%M"), "2026-10-03T14:25")
            self.assertEqual(record.plan_tratamiento, "Plan textual actualizado sin tocar JSON")
            self.assertEqual(record.observaciones, "Observación textual actualizada")
            self.assertEqual(record.estado, "borrador")
            self.assertEqual((record.fecha_creacion, record.creado_por_id), (created, creator))
            self.assertEqual(record.actualizado_por_id, self.usuario.pk)
            audit = RegistroAuditoria.objects.get(modelo="historiaclinicaespecialidad", objeto_id=str(record.pk), accion="modificar", metodo_http="POST")
            self.assertNotIn("datos_especialidad", audit.cambios)
            self.assertEqual(audit.usuario_id, self.usuario.pk)
            self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])

    def test_invalid_generic_edit_retains_all_submitted_work_and_original_json(self):
        before = self.conteos()
        for tipo in self.ESPECIALES:
            original_json = deepcopy(self.especiales[(self.empresa.slug, tipo)].datos_especialidad)
            data = self.edit_data(tipo, fecha_atencion="fecha-inválida", observaciones="Trabajo no guardado conservado")
            response = self.client.post(self.edit_url(tipo), data)
            self.assertEqual(response.status_code, 200)
            self.assertIn("fecha_atencion", response.context["form"].errors)
            self.assertTrue(response.context["mobile_history_generic_only"])
            for name in ("motivo_consulta", "antecedentes", "procedimiento", "notas_privadas_doctor", "observaciones", "fecha_atencion"):
                self.assertEqual(response.context["form"][name].value(), data[name])
            self.especiales[(self.empresa.slug, tipo)].refresh_from_db()
            self.assertEqual(self.especiales[(self.empresa.slug, tipo)].datos_especialidad, original_json)
        self.assertEqual(self.conteos(), before)

    def test_editor_only_generic_get_and_invalid_post_query_no_other_histories_or_preconsultations(self):
        self.asignar_rol(puede_editar_historia_clinica=True)
        for tipo in self.ESPECIALES:
            record = self.especiales[(self.empresa.slug, tipo)]
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(self.edit_url(tipo))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["historias_previas"], [])
            self.assertEqual(response.context["preconsultas_tipo"], [])
            self.assertIsNone(response.context["ultima_preconsulta"])
            self.assertFalse(response.context["app_home_disponible"])
            self.assert_no_prior_queries(captured, allow_current_history=True)
            selects = [query["sql"] for query in captured if query["sql"].lstrip().upper().startswith("SELECT") and f'"{HistoriaClinicaEspecialidad._meta.db_table}"' in query["sql"]]
            self.assertTrue(selects)
            self.assertTrue(all(f'"{HistoriaClinicaEspecialidad._meta.db_table}"."id" = {record.pk}' in query for query in selects), selects)
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(self.edit_url(tipo), self.edit_data(tipo, estado="forged"))
            self.assertEqual(response.status_code, 200)
            self.assert_no_prior_queries(captured, allow_current_history=True)

    def test_generic_post_rejects_json_structured_foreign_metadata_and_duplicate_keys(self):
        before = self.conteos()
        for tipo in self.ESPECIALES:
            for extra in ({"datos_especialidad": '{"changed": true}'}, {"numero_sesion": "99"}, {"funcion_ojos": "normal"},
                          {"paciente_id": self.pacientes["medical_spa"].pk}, {"tipo": "capilar"}, {"accion": "eliminar_historia"}):
                self.assertIn(self.client.post(self.edit_url(tipo), self.edit_data(tipo, **extra)).status_code, (403, 404))
            base = QueryDict(mutable=True)
            for key, value in self.edit_data(tipo).items():
                base.setlist(key, [str(value)])
            for key in ("app", "fecha_atencion", "profesional", "estado"):
                data = base.copy()
                data.setlist(key, [base[key], base[key]])
                self.assertIn(self.client.post(self.edit_url(tipo), data.urlencode(), content_type="application/x-www-form-urlencoded").status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_generic_foreign_record_patient_and_professional_cannot_modify_other_company(self):
        before = self.conteos()
        for tipo in self.ESPECIALES:
            foreign = self.especiales[("medical_spa", tipo)]
            self.assertEqual(self.client.get(self.edit_url(tipo, historia=foreign)).status_code, 404)
            self.assertEqual(self.client.post(self.edit_url(tipo, historia=foreign), self.edit_data(tipo)).status_code, 404)
            self.assertEqual(self.client.get(self.edit_url(tipo, paciente=self.otro_paciente)).status_code, 404)
            response = self.client.post(self.edit_url(tipo), self.edit_data(tipo, profesional=self.profesionales["medical_spa"].pk))
            self.assertEqual(response.status_code, 200)
            self.assertIn("profesional", response.context["form"].errors)
        self.assertEqual(self.conteos(), before)

    def test_generic_edit_preserves_non_dict_stale_json_values_without_normalizing(self):
        for tipo in self.ESPECIALES:
            record = self.especiales[(self.empresa.slug, tipo)]
            legacy = ["retired", {"unknown": [None, 7, "Texto antiguo"]}]
            HistoriaClinicaEspecialidad.objects.filter(pk=record.pk).update(datos_especialidad=legacy)
            response = self.client.post(self.edit_url(tipo), self.edit_data(tipo))
            self.assertEqual(response.status_code, 302)
            record.refresh_from_db()
            self.assertEqual(record.datos_especialidad, legacy)

    def test_generic_app_and_web_updates_match_all_original_fields_and_json(self):
        for tipo in self.ESPECIALES:
            original = self.especiales[(self.empresa.slug, tipo)]
            web_record = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo=tipo, datos_especialidad=deepcopy(original.datos_especialidad))
            data = self.edit_data(tipo)
            self.assertEqual(self.client.post(self.edit_url(tipo), data).status_code, 302)
            web_data = {key: value for key, value in data.items() if key != "app"}
            web_response = self.client.post(self.edit_url(tipo, historia=web_record, mobile=False), web_data)
            self.assertEqual(web_response.status_code, 302)
            self.assertNotIn("app=1", web_response.url)
            original.refresh_from_db()
            web_record.refresh_from_db()
            for name in HistoriaClinicaEspecialidadForm.Meta.fields:
                self.assertEqual(getattr(original, name), getattr(web_record, name), name)
            self.assertEqual(original.datos_especialidad, web_record.datos_especialidad)

    def test_generic_and_antecedent_actions_do_not_create_recipes_inventory_patients_or_citas(self):
        models = (HistoriaClinicaEspecialidad, ClasificacionAlopecia, PreconsultaClinica, Paciente, ProfesionalSalud, RecetaMedica, MovimientoInventario, Factura, CitaCliente)
        before = tuple(model.objects.count() for model in models)
        self.assertEqual(self.client.post(self.url(), self.datos()).status_code, 302)
        for tipo in self.ESPECIALES:
            self.assertEqual(self.client.post(self.edit_url(tipo), self.edit_data(tipo)).status_code, 302)
        self.assertEqual(tuple(model.objects.count() for model in models), before)

    def test_legacy_expediente_only_can_use_new_composer_and_generic_editor_with_safe_navigation(self):
        self.asignar_rol(granular=False, puede_clinica=True, puede_expediente_clinico=True)
        for url in (self.url(), *(self.edit_url(tipo) for tipo in self.ESPECIALES)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            self.assertIn("/app/modulos/", response.context["app_modules_url"])
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True)
        with self.assertRaises(PermissionDenied):
            self.directo()
        with self.assertRaises(PermissionDenied):
            self.directo(edit=True)

    def test_antecedent_picker_requires_list_and_both_flags_without_changing_normal_directory(self):
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        response = self.client.get(picker, {"app": "1", "destino": "antecedentes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["mobile_patient_antecedents_mode"])
        self.assertFalse(response.context["can_view_patient"])
        self.assertContains(response, self.url().replace("&", "&amp;"))
        normal = self.client.get(picker, {"app": "1"})
        self.assertFalse(normal.context.get("mobile_patient_antecedents_mode"))
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True)
        response = self.client.get(picker, {"app": "1", "destino": "antecedentes"})
        self.assertFalse(response.context.get("mobile_patient_antecedents_mode"))
        self.asignar_rol(puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        self.assertIn(self.client.get(picker, {"app": "1", "destino": "antecedentes"}).status_code, (302, 403))
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_antecedent_selector_query_and_pagination_preserve_intent_and_tenant(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        Paciente.objects.bulk_create([Paciente(empresa=self.empresa, nombre=f"Selector antecedente {index:02d}", expediente_codigo=f"MA-SEL-{index:02d}") for index in range(27)])
        before = self.conteos()
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        for page in ("1", "2"):
            response = self.client.get(picker, {"app": "1", "destino": "antecedentes", "q": "Selector antecedente", "page": page})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_patient_antecedents_mode"])
            self.assertEqual(response.context["pacientes"].paginator.count, 27)
            self.assertContains(response, 'name="destino" value="antecedentes"')
            self.assertContains(response, "destino=antecedentes")
            self.assertContains(response, "q=Selector%20antecedente")
            self.assertNotContains(response, self.pacientes["medical_spa"].nombre)
            for paciente in response.context["pacientes"]:
                self.assertContains(response, self.url(paciente=paciente).replace("&", "&amp;"))
        self.assertEqual(self.conteos(), before)

    def test_antecedent_hub_card_requires_both_flags_and_patient_list_for_mobile_url(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for grants, state in (({"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True, "puede_ver_pacientes": True}, "disponible"),
                              ({"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True}, "pendiente"),
                              ({"puede_ver_historia_clinica": True}, None), ({"puede_editar_historia_clinica": True}, None)):
            self.asignar_rol(**grants)
            response = self.client.get(hub)
            self.assertEqual(response.status_code, 200)
            card = next((item for item in response.context["modulos_app"] if item["key"] == "antecedentes"), None)
            self.assertEqual(card is not None, state is not None)
            if card:
                self.assertEqual(card["estado"], state)
                self.assertEqual(bool(card["url"]), state == "disponible")
                if card["url"]:
                    self.assertIn("app=1&destino=antecedentes", card["url"])
                    self.assertEqual(self.client.get(card["url"]).status_code, 200)

    def test_antecedent_mutation_retains_original_csrf_protection(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        self.assertEqual(protected.get(self.url()).status_code, 200)
        before = self.conteos()
        response = protected.post(self.url(), self.datos())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", args=[self.empresa.slug]))
        self.assertEqual(self.conteos(), before)
        token = protected.cookies["csrftoken"].value
        self.assertEqual(protected.post(self.url(), self.datos(), HTTP_X_CSRFTOKEN=token).status_code, 302)
