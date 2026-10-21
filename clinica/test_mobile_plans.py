"""The mobile plan log reuses the original text form and append-only save."""
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM, NotificacionCitaWhatsApp, OpcionServicioAgenda
from facturacion.models import Cliente, Factura, MovimientoInventario, MovimientoLoteBodega, PagoFactura, Producto
from . import views
from .forms import PlanTratamientoPacienteForm
from .models import CitaClinica, ConfiguracionClinica, ExpedienteEvento, HistoriaClinicaEspecialidad, Paciente, PlanTratamientoPaciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica, ServicioClinico, TratamientoPaciente


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePlansIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas, cls.pacientes, cls.profesionales, cls.planes = {}, {}, {}, {}
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Planes QA {slug}", slug=slug, rtn=f"MPLAN-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            for activo in (True, False):
                cls.pacientes[(slug, activo)] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente planes {slug} {activo}", expediente_codigo=f"MPLAN-{index}-{activo}", activo=activo)
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional planes {slug}", activo=True)
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[(cls.empresa.slug, True)]
        cls.rol = RolSistema.objects.create(nombre="Planes móviles", codigo="mobile-plans-full", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_planes_tratamiento=True, puede_editar_planes_tratamiento=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-plans", password="test-only", first_name="Autor", last_name="Sintético", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        for slug, empresa in cls.empresas.items():
            ProfesionalSalud.objects.filter(pk=cls.profesionales[slug].pk).update(usuario=cls.usuario)
            for index in range(3):
                cls.planes[(slug, index)] = PlanTratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[(slug, True)], texto=f"Plan anterior sintético {slug} {index}\nTexto completo conservado.", profesional=cls.profesionales[slug], creado_por=cls.usuario)
            PlanTratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[(slug, False)], texto=f"Plan paciente inactivo {slug}", creado_por=cls.usuario)

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("clinica.views.EmailMultiAlternatives", "clinica.views.enviar_documento_whatsapp", "clinica.views.enviar_plantilla_preconsulta_whatsapp", "clinica.views.subir_documento_whatsapp"):
            patcher = patch(target)
            transport = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(transport.assert_not_called)

    def asignar_rol(self, empresa=None, granular=True, **permissions):
        rol = RolSistema.objects.create(nombre="Planes restringidos", codigo=f"mobile-plans-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, empresa=None, paciente=None, composer=False, mobile=True, **parameters):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get((empresa.slug, True), self.paciente)
        route = reverse("clinica_plan_tratamiento_crear_app" if composer else "clinica_planes_tratamiento_paciente", args=[empresa.slug, paciente.pk])
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        return route+("?"+urlencode(query) if query else "")

    def datos(self, texto="  Plan nuevo sintético\nSegunda línea conservada.  ", **extra):
        data = {"app": "1", "texto": texto}
        data.update(extra)
        return data

    def side_counts(self):
        return tuple(model.objects.count() for model in (Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente, CitaClinica, CitaCliente, OpcionServicioAgenda, NotificacionCitaWhatsApp, ExpedienteEvento, HistoriaClinicaEspecialidad, PreconsultaClinica, RecetaMedica, Cliente, Producto, Factura, PagoFactura, MovimientoInventario, MovimientoLoteBodega, ConfiguracionClinica, ConfiguracionCRM, ConfiguracionAvanzadaEmpresa))

    def snapshot(self):
        return (list(PlanTratamientoPaciente.objects.order_by("pk").values()), self.side_counts(), RegistroAuditoria.objects.count())

    def directo(self, empresa=None, paciente=None, composer=False, method="get", data=None, url=None):
        url = url or self.url(empresa, paciente, composer=composer)
        body = self.datos() if data is None else data
        if isinstance(body, QueryDict):
            body = {key: body.getlist(key) for key in body}
        request = RequestFactory().post(url, body) if method == "post" else RequestFactory().generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        match = resolve(urlsplit(url).path)
        return match.func(request, **match.kwargs)

    def assert_no_private_queries(self, queries, include_professional=True):
        forbidden = (PlanTratamientoPaciente, HistoriaClinicaEspecialidad, PreconsultaClinica, ExpedienteEvento, TratamientoPaciente)
        if include_professional:
            forbidden += (ProfesionalSalud,)
        selects = [item["sql"] for item in queries if item["sql"].lstrip().upper().startswith("SELECT")]
        for model in forbidden:
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects), model._meta.label)

    def test_four_companies_mobile_list_and_composer_keep_original_web_and_do_not_seed(self):
        before = self.snapshot()
        with patch.object(views, "asegurar_profesionales_agenda_base") as seed:
            for empresa in self.empresas.values():
                for composer, template in ((False, "clinica/mobile_plan_list.html"), (True, "clinica/mobile_plan_form.html")):
                    response = self.client.get(self.url(empresa, composer=composer))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, template)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotContains(response, "<table")
                web = self.client.get(self.url(empresa, mobile=False))
                self.assertTemplateUsed(web, "clinica/planes_tratamiento_paciente.html")
                self.assertTemplateNotUsed(web, "clinica/mobile_plan_list.html")
            seed.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_composer_uses_only_original_text_bound_field_and_canonical_post_url(self):
        response = self.client.get(self.url(composer=True))
        form = response.context["form"]
        self.assertIsInstance(form, PlanTratamientoPacienteForm)
        original = self.client.get(self.url(mobile=False)).context["form"]
        self.assertEqual(list(form.fields), ["texto"])
        self.assertIs(type(form.fields["texto"].widget), type(original.fields["texto"].widget))
        self.assertEqual(form.fields["texto"].widget.attrs, original.fields["texto"].widget.attrs)
        self.assertContains(response, 'name="texto"', count=1)
        self.assertContains(response, 'name="app" value="1"', count=1)
        self.assertContains(response, 'name="csrfmiddlewaretoken"', count=1)
        self.assertContains(response, f'action="{self.url()}"')
        self.assertNotContains(response, 'name="accion"')

    def test_composer_get_and_invalid_post_never_read_plans_histories_or_default_professional(self):
        self.asignar_rol(puede_editar_planes_tratamiento=True)
        # Even a damaged pre-existing log is irrelevant to writing a new plan.
        PlanTratamientoPaciente.objects.filter(pk=self.planes[(self.empresa.slug, 0)].pk).update(profesional=self.profesionales["medical_spa"])
        before = self.snapshot()
        with patch.object(views, "_profesional_predeterminado_usuario") as default_professional:
            for method in ("get", "post"):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.url(composer=True)) if method == "get" else self.client.post(self.url(), self.datos(texto="   \n  "))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "clinica/mobile_plan_form.html")
                self.assert_no_private_queries(queries)
                self.assertNotContains(response, self.planes[(self.empresa.slug, 0)].texto.splitlines()[0])
            default_professional.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_writer_only_has_composer_and_post_but_no_history_patient_or_calendar_grant(self):
        self.asignar_rol(puede_editar_planes_tratamiento=True)
        response = self.client.get(self.url(composer=True))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_plans"])
        self.assertTrue(response.context["can_create_plan"])
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        for url in (self.url(), reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1", reverse("clinica_pacientes", args=[self.empresa.slug])+"?app=1"):
            self.assertIn(self.client.get(url).status_code, (302, 403))
        with self.assertRaises(PermissionDenied):
            self.directo()
        result = self.client.post(self.url(), self.datos())
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.url, self.url(composer=True))

    def test_reader_only_hides_write_actions_and_blocks_direct_post_and_composer_before_queries(self):
        self.asignar_rol(puede_ver_planes_tratamiento=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_create_plan"])
        self.assertNotContains(response, self.url(composer=True))
        before = self.snapshot()
        for composer, method in ((True, "get"), (False, "post")):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(PermissionDenied):
                    self.directo(composer=composer, method=method)
            self.assert_no_private_queries(queries)
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_granular_and_legacy_flags_never_authorize_plans(self):
        for granular, permissions in ((True, {"puede_tratamientos_clinicos": True, "puede_expediente_clinico": True, "puede_pacientes": True}), (True, {"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True}), (False, {"puede_clinica": True, "puede_tratamientos_clinicos": True}), (False, {"puede_clinica": True, "puede_expediente_clinico": True})):
            self.asignar_rol(granular=granular, **permissions)
            before = self.snapshot()
            for composer, method in ((False, "get"), (True, "get"), (False, "post")):
                with self.assertRaises(PermissionDenied):
                    self.directo(composer=composer, method=method)
            self.assertEqual(self.snapshot(), before)

    def test_legacy_patients_reads_and_patients_plus_expediente_writes_without_treatment_flag(self):
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True)
        read = self.client.get(self.url())
        self.assertEqual(read.status_code, 200)
        self.assertFalse(read.context["can_create_plan"])
        with self.assertRaises(PermissionDenied):
            self.directo(method="post")
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True, puede_expediente_clinico=True)
        self.assertEqual(self.client.get(self.url(composer=True)).status_code, 200)
        result = self.client.post(self.url(), self.datos())
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.url, self.url())
        self.assertEqual(self.client.get(self.url()).context["can_create_plan"], True)

    def test_direct_guards_company_membership_module_active_license_and_clinical_solution(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for composer in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(composer=composer)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for invalid in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}, {"tipo_solucion": "comercial"}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**invalid)
            for composer in (False, True):
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(composer=composer)
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None, tipo_solucion="clinica")
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        for composer in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(other, composer=composer)

    def test_other_clinic_app_flag_keeps_original_web_list_and_new_composer_is_unavailable(self):
        other = Empresa.objects.create(nombre="Otra clínica planes", slug="other_mobile_plans", rtn="MPLAN-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=other, modulo=self.modulo)
        self.usuario.empresas_acceso.add(other)
        paciente = Paciente.objects.create(empresa=other, nombre="Paciente otra clínica", expediente_codigo="MPLAN-OTHER")
        app = self.client.get(self.url(other, paciente))
        web = self.client.get(self.url(other, paciente, mobile=False))
        self.assertEqual(app.status_code, web.status_code)
        self.assertTemplateUsed(app, "clinica/planes_tratamiento_paciente.html")
        self.assertTemplateNotUsed(app, "clinica/mobile_plan_list.html")
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(other, paciente, composer=True)

    def test_inactive_patient_remains_accessible_for_original_plan_append(self):
        paciente = self.pacientes[(self.empresa.slug, False)]
        self.assertEqual(self.client.get(self.url(paciente=paciente)).status_code, 200)
        self.assertEqual(self.client.get(self.url(paciente=paciente, composer=True)).status_code, 200)
        result = self.client.post(self.url(paciente=paciente), self.datos(texto="Plan nuevo para expediente inactivo"))
        self.assertEqual(result.status_code, 302)
        self.assertTrue(PlanTratamientoPaciente.objects.filter(paciente=paciente, texto="Plan nuevo para expediente inactivo").exists())

    def test_patient_scope_foreign_missing_and_overflow_paths_do_not_read_or_write_plans(self):
        foreign = self.pacientes[("medical_spa", True)]
        before = self.snapshot()
        for patient_id in (foreign.pk, 9223372036854775807, 9223372036854775808, 99999999999999999999999999999):
            for composer in (False, True):
                path = reverse("clinica_plan_tratamiento_crear_app" if composer else "clinica_planes_tratamiento_paciente", args=[self.empresa.slug, patient_id])+"?app=1"
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(url=path)
        self.assertEqual(self.snapshot(), before)

    def test_company_and_patient_scoped_list_never_contains_other_patient_or_company(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            rows = list(response.context["planes"])
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row.empresa_id == empresa.pk and row.paciente_id == self.pacientes[(empresa.slug, True)].pk for row in rows))
            self.assertNotContains(response, f"Plan paciente inactivo {empresa.slug}")
            for other in self.empresas.values():
                if other.pk != empresa.pk:
                    self.assertNotContains(response, f"Plan anterior sintético {other.slug}")

    def test_search_and_stable_server_pagination_cover_full_patient_log(self):
        plans = [PlanTratamientoPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, texto=f"Texto paginado coincidente {index}", creado_por=self.usuario) for index in range(23)]
        fixed = timezone.now()
        PlanTratamientoPaciente.objects.filter(pk__in=[row.pk for row in plans]).update(fecha_creacion=fixed)
        first = self.client.get(self.url(q="Texto paginado coincidente", page="1"))
        second = self.client.get(self.url(q="Texto paginado coincidente", page="2"))
        ids1 = [row.pk for row in first.context["planes"]]
        ids2 = [row.pk for row in second.context["planes"]]
        self.assertEqual((len(ids1), len(ids2)), (20, 3))
        self.assertEqual(ids1+ids2, [row.pk for row in reversed(plans)])
        self.assertEqual(first.context["planes"].paginator.count, 23)
        self.assertContains(first, "q=Texto+paginado+coincidente")
        self.assertContains(first, "page=2")
        self.assertEqual([row.pk for row in self.client.get(self.url(q="Texto paginado coincidente", page="1")).context["planes"]], ids1)

    def test_valid_form_all_four_appends_one_plan_without_overwriting_or_side_effects(self):
        for empresa in self.empresas.values():
            previous = list(PlanTratamientoPaciente.objects.order_by("pk").values())
            side = self.side_counts()
            text = f"  Plan nuevo sintético {empresa.slug}\nNueva línea de prueba.  "
            start = timezone.now()
            result = self.client.post(self.url(empresa), self.datos(texto=text))
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result.url, self.url(empresa))
            plan = PlanTratamientoPaciente.objects.get(empresa=empresa, texto=text.strip())
            self.assertEqual(plan.paciente_id, self.pacientes[(empresa.slug, True)].pk)
            self.assertEqual(plan.profesional_id, self.profesionales[empresa.slug].pk)
            self.assertEqual(plan.creado_por_id, self.usuario.pk)
            self.assertEqual(plan.profesional_nombre, self.profesionales[empresa.slug].nombre)
            self.assertEqual(plan.autor_nombre, self.usuario.get_full_name())
            self.assertGreaterEqual(plan.fecha_creacion, start)
            self.assertEqual(list(PlanTratamientoPaciente.objects.filter(pk__in=[row["id"] for row in previous]).order_by("pk").values()), previous)
            self.assertEqual(PlanTratamientoPaciente.objects.count(), len(previous)+1)
            self.assertEqual(self.side_counts(), side)

    def test_original_form_trims_preserves_line_breaks_and_does_not_invent_text_maximum(self):
        text = "  Primera línea\n"+("Texto sintético completo. "*140)+"\nÚltima línea  "
        form = PlanTratamientoPacienteForm({"texto": text})
        self.assertTrue(form.is_valid())
        result = self.client.post(self.url(), self.datos(texto=text))
        self.assertEqual(result.status_code, 302)
        plan = PlanTratamientoPaciente.objects.get(texto=form.cleaned_data["texto"])
        self.assertGreater(len(plan.texto), 1200)
        self.assertEqual(plan.texto, text.strip())
        response = self.client.get(self.url(q="Última línea"))
        self.assertContains(response, "Última línea")
        self.assertEqual(list(response.context["planes"])[0].texto, text.strip())

    def test_global_audit_actor_time_full_model_text_and_original_truncated_snapshot(self):
        text = "Texto de auditoría "*100
        started = timezone.now()
        self.assertEqual(self.client.post(self.url(), self.datos(texto=text)).status_code, 302)
        plan = PlanTratamientoPaciente.objects.get(texto=text.strip())
        audit = RegistroAuditoria.objects.get(empresa=self.empresa, modelo=plan._meta.model_name, objeto_id=str(plan.pk), accion="crear")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertGreaterEqual(audit.fecha, started)
        self.assertEqual(audit.metodo_http, "POST")
        self.assertEqual(audit.cambios["texto"]["nuevo"], plan.texto[:1200]+"...")
        self.assertEqual(audit.cambios["profesional"]["nuevo"], plan.profesional_id)
        self.assertEqual(audit.cambios["creado_por"]["nuevo"], self.usuario.pk)
        self.assertEqual(audit.cambios["autor_nombre"]["nuevo"], self.usuario.get_full_name())
        self.assertEqual(audit.cambios["fecha_creacion"]["nuevo"], plan.fecha_creacion.isoformat())

    def test_web_and_app_save_same_original_values_with_original_web_redirect(self):
        self.client.post(self.url(), self.datos(texto="  Plan equivalente app  "))
        web = self.client.post(self.url(mobile=False), {"texto": "  Plan equivalente web  "})
        self.assertEqual(web.status_code, 302)
        self.assertEqual(web.url, self.url(mobile=False))
        app_plan = PlanTratamientoPaciente.objects.get(texto="Plan equivalente app")
        web_plan = PlanTratamientoPaciente.objects.get(texto="Plan equivalente web")
        for field in ("empresa_id", "paciente_id", "profesional_id", "creado_por_id", "profesional_nombre", "autor_nombre"):
            self.assertEqual(getattr(app_plan, field), getattr(web_plan, field))
        self.assertTemplateUsed(self.client.get(web.url), "clinica/planes_tratamiento_paciente.html")

    def test_without_linked_active_professional_uses_original_nullable_and_actor_snapshot(self):
        ProfesionalSalud.objects.filter(empresa=self.empresa).update(activo=False)
        before = ProfesionalSalud.objects.count()
        result = self.client.post(self.url(), self.datos(texto="Plan sin profesional disponible"))
        self.assertEqual(result.status_code, 302)
        plan = PlanTratamientoPaciente.objects.get(texto="Plan sin profesional disponible")
        self.assertIsNone(plan.profesional_id)
        self.assertEqual(plan.profesional_nombre, "")
        self.assertEqual(plan.autor_nombre, self.usuario.get_full_name())
        self.assertEqual(plan.responsable_display, self.usuario.get_full_name())
        self.assertEqual(ProfesionalSalud.objects.count(), before)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_existing_responsible_and_author_snapshots_survive_later_names_and_nullable_actor(self):
        plan = self.planes[(self.empresa.slug, 0)]
        expected = plan.responsable_display
        ProfesionalSalud.objects.filter(pk=plan.profesional_id).update(nombre="Nombre actualizado, no sustituye snapshot")
        PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(creado_por=None)
        response = self.client.get(self.url(q="hospital_mia 0"))
        self.assertContains(response, expected)
        plan.refresh_from_db()
        self.assertEqual(plan.responsable_display, expected)
        self.assertContains(response, plan.autor_nombre)

    def test_author_with_another_primary_company_is_a_legitimate_multicompany_actor(self):
        actor = Usuario.objects.create_user(username="plans-multicompany-author", empresa=self.empresas["medical_spa"], first_name="Autor", last_name="Multicompañía")
        actor.empresas_acceso.add(self.empresa)
        plan = PlanTratamientoPaciente.objects.create(empresa=self.empresa, paciente=self.paciente, texto="Texto autor multiempresa legítimo", creado_por=actor)
        response = self.client.get(self.url(q=plan.texto))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.pk for row in response.context["planes"]], [plan.pk])
        self.assertContains(response, actor.get_full_name())

    def test_invalid_empty_whitespace_and_missing_text_remain_bound_without_history_or_writes(self):
        for data in (self.datos(texto=""), self.datos(texto="  \n\t  "), {"app": "1"}):
            before = self.snapshot()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url(), data)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_plan_form.html")
            self.assertTrue(response.context["form"].is_bound)
            self.assertIn("texto", response.context["form"].errors)
            self.assertEqual(response.context["form"]["texto"].value(), data.get("texto"))
            self.assert_no_private_queries(queries)
            self.assertEqual(self.snapshot(), before)

    def test_html_text_is_escaped_in_existing_log_and_new_bound_forms(self):
        text = '<script>alert("synthetic")</script>\nTexto normal & conservado'
        result = self.client.post(self.url(), self.datos(texto=text))
        self.assertEqual(result.status_code, 302)
        response = self.client.get(self.url(q="synthetic"))
        self.assertNotContains(response, '<script>alert("synthetic")</script>')
        self.assertContains(response, "&lt;script&gt;")
        self.assertEqual(PlanTratamientoPaciente.objects.get(texto=text).texto, text)

    def test_closed_list_and_composer_queries_reject_unknown_duplicates_and_bad_pages(self):
        for composer, query in ((False, "app=1&app=1"), (False, "app=1&q=a&q=b"), (False, "app=1&unknown=1"), (True, "app=1&page=1"), (True, "app=1&app=0"), (True, "app=0"), (True, "")):
            before = self.snapshot()
            url = self.url(composer=composer).split("?")[0]+"?"+query
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(url=url)
            self.assertEqual(self.snapshot(), before)
        for page in ("", "0", "-1", "01", "1.0", "1e2", "١", "9223372036854775808", "999999999999999999999999"):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(url=self.url(page=page))

    def test_closed_post_rejects_duplicate_fields_unknown_metadata_and_mismatched_app(self):
        before = self.snapshot()
        cases = []
        for field in ("app", "texto", "csrfmiddlewaretoken"):
            data = QueryDict(mutable=True)
            data.update(self.datos())
            data.setlist(field, ["1", "1"] if field != "texto" else ["Plan A", "Plan B"])
            cases.append(data)
        for field in ("empresa", "paciente", "profesional", "creado_por", "plan_id", "accion", "fecha_creacion", "secretack", "initial-texto"):
            cases.append(self.datos(**{field: "1"}))
        cases.extend(({"texto": "App ausente"}, self.datos(app="0")))
        for data in cases:
            with self.assertRaises(PermissionDenied):
                self.directo(method="post", data=data)
        for url in (self.url().split("?")[0]+"?app=0", self.url().split("?")[0]+"?app=1&app=1"):
            with self.assertRaises(PermissionDenied):
                self.directo(method="post", data=self.datos(), url=url)
        self.assertEqual(self.snapshot(), before)

    def test_composer_is_get_head_only_and_uses_original_post_not_a_parallel_history_save(self):
        before = self.snapshot()
        for method in ("head", "post", "put", "delete"):
            response = getattr(self.client, method)(self.url(composer=True), self.datos() if method == "post" else {})
            if method == "head":
                self.assertEqual(response.status_code, 200)
            else:
                # The original permission middleware can deny before the decorator.
                self.assertIn(response.status_code, (302, 403, 405))
                self.assertEqual(self.directo(composer=True, method=method).status_code, 405)
        self.assertEqual(self.snapshot(), before)

    def test_imported_foreign_plan_company_patient_or_professional_denies_before_search_page(self):
        plan = self.planes[(self.empresa.slug, 0)]
        for field, foreign in (("empresa_id", self.empresas["medical_spa"].pk), ("profesional_id", self.profesionales["medical_spa"].pk)):
            original = getattr(plan, field)
            try:
                PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(**{field: foreign})
                before = self.snapshot()
                response = self.client.get(self.url(q="No coincide ningún plan", page="100"))
                self.assertIn(response.status_code, (403, 404))
                self.assertNotContains(response, "Texto completo conservado", status_code=response.status_code)
                self.assertEqual(self.snapshot(), before)
            finally:
                PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(**{field: original})
        # Another patient's damaged log must not block this healthy profile.
        foreign_patient = self.pacientes[("medical_spa", True)]
        original = plan.paciente_id
        try:
            PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(paciente=foreign_patient)
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(plan.pk, [row.pk for row in response.context["planes"]])
            self.assertEqual(self.client.get(self.url(paciente=foreign_patient)).status_code, 404)
        finally:
            PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(paciente_id=original)

    def test_orphaned_related_professional_or_author_is_denied_and_unrelated_patient_does_not_block(self):
        plan = self.planes[(self.empresa.slug, 0)]
        for field in ("paciente_id", "profesional_id", "creado_por_id"):
            original = getattr(plan, field)
            with connection.constraint_checks_disabled():
                try:
                    PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(**{field: 2147483647})
                    before = self.snapshot()
                    response = self.client.get(self.url(q="No coincide", page="100"))
                    if field == "paciente_id":
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(list(response.context["planes"]), [])
                    else:
                        self.assertIn(response.status_code, (403, 404))
                    self.assertEqual(self.snapshot(), before)
                finally:
                    PlanTratamientoPaciente.objects.filter(pk=plan.pk).update(**{field: original})

    def test_methods_csrf_and_logout_do_not_allow_unconfirmed_backend_writes(self):
        before = self.snapshot()
        self.assertEqual(self.client.head(self.url()).status_code, 200)
        for method in ("put", "delete", "patch"):
            self.assertIn(getattr(self.client, method)(self.url(), {}).status_code, (403, 405))
        self.assertEqual(self.snapshot(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url(composer=True)).status_code, 200)
        self.assertIn(protected.post(self.url(), self.datos()).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        token = protected.cookies["csrftoken"].value
        self.assertEqual(protected.post(self.url(), self.datos(), HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.client.logout()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(), self.datos()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_hub_is_pending_without_patient_list_and_links_selector_with_real_read_or_write_access(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for permission, mode in (("puede_ver_planes_tratamiento", "planes"), ("puede_editar_planes_tratamiento", "crear_plan")):
            for patient_list in (False, True):
                self.asignar_rol(**{permission: True, "puede_ver_pacientes": patient_list})
                response = self.client.get(hub)
                self.assertEqual(response.status_code, 200)
                card = next(item for item in response.context["modulos_app"] if item["key"] == "planes_tratamiento")
                self.assertEqual(card["estado"], "disponible" if patient_list else "pendiente")
                if patient_list:
                    self.assertEqual(card["url"], reverse("clinica_pacientes", args=[self.empresa.slug])+f"?app=1&destino={mode}")
                    self.assertEqual(self.client.get(card["url"]).status_code, 200)
                else:
                    self.assertFalse(card.get("url"))
                self.assertFalse(response.context["app_home_disponible"])

    def test_patient_selector_uses_read_or_writer_route_without_general_data_or_history_permission(self):
        base = reverse("clinica_pacientes", args=[self.empresa.slug])
        for permission, mode, composer in (("puede_ver_planes_tratamiento", "planes", False), ("puede_editar_planes_tratamiento", "crear_plan", True)):
            self.asignar_rol(puede_ver_pacientes=True, **{permission: True})
            response = self.client.get(base+f"?app=1&destino={mode}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["mobile_patient_plan_mode"], mode)
            self.assertContains(response, self.url(composer=composer))
            self.assertNotContains(response, reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1")
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            self.assertEqual(self.client.get(self.url(composer=composer)).status_code, 200)

    def test_selector_search_and_pagination_keep_intent_but_unauthorized_mode_grants_nothing(self):
        base = reverse("clinica_pacientes", args=[self.empresa.slug])
        for index in range(27):
            Paciente.objects.create(empresa=self.empresa, nombre=f"Selector plan sintético {index:02}", expediente_codigo=f"MPLAN-PICK-{index}")
        self.asignar_rol(puede_ver_pacientes=True, puede_editar_planes_tratamiento=True)
        result = self.client.get(base+"?app=1&destino=crear_plan&q=Selector+plan+sintético")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.context["mobile_patient_plan_mode"], "crear_plan")
        self.assertContains(result, "destino=crear_plan")
        self.assertContains(result, "page=2")
        self.assertContains(result, "q=Selector")
        unauthorized = self.client.get(base+"?app=1&destino=planes")
        self.assertFalse(unauthorized.context.get("mobile_patient_plan_mode"))
        self.assertNotContains(unauthorized, self.url())
        self.asignar_rol(puede_editar_planes_tratamiento=True)
        self.assertIn(self.client.get(base+"?app=1&destino=crear_plan").status_code, (302, 403))

    def test_patient_detail_plan_actions_use_plan_permissions_independently_of_general_data(self):
        detail = reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
        self.asignar_rol(puede_ver_datos_generales_paciente=True)
        response = self.client.get(detail)
        self.assertNotContains(response, self.url())
        self.assertNotContains(response, self.url(composer=True))
        self.asignar_rol(puede_ver_datos_generales_paciente=True, puede_editar_planes_tratamiento=True)
        response = self.client.get(detail)
        self.assertContains(response, self.url(composer=True))
        self.assertNotContains(response, self.url())

    def test_capability_helper_is_pure_and_preserves_exact_legacy_and_granular_actions(self):
        from .mobile_plans import planes_disponibles_app
        before = self.snapshot()
        request = RequestFactory().get(self.url())
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertEqual(planes_disponibles_app(request, self.empresa), {"ver": True, "crear": True})
        with CaptureQueriesContext(connection) as queries:
            planes_disponibles_app(request, self.empresa)
        self.assert_no_private_queries(queries)
        self.assertEqual(self.snapshot(), before)
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertEqual(planes_disponibles_app(request, self.empresa), {"ver": True, "crear": False})
