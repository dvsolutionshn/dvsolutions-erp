"""Disponibilidad móvil: formulario, validaciones y operaciones originales.

Datos sintéticos y TestCase independiente; ninguna agenda, mensaje o cuenta real.
La ausencia de módulos clínicos/agenda es intencional: las rutas originales de
disponibilidad tienen esa excepción y la app no concede permisos nuevos.
"""
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from clinica.models import Paciente, ProfesionalSalud, ServicioClinico
from contabilidad.models import CuentaContable
from core.models import Empresa, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Cliente, InventarioProducto, Producto
from . import views
from .forms import BloqueoDisponibilidadMedicaForm
from .models import BloqueoDisponibilidadMedica, CitaCliente


class _AvailabilityMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.names, self.links = [], [], []
        self.post = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.post = attrs.get("method", "get").lower() == "post"
            if self.post:
                self.forms.append(attrs)
        if self.post and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.names.append(attrs["name"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.post = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAvailabilityTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = ("profesional", "fecha", "hora_inicio", "hora_inicio_periodo", "fecha_fin",
              "hora_fin", "hora_fin_periodo", "dia_completo", "motivo")
    TEMPLATE = "crm/mobile_availability_form.html"
    FECHA = date(2026, 11, 18)

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Disponibilidad QA {slug}", slug=slug,
            rtn=f"APP-AVAIL-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Empresa externa disponibilidad QA", slug="availability-external-qa",
            rtn="APP-AVAIL-EXT", tipo_solucion="clinica", estado_licencia="activa")
        cls.roles = {name: RolSistema.objects.create(nombre=f"Disponibilidad {name} QA", codigo=f"availability-{name}-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular",
            # The original management helper still needs its own permission;
            # Editar citas does not independently grant management of others.
            **({"puede_editar_citas": True, "puede_ver_calendario": True, "puede_citas": True}
               if name == "granular" else {"puede_citas": True}))
            for name in ("legacy", "granular")}
        cls.users = {name: Usuario.objects.create_user(username=f"availability-{name}-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=role) for name, role in cls.roles.items()}
        for actor in cls.users.values():
            actor.empresas_acceso.add(*cls.empresas.values())
        cls.supervisor = Usuario.objects.create_superuser(username="availability-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.company_admin = Usuario.objects.create_user(username="availability-admin-qa", password="test-only",
            empresa=cls.empresa, es_administrador_empresa=True)
        cls.company_admin.empresas_acceso.add(*cls.empresas.values())
        cls.no_permission = Usuario.objects.create_user(username="availability-none-qa", password="test-only", empresa=cls.empresa)
        cls.foreign_user = Usuario.objects.create_user(username="availability-foreign-qa", password="test-only",
            empresa=cls.external, rol_sistema=cls.roles["granular"])
        cls.profesionales, cls.pacientes, cls.clientes, cls.servicios, cls.productos, cls.citas = {}, {}, {}, {}, {}, {}
        for index, empresa in enumerate((*cls.empresas.values(), cls.external), 1):
            slug = empresa.slug
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa,
                nombre=f"Luis Gonzales disponibilidad sintética {index}", telefono="00000000")
            cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente sintético disponibilidad {index}",
                expediente_codigo=f"AVAIL-{index}", identidad=f"0801202600{index:03d}")
            cls.clientes[slug] = Cliente.objects.create(empresa=empresa, nombre=f"Cliente sintético disponibilidad {index}", rtn=f"AVAIL-CLIENT-{index}")
            cls.servicios[slug] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Consulta disponibilidad QA {index}", duracion_minutos=60)
            cls.productos[slug] = Producto.objects.create(empresa=empresa, nombre=f"Producto intacto disponibilidad QA {index}",
                codigo=f"AVAIL-P-{index}", precio="25.00")
            InventarioProducto.objects.create(empresa=empresa, producto=cls.productos[slug], existencias="18.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"AVAIL-ACCOUNT-{index}", nombre=f"Cuenta intacta {index}", tipo="activo")
            cls.citas[slug] = CitaCliente.objects.create(empresa=empresa, paciente=cls.pacientes[slug],
                cliente=cls.clientes[slug], producto=cls.productos[slug], servicio_clinico=cls.servicios[slug],
                profesional_salud=cls.profesionales[slug], titulo=f"Cita intacta QA {index}",
                fecha_hora=timezone.make_aware(datetime.combine(cls.FECHA, time(10, 15))),
                duracion_minutos=60, estado="confirmada", observacion="Texto clínico sintético intacto")
        cls.profesional = cls.profesionales[cls.empresa.slug]
        cls.inactive_professional = ProfesionalSalud.objects.create(empresa=cls.empresa, nombre="Profesional inactivo QA", activo=False)
        cls.block = BloqueoDisponibilidadMedica.objects.create(empresa=cls.empresa, profesional=cls.profesional,
            fecha=cls.FECHA-timedelta(days=1), fecha_fin=cls.FECHA-timedelta(days=1),
            hora_inicio=time(14, 7), hora_fin=time(15, 22), motivo="Motivo anterior intacto", creado_por=cls.users["legacy"])
        cls.foreign_block = BloqueoDisponibilidadMedica.objects.create(empresa=cls.external,
            profesional=cls.profesionales[cls.external.slug], fecha=cls.FECHA, fecha_fin=cls.FECHA,
            dia_completo=True, motivo="NO MOSTRAR BLOQUEO AJENO QA", creado_por=cls.foreign_user)

    def setUp(self):
        self.client.force_login(self.users["granular"])
        for target in ("crm.views.urlopen", "crm.views.enviar_mensaje_whatsapp_texto", "crm.views.enviar_plantilla_cita_whatsapp",
                       "crm.views.programar_notificaciones_cita", "crm.views.procesar_notificacion",
                       "crm.views.subir_media_whatsapp", "django.core.mail.EmailMessage.send"):
            mocker = patch(target, side_effect=AssertionError("No transportes ni recordatorios reales"))
            mocked = mocker.start()
            self.addCleanup(mocker.stop)
            self.addCleanup(mocked.assert_not_called)

    def url(self, empresa=None, *, delete=None, mobile=True, **query):
        empresa = empresa or self.empresa
        path = reverse("agenda_bloqueo_eliminar" if delete is not None else "agenda_bloqueo_guardar",
                       args=[empresa.slug]+([delete] if delete is not None else []))
        params = {"app": "1"} if mobile else {}
        params.update(query)
        return path+("?"+urlencode(params, doseq=True) if params else "")

    def data(self, **updates):
        data = {"app": "1", "regresar_a": "app", "profesional": str(self.profesional.pk),
                "fecha": self.FECHA.isoformat(), "fecha_fin": self.FECHA.isoformat(),
                "hora_inicio": "02:00", "hora_inicio_periodo": "PM", "hora_fin": "03:00",
                "hora_fin_periodo": "PM", "motivo": "Motivo sintético nuevo"}
        data.update(updates)
        return data

    def direct(self, url=None, *, data=None, actor=None, method="post"):
        url = url or self.url()
        factory = RequestFactory()
        request = factory.post(url, self.data() if data is None else data) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=(actor or self.users["granular"]).pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        request._dont_enforce_csrf_checks = True
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return match.func(request, **match.kwargs)

    def snapshot(self, exclude=()):
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values()))
            for model in apps.get_models(include_auto_created=True) if model._meta.label_lower not in exclude)

    def assert_no_writes(self, queries):
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for row in queries))

    def denied(self, url=None, *, status=403, **kwargs):
        before = self.snapshot()
        try:
            with CaptureQueriesContext(connection) as queries:
                response = self.direct(url, **kwargs)
        except PermissionDenied:
            self.assertEqual(status, 403)
        except Http404:
            self.assertEqual(status, 404)
        else:
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)

    def role(self, *, actor=None, empresa=None, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Rol disponibilidad restringida QA", codigo=f"availability-r-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=granular, **flags)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=actor or self.users["granular"], empresa=empresa or self.empresa,
            defaults={"rol_sistema": role, "activo": True})
        return role

    def assert_only_block_and_audit_changed(self, before):
        self.assertEqual(self.snapshot(exclude=("crm.bloqueodisponibilidadmedica", "core.registroauditoria")), before)

    def test_four_clinics_both_roles_save_to_the_existing_hospital_agenda(self):
        for actor in self.users.values():
            self.client.force_login(actor)
            for index, empresa in enumerate(self.empresas.values()):
                data = self.data(fecha=f"2026-12-{index+1:02d}", fecha_fin=f"2026-12-{index+1:02d}",
                                 motivo=f"{actor.pk}-{empresa.slug}")
                before = self.snapshot(exclude=("crm.bloqueodisponibilidadmedica", "core.registroauditoria"))
                response = self.client.post(self.url(empresa), data)
                self.assertEqual(response.status_code, 302)
                saved = BloqueoDisponibilidadMedica.objects.get(motivo=data["motivo"])
                self.assertEqual((saved.empresa_id, saved.profesional_id, saved.creado_por_id),
                                 (self.empresa.pk, self.profesional.pk, actor.pk))
                self.assert_only_block_and_audit_changed(before)
                saved.delete()  # Synthetic fixture cleanup before the next role's identical time.

    def test_trained_regresar_a_app_posts_without_app_flag_are_supported(self):
        for index, empresa in enumerate(self.empresas.values()):
            data = self.data(fecha=f"2026-12-{index+10:02d}", fecha_fin=f"2026-12-{index+10:02d}")
            del data["app"]
            response = self.client.post(self.url(empresa, mobile=False), data)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(BloqueoDisponibilidadMedica.objects.filter(empresa=self.empresa, fecha=data["fecha"]).exists())

    def test_explicit_app_flag_does_not_fall_through_to_other_company_web(self):
        for actor in (self.supervisor, self.company_admin):
            self.denied(self.url(self.external), actor=actor)
            self.denied(self.url(self.external, mobile=False), actor=actor, data=self.data())

    def test_clinical_solution_is_required_for_source_and_central_hospital(self):
        Empresa.objects.filter(pk=self.empresas["medical_spa"].pk).update(tipo_solucion="erp")
        self.denied(self.url(self.empresas["medical_spa"]), actor=self.supervisor)
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.denied(self.url(self.empresas["luque_aestetic"]), actor=self.supervisor)

    def test_logged_out_requests_cannot_save_or_delete(self):
        self.client.logout()
        before = self.snapshot()
        for url, data in ((self.url(), self.data()), (self.url(delete=self.block.pk), {"app": "1", "regresar_a": "app"})):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_source_is_denied_even_for_super(self):
        source = self.empresas["medical_spa"]
        Empresa.objects.filter(pk=source.pk).update(activa=False)
        self.denied(self.url(source), actor=self.supervisor, status=404)

    def test_missing_or_inactive_hospital_never_falls_back_to_local_company(self):
        for updates in ({"slug": "hospital-unavailable-qa"}, {"activa": False}):
            with self.subTest(updates=updates):
                Empresa.objects.filter(pk=self.empresa.pk).update(**updates)
                self.denied(self.url(self.empresas["medical_spa"]), actor=self.supervisor)
                Empresa.objects.filter(pk=self.empresa.pk).update(slug="hospital_mia", activa=True)

    def test_source_and_target_license_are_checked_except_for_super(self):
        source = self.empresas["medical_spa"]
        for company in (source, self.empresa):
            Empresa.objects.filter(pk=company.pk).update(estado_licencia="suspendida")
            self.denied(self.url(source))
            self.denied(self.url(source), actor=self.company_admin)
            response = self.direct(self.url(source), actor=self.supervisor, data=self.data(fecha="", fecha_fin=""))
            self.assertEqual(response.status_code, 400)
            Empresa.objects.filter(pk=company.pk).update(estado_licencia="activa")

    def test_membership_is_required_for_both_source_and_target(self):
        self.denied(actor=self.foreign_user)
        source = self.empresas["medical_spa"]
        actor = Usuario.objects.create_user(username="availability-source-only-qa", empresa=source, rol_sistema=self.roles["granular"])
        self.denied(self.url(source), actor=actor)
        actor.empresas_acceso.add(self.empresa)
        self.assertEqual(self.direct(self.url(source), actor=actor, data=self.data(fecha="")).status_code, 400)

    def test_granular_calendar_or_clinical_configuration_never_substitutes_edit_citas(self):
        for flags in ({"puede_ver_calendario": True}, {"puede_configuracion_clinica": True}, {"puede_citas": True}, {}):
            self.role(**flags)
            self.denied()
            self.denied(self.url(delete=self.block.pk), data={"app": "1", "regresar_a": "app"})

    def test_legacy_clinical_writer_can_only_manage_their_existing_professional(self):
        actor = Usuario.objects.create_user(username="availability-own-doctor-qa", empresa=self.empresa)
        self.role(actor=actor, granular=False, puede_clinica=True)
        own = ProfesionalSalud.objects.create(empresa=self.empresa, usuario=actor, nombre="Doctor propio QA")
        foreign = self.direct(actor=actor)
        self.assertEqual(foreign.status_code, 400)
        response = self.direct(actor=actor, data=self.data(profesional=str(own.pk)))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BloqueoDisponibilidadMedica.objects.filter(profesional=own, creado_por=actor).exists())

    def test_legacy_billing_access_preserves_original_own_professional_exception(self):
        actor = Usuario.objects.create_user(username="availability-billing-doctor-qa", empresa=self.empresa)
        self.role(actor=actor, granular=False, puede_ver_facturas=True)
        own = ProfesionalSalud.objects.create(empresa=self.empresa, usuario=actor, nombre="Profesional facturación propio QA")
        self.assertEqual(self.direct(actor=actor, data=self.data(profesional=str(own.pk))).status_code, 302)
        self.assertEqual(self.direct(actor=actor, data=self.data()).status_code, 400)

    def test_active_local_role_overrides_are_checked_on_both_companies(self):
        source = self.empresas["medical_spa"]
        self.role(empresa=source, puede_editar_citas=True)
        self.role(empresa=self.empresa, puede_ver_calendario=True)
        self.denied(self.url(source))
        UsuarioEmpresaPermiso.objects.filter(usuario=self.users["granular"], empresa=self.empresa).update(rol_sistema=None)
        self.denied(self.url(source))
        UsuarioEmpresaPermiso.objects.filter(usuario=self.users["granular"], empresa=self.empresa).update(activo=False)
        self.assertEqual(self.direct(self.url(source), data=self.data(fecha="")).status_code, 400)

    def test_super_and_company_administrator_keep_original_management_rights(self):
        for actor in (self.supervisor, self.company_admin):
            self.client.force_login(actor)
            before = self.snapshot()
            response = self.client.post(self.url(), self.data(fecha="", motivo="Datos inválidos conservados"))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(set(response.context["bloqueo_form"].fields), set(self.FIELDS))
            self.assertEqual(self.snapshot(), before)

    def test_availability_does_not_require_new_clinical_or_agenda_modules(self):
        self.assertFalse(self.empresa.modulos_habilitados().exists())
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.empresa.modulos_habilitados().exists())

    def test_original_get_head_methods_remain_405_without_forms_or_seeds(self):
        for method in ("get", "head"):
            for url in (self.url(), self.url(delete=self.block.pk)):
                with patch("crm.views.BloqueoDisponibilidadMedicaForm", side_effect=AssertionError("GET no forma")), patch("crm.views.CitaClienteForm", side_effect=AssertionError("GET no inicializador")):
                    self.denied(url, method=method, status=405)

    def test_duplicate_and_non_one_app_query_metadata_is_denied(self):
        for query in ("app=1&app=1", "app=0", "app=", "app=01"):
            self.denied(self.url(mobile=False)+"?"+query)

    def test_unknown_query_fields_are_denied_without_clinical_queries(self):
        for key in ("fecha", "profesional", "bloqueo_id", "next", "empresa", "vista", "_popup"):
            self.denied(self.url()+"&"+urlencode({key: "1"}))

    def test_duplicate_scalar_body_fields_are_denied_without_writes(self):
        for key in (*self.FIELDS, "app", "regresar_a", "bloqueo_id", "confirmar_conflictos"):
            data = self.data()
            data[key] = [str(data.get(key, "1")), str(data.get(key, "1"))]
            self.denied(data=data)

    def test_unknown_body_fields_and_all_files_are_denied(self):
        for key in ("empresa", "creado_por", "fecha_creacion", "cita_id", "accion", "initial-profesional", "_saveasnew"):
            self.denied(data={**self.data(), key: "1"})
        self.denied(data={**self.data(), "archivo": SimpleUploadedFile("proof.txt", b"synthetic")})

    def test_app_body_and_return_envelope_cannot_be_ambiguous(self):
        self.denied(self.url(mobile=False), data=self.data())
        for data in ({**self.data(), "app": "0"}, {**self.data(), "regresar_a": "web"},
                     {key: value for key, value in self.data().items() if key != "regresar_a"},
                     {**self.data(), "confirmar_conflictos": "0"}):
            self.denied(data=data)

    def test_noncanonical_malformed_or_overflow_post_ids_are_denied(self):
        for key in ("profesional", "bloqueo_id"):
            for value in ("-1", "0", "01", "1.0", "1e1", "９", "abc", str(2**63)):
                self.denied(data={**self.data(), key: value})

    def test_foreign_existing_block_cannot_be_retargeted_by_post(self):
        self.denied(data=self.data(bloqueo_id=str(self.foreign_block.pk)), status=404)
        self.denied(data=self.data(bloqueo_id=str(2**62)), status=404)

    def test_foreign_local_or_inactive_professional_uses_original_modelchoice_error(self):
        for professional in (self.profesionales["medical_spa"], self.profesionales[self.external.slug], self.inactive_professional):
            before = self.snapshot()
            response = self.client.post(self.url(self.empresas["luque_aestetic"]), self.data(profesional=str(professional.pk)))
            self.assertEqual(response.status_code, 400)
            self.assertIn("profesional", response.context["bloqueo_form"].errors)
            self.assertEqual(self.snapshot(), before)

    def test_invalid_form_preserves_all_nine_original_fields_and_no_initializer(self):
        data = self.data(fecha="2026-11-18", fecha_fin="2026-11-17", motivo="Trabajo previo <script>evil</script>")
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries, patch("crm.views.CitaClienteForm", side_effect=AssertionError("No agenda initializer")), patch("crm.views._contexto_calendario", side_effect=AssertionError("No calendario")):
            response = self.client.post(self.url(self.empresas["medical_spa"]), data)
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, self.TEMPLATE)
        form = response.context["bloqueo_form"]
        self.assertIsInstance(form, BloqueoDisponibilidadMedicaForm)
        self.assertEqual(tuple(form.fields), self.FIELDS)
        for field, value in data.items():
            self.assertEqual(form.data[field], value)
        self.assertContains(response, "&lt;script&gt;evil&lt;/script&gt;", status_code=400)
        self.assertNotContains(response, "<script>evil</script>", status_code=400)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)

    def test_original_hour_conversion_handles_midnight_and_noon(self):
        cases = (("12:00", "AM", "01:00", "AM", time(0), time(1)), ("12:00", "PM", "01:00", "PM", time(12), time(13)))
        for index, (start, start_period, end, end_period, expected_start, expected_end) in enumerate(cases):
            response = self.client.post(self.url(), self.data(fecha=f"2026-12-{20+index:02d}", fecha_fin=f"2026-12-{20+index:02d}",
                hora_inicio=start, hora_inicio_periodo=start_period, hora_fin=end, hora_fin_periodo=end_period))
            self.assertEqual(response.status_code, 302)
            saved = BloqueoDisponibilidadMedica.objects.get(fecha=f"2026-12-{20+index:02d}")
            self.assertEqual((saved.hora_inicio, saved.hora_fin), (expected_start, expected_end))

    def test_original_required_dates_time_choices_and_range_errors_do_not_write(self):
        for updates in ({"fecha": ""}, {"fecha": "2026-02-30"}, {"fecha_fin": "2026-11-17"},
                        {"hora_inicio": ""}, {"hora_inicio_periodo": ""}, {"hora_fin": ""},
                        {"hora_fin_periodo": "XX"}, {"hora_inicio": "13:00"}, {"hora_fin": "01:00"},
                        {"motivo": "x"*221}):
            before = self.snapshot()
            response = self.client.post(self.url(), self.data(**updates))
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.context["bloqueo_form"].errors)
            self.assertEqual(self.snapshot(), before)

    def test_overlapping_blocks_use_original_validation_and_edit_excludes_itself(self):
        data = self.data(fecha=self.block.fecha.isoformat(), fecha_fin=self.block.fecha_fin.isoformat())
        before = self.snapshot()
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["bloqueo_form"].non_field_errors())
        self.assertEqual(self.snapshot(), before)
        response = self.client.post(self.url(), {**data, "bloqueo_id": str(self.block.pk), "hora_inicio": "02:07", "hora_fin": "03:22"})
        self.assertEqual(response.status_code, 302)
        self.block.refresh_from_db()
        self.assertEqual((self.block.hora_inicio, self.block.hora_fin), (time(14, 7), time(15, 22)))

    def test_adjacent_time_blocks_keep_original_half_open_boundary(self):
        response = self.client.post(self.url(), self.data(fecha=self.block.fecha.isoformat(), fecha_fin=self.block.fecha_fin.isoformat(),
            hora_inicio="01:00", hora_fin="02:00"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BloqueoDisponibilidadMedica.objects.filter(empresa=self.empresa, fecha=self.block.fecha).count(), 2)

    def test_multiday_full_day_original_save_clears_times(self):
        response = self.client.post(self.url(), self.data(fecha="2026-12-26", fecha_fin="2026-12-28", dia_completo="1"))
        self.assertEqual(response.status_code, 302)
        block = BloqueoDisponibilidadMedica.objects.get(fecha="2026-12-26")
        self.assertTrue(block.dia_completo)
        self.assertEqual(block.fecha_fin, date(2026, 12, 28))
        self.assertIsNone(block.hora_inicio)
        self.assertIsNone(block.hora_fin)

    def test_cita_conflict_requires_original_confirmation_without_any_mutation(self):
        data = self.data(hora_inicio="09:00", hora_inicio_periodo="AM", hora_fin="12:00", hora_fin_periodo="PM")
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url(self.empresas["medical_spa"]), data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, self.TEMPLATE)
        self.assertEqual([cita.pk for cita in response.context["citas_afectadas"]], [self.citas[self.empresa.slug].pk])
        self.assertTrue(response.context["bloqueo_form"].is_bound)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)

    def test_confirmed_conflict_saves_only_block_and_audit_never_citas(self):
        before = self.snapshot(exclude=("crm.bloqueodisponibilidadmedica", "core.registroauditoria"))
        response = self.client.post(self.url(self.empresas["luque_aestetic"]), self.data(hora_inicio="09:00", hora_inicio_periodo="AM",
            hora_fin="12:00", hora_fin_periodo="PM", confirmar_conflictos="1"))
        self.assertEqual(response.status_code, 302)
        self.assert_only_block_and_audit_changed(before)
        self.assertTrue(BloqueoDisponibilidadMedica.objects.filter(empresa=self.empresa, fecha=self.FECHA).exists())

    def test_pure_capability_and_calendar_context_require_both_company_gates(self):
        from .mobile_availability import disponibilidad_disponible_app

        source = self.empresas["medical_spa"]
        request = RequestFactory().get(reverse("agenda_mobile", args=[source.slug]))
        request.user = Usuario.objects.get(pk=self.users["granular"].pk)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(disponibilidad_disponible_app(request, source, self.empresa))
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)
        self.role(puede_ver_calendario=True)
        before = self.snapshot()
        request.user = Usuario.objects.get(pk=self.users["granular"].pk)
        with CaptureQueriesContext(connection) as queries:
            self.assertFalse(disponibilidad_disponible_app(request, source, self.empresa))
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)
        # The trained calendar GET keeps its original initialization behavior.
        # Only the new capability flag is restricted; no zero-write claim here.
        response = self.client.get(reverse("agenda_mobile", args=[source.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/agenda_mobile.html")
        self.assertFalse(response.context["puede_gestionar_bloqueos"])
        self.role(puede_editar_citas=True, puede_ver_calendario=True, puede_citas=True)
        response = self.client.get(reverse("agenda_mobile", args=[source.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["puede_gestionar_bloqueos"])

    def test_cancelled_citas_do_not_require_confirmation(self):
        CitaCliente.objects.filter(pk=self.citas[self.empresa.slug].pk).update(estado="cancelada")
        response = self.client.post(self.url(), self.data(hora_inicio="09:00", hora_inicio_periodo="AM", hora_fin="12:00", hora_fin_periodo="PM"))
        self.assertEqual(response.status_code, 302)

    def test_create_has_original_global_audit_actor_company_values_and_route(self):
        response = self.client.post(self.url(self.empresas["medical_spa"]), self.data())
        self.assertEqual(response.status_code, 302)
        block = BloqueoDisponibilidadMedica.objects.get(fecha=self.FECHA, empresa=self.empresa)
        audit = RegistroAuditoria.objects.get(modelo="bloqueodisponibilidadmedica", objeto_id=str(block.pk), accion="crear")
        self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (self.users["granular"].pk, self.empresa.pk, "POST"))
        self.assertEqual(audit.ruta, urlsplit(self.url(self.empresas["medical_spa"])).path)
        self.assertTrue(audit.fecha)
        self.assertIn("motivo", str(audit.cambios))

    def test_edit_preserves_creator_and_updates_original_audit_before_after(self):
        before = self.snapshot(exclude=("crm.bloqueodisponibilidadmedica", "core.registroauditoria"))
        response = self.client.post(self.url(self.empresas["medical_spa"]), self.data(bloqueo_id=str(self.block.pk), motivo="Motivo corregido QA"))
        self.assertEqual(response.status_code, 302)
        self.block.refresh_from_db()
        self.assertEqual(self.block.creado_por_id, self.users["legacy"].pk)
        audit = RegistroAuditoria.objects.filter(modelo="bloqueodisponibilidadmedica", objeto_id=str(self.block.pk), accion="modificar").latest("pk")
        self.assertEqual(audit.cambios["motivo"], {"anterior": "Motivo anterior intacto", "nuevo": "Motivo corregido QA"})
        self.assertEqual(audit.usuario_id, self.users["granular"].pk)
        self.assert_only_block_and_audit_changed(before)

    def test_null_creator_is_assigned_by_the_original_save_branch(self):
        BloqueoDisponibilidadMedica.objects.filter(pk=self.block.pk).update(creado_por=None)
        response = self.client.post(self.url(), self.data(bloqueo_id=str(self.block.pk)))
        self.assertEqual(response.status_code, 302)
        self.block.refresh_from_db()
        self.assertEqual(self.block.creado_por_id, self.users["granular"].pk)

    def test_delete_removes_only_block_and_original_audit_preserves_actor(self):
        before = self.snapshot(exclude=("crm.bloqueodisponibilidadmedica", "core.registroauditoria"))
        response = self.client.post(self.url(self.empresas["medical_spa"], delete=self.block.pk), {"app": "1", "regresar_a": "app"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BloqueoDisponibilidadMedica.objects.filter(pk=self.block.pk).exists())
        self.assertTrue(BloqueoDisponibilidadMedica.objects.filter(pk=self.foreign_block.pk).exists())
        audit = RegistroAuditoria.objects.get(modelo="bloqueodisponibilidadmedica", objeto_id=str(self.block.pk), accion="eliminar")
        self.assertEqual((audit.usuario_id, audit.empresa_id), (self.users["granular"].pk, self.empresa.pk))
        self.assert_only_block_and_audit_changed(before)

    def test_delete_has_a_closed_scalar_envelope_without_form_data_or_files(self):
        data = {"app": "1", "regresar_a": "app"}
        for key in ("profesional", "bloqueo_id", "confirmar_conflictos", "empresa", "motivo", "accion"):
            self.denied(self.url(delete=self.block.pk), data={**data, key: "1"})
        for key in ("app", "regresar_a"):
            self.denied(self.url(delete=self.block.pk), data={**data, key: [data[key], data[key]]})
        self.denied(self.url(delete=self.block.pk), data={**data, "archivo": SimpleUploadedFile("x.txt", b"x")})

    def test_delete_foreign_missing_or_noncanonical_path_id_has_no_fallback(self):
        for raw, expected in ((str(self.foreign_block.pk), 404), (str(2**62), 404), ("0", 403),
                              ("00"+str(self.block.pk), 403), (str(2**63), 403)):
            self.denied(self.url(delete=raw), status=expected, data={"app": "1", "regresar_a": "app"})

    def test_own_professional_exception_does_not_allow_edit_or_delete_other_professional(self):
        actor = Usuario.objects.create_user(username="availability-other-editor-qa", empresa=self.empresa)
        self.role(actor=actor, granular=False, puede_clinica=True)
        ProfesionalSalud.objects.create(empresa=self.empresa, usuario=actor, nombre="Otro doctor propio QA")
        self.denied(actor=actor, data=self.data(bloqueo_id=str(self.block.pk)))
        self.denied(self.url(delete=self.block.pk), actor=actor, data={"app": "1", "regresar_a": "app"})

    def test_all_target_cita_related_entities_are_validated_before_contact_join(self):
        cita = self.citas[self.empresa.slug]
        fields = {"paciente_id": self.pacientes[self.external.slug].pk, "cliente_id": self.clientes[self.external.slug].pk,
                  "servicio_clinico_id": self.servicios[self.external.slug].pk, "producto_id": self.productos[self.external.slug].pk}
        for key, foreign in fields.items():
            original = getattr(cita, key)
            for value in (foreign, 2**62):
                CitaCliente.objects.filter(pk=cita.pk).update(**{key: value})
                with patch("crm.views.citas_afectadas_por_bloqueo", side_effect=AssertionError("No foreign contacts")):
                    self.denied(data=self.data(hora_inicio="09:00", hora_inicio_periodo="AM", hora_fin="12:00", hora_fin_periodo="PM"))
                CitaCliente.objects.filter(pk=cita.pk).update(**{key: original})

    def test_block_professional_and_creator_imported_relations_are_fail_closed(self):
        for key, value in (("profesional_id", self.profesionales[self.external.slug].pk), ("profesional_id", 2**62), ("creado_por_id", 2**62)):
            original = getattr(self.block, key)
            BloqueoDisponibilidadMedica.objects.filter(pk=self.block.pk).update(**{key: value})
            self.denied(data=self.data(bloqueo_id=str(self.block.pk)))
            self.denied(self.url(delete=self.block.pk), data={"app": "1", "regresar_a": "app"})
            BloqueoDisponibilidadMedica.objects.filter(pk=self.block.pk).update(**{key: original})

    def test_out_of_date_imported_cita_still_blocks_app_without_foreign_html(self):
        cita = self.citas[self.empresa.slug]
        CitaCliente.objects.filter(pk=cita.pk).update(paciente_id=self.pacientes[self.external.slug].pk,
            fecha_hora=timezone.make_aware(datetime(2020, 1, 1, 10)))
        with patch("crm.views.citas_afectadas_por_bloqueo", side_effect=AssertionError("No foreign disclosure")):
            self.denied(data=self.data())

    def test_nullable_cita_links_and_existing_multicompany_actors_are_legitimate(self):
        CitaCliente.objects.filter(pk=self.citas[self.empresa.slug].pk).update(paciente=None, cliente=None,
            producto=None, servicio_clinico=None, profesional_salud=None)
        ProfesionalSalud.objects.filter(pk=self.profesional.pk).update(usuario=self.foreign_user)
        BloqueoDisponibilidadMedica.objects.filter(pk=self.block.pk).update(creado_por=self.foreign_user)
        response = self.client.post(self.url(), self.data(bloqueo_id=str(self.block.pk)))
        self.assertEqual(response.status_code, 302)
        self.block.refresh_from_db()
        self.assertEqual(self.block.creado_por_id, self.foreign_user.pk)

    def test_safe_mobile_return_uses_source_date_and_agenda_panel_without_seeding(self):
        source = self.empresas["medical_spa"]
        response = self.client.post(self.url(source), self.data())
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.path, reverse("agenda_mobile", args=[source.slug]))
        self.assertEqual(parse_qs(parsed.query), {"app": ["agenda"], "vista": ["dia"], "fecha": [self.FECHA.isoformat()]})
        self.assertEqual(parsed.fragment, "agenda-app")
        self.role(puede_editar_citas=True, puede_citas=True)
        before = self.snapshot()
        response = self.client.post(self.url(), self.data(fecha=""))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"', status_code=400)
        self.assertEqual(self.snapshot(), before)
        markup = _AvailabilityMarkup(response.content.decode())
        self.assertFalse(any(urlsplit(href).path == reverse("agenda_mobile", args=[self.empresa.slug]) for href in markup.links))
        response = self.client.post(self.url(), self.data(fecha="2026-12-24", fecha_fin="2026-12-24"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_web_local_company_form_templates_save_redirects_remain_original(self):
        for index, empresa in enumerate(self.empresas.values()):
            self.client.force_login(self.users["legacy"])
            target = self.empresa if empresa.slug == "serviciosmedicos" else empresa
            data = self.data(profesional=str(self.profesionales[target.slug].pk), fecha=f"2026-12-{index+5:02d}", fecha_fin=f"2026-12-{index+5:02d}")
            del data["app"]
            del data["regresar_a"]
            response = self.client.post(self.url(empresa, mobile=False), {**data, "fecha_fin": ""})
            self.assertEqual(response.status_code, 400)
            self.assertTemplateUsed(response, "crm/bloqueo_disponibilidad_form.html")
            self.assertTemplateNotUsed(response, self.TEMPLATE)
            response = self.client.post(self.url(empresa, mobile=False), data)
            self.assertEqual(response.status_code, 302)
            saved = BloqueoDisponibilidadMedica.objects.get(empresa=target, fecha=data["fecha"])
            self.assertEqual(saved.profesional_id, self.profesionales[target.slug].pk)
            self.assertEqual(response.url, f"{reverse('agenda_citas', args=[empresa.slug])}?vista=dia&fecha={data['fecha']}#disponibilidad-medica")
            response = self.client.post(self.url(empresa, mobile=False, delete=saved.pk), {})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(BloqueoDisponibilidadMedica.objects.filter(pk=saved.pk).exists())

    def test_mobile_render_uses_nine_native_fields_closed_action_and_csrf(self):
        response = self.client.post(self.url(), self.data(fecha="", bloqueo_id=str(self.block.pk)))
        self.assertEqual(response.status_code, 400)
        markup = _AvailabilityMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        self.assertEqual(urlsplit(markup.forms[0]["action"]).path, urlsplit(self.url()).path)
        for field in (*self.FIELDS, "app", "regresar_a", "csrfmiddlewaretoken", "bloqueo_id"):
            self.assertEqual(markup.names.count(field), 1, field)
        self.assertFalse(set(markup.names)-{*self.FIELDS, "app", "regresar_a", "csrfmiddlewaretoken", "bloqueo_id"})
        self.assertNotContains(response, self.foreign_block.motivo, status_code=400)
        self.assertEqual(response.context["bloqueo_form"].fields["fecha"].widget.format, "%Y-%m-%d")
        self.assertEqual(BloqueoDisponibilidadMedicaForm().fields["fecha"].widget.format, None)

    def test_csrf_handler_blocks_writes_and_valid_token_keeps_original_operation(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.users["granular"])
        before = self.snapshot(exclude=("sessions.session",))
        response = strict.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(self.snapshot(exclude=("sessions.session",)), before)
        strict.cookies["csrftoken"] = "a"*32
        response = strict.post(self.url(), {**self.data(), "csrfmiddlewaretoken": "a"*32})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BloqueoDisponibilidadMedica.objects.filter(empresa=self.empresa, fecha=self.FECHA).exists())
