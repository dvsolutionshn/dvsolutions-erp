from datetime import timedelta
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from clinica import test_mobile_controls as control_fixtures
from clinica.models import HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica, ServicioClinico
from facturacion.models import Factura, MovimientoInventario
from . import views
from .forms import ProgramaCamaraHiperbaricaForm, ProgramaTerapiaPostQuirurgicaForm, SesionCamaraHiperbaricaForm, SesionTerapiaPostQuirurgicaForm
from .models import CitaCliente, ConfiguracionCRM, NotificacionCitaWhatsApp, OpcionServicioAgenda, ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionCamaraHiperbarica, SesionTerapiaPostQuirurgica


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAppointmentControlsIntegrationTests(TestCase):
    TIPOS = ("camara_hiperbarica", "terapias_postquirurgicas")
    CONFIG = {
        "camara_hiperbarica": ("agenda_camara_hiperbarica", "agenda_camara_hiperbarica_guardar", "control_camara", "camara", "puede_ver_camara_hiperbarica", "puede_escribir_camara_hiperbarica", ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaCamaraHiperbaricaForm, SesionCamaraHiperbaricaForm),
        "terapias_postquirurgicas": ("agenda_terapias_postquirurgicas", "agenda_terapias_postquirurgicas_guardar", "control_terapia", "terapia", "puede_ver_postquirurgicas", "puede_escribir_postquirurgicas", ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica, ProgramaTerapiaPostQuirurgicaForm, SesionTerapiaPostQuirurgicaForm),
    }

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.profesionales, cls.servicios, cls.citas, cls.programas, cls.sesiones = {}, {}, {}, {}, {}, {}
        cls.modulo_agenda, _ = Modulo.objects.get_or_create(codigo="agenda_citas", defaults={"nombre": "Agenda"})
        cls.modulo_clinica, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.fecha = timezone.localdate()+timedelta(days=2)
        for index, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Control de citas {slug}", slug=slug, rtn=f"MAC-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo_agenda)
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo_clinica)
            profesional = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Personal sintético {slug}")
            cls.profesionales[slug] = profesional
            for tipo, config in cls.CONFIG.items():
                nombre = "Cámara hiperbárica" if tipo == cls.TIPOS[0] else "Terapias Post Quirúrgicas"
                servicio = ServicioClinico.objects.create(empresa=empresa, nombre=nombre, categoria="tratamiento", duracion_minutos=60)
                cls.servicios[(slug, tipo)] = servicio
                for caso, numero in (("new", 6), ("draft", 1), ("locked", 3)):
                    key = (slug, tipo, caso)
                    paciente = Paciente.objects.create(empresa=empresa, nombre=f"Paciente cita {slug} {tipo} {caso}", expediente_codigo=f"MAC-{index}-{tipo}-{caso}")
                    cita = CitaCliente.objects.create(
                        empresa=empresa, paciente=paciente, servicio_clinico=servicio, profesional_salud=profesional,
                        titulo=nombre, responsable=profesional.nombre,
                        fecha_hora=timezone.localtime().replace(year=cls.fecha.year, month=cls.fecha.month, day=cls.fecha.day, hour=9 if tipo == cls.TIPOS[0] else 10, minute=0, second=0, microsecond=0),
                        sesion_servicio=numero, fase_servicio=2 if tipo == cls.TIPOS[1] and caso == "new" else 1 if tipo == cls.TIPOS[1] else None,
                    )
                    cls.citas[key] = cita
                    if caso != "new":
                        programa = config[6].objects.create(empresa=empresa, paciente=paciente, cirugia="Programa sintético conservado", fecha_cirugia="2026-10-01")
                        cls.programas[key] = programa
                        cls.sesiones[key] = config[7].objects.create(
                            programa=programa, empresa=empresa, paciente=paciente, cita=cita, numero_sesion=numero,
                            nota_enfermeria=f"Nota anterior {slug} {tipo} {caso}", estado="finalizada" if caso == "locked" else "borrador",
                            **({"fase": 1} if tipo == cls.TIPOS[1] else {}),
                        )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(
            nombre="Atención citas móvil", codigo="mobile-appointment-controls-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_ver_camara_hiperbarica=True, puede_escribir_camara_hiperbarica=True, puede_ver_postquirurgicas=True, puede_escribir_postquirurgicas=True,
        )
        cls.usuario = Usuario.objects.create_user(username="appointment-controls-mobile", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="dv-mobile-appointment-controls-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        for target in ("crm.views.enviar_mensaje_whatsapp_texto", "crm.views.enviar_plantilla_cita_whatsapp", "crm.views.subir_media_whatsapp", "crm.views.programar_notificaciones_cita", "crm.views.procesar_notificacion", "crm.views.urlopen"):
            patcher = patch(target)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mock.assert_not_called)

    def asignar_rol(self, empresa=None, granular=True, **permisos):
        rol = RolSistema.objects.create(nombre="Atención citas móvil restringida", codigo=f"mobile-appointment-role-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permisos)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def cita(self, tipo="camara_hiperbarica", caso="new", empresa=None):
        return self.citas[((empresa or self.empresa).slug, tipo, caso)]

    def url(self, tipo="camara_hiperbarica", cita=None, save=False, empresa=None, mobile=True, **overrides):
        empresa = empresa or self.empresa
        route = self.CONFIG[tipo][1 if save else 0]
        args = [empresa.slug]+([cita.pk] if save else [])
        params = {"app": "1"} if mobile else {}
        if not save:
            params["fecha"] = timezone.localtime(cita.fecha_hora).date().isoformat() if cita else self.fecha.isoformat()
            if cita:
                params[self.CONFIG[tipo][2]] = cita.pk
        params.update(overrides)
        return reverse(route, args=args)+("?"+urlencode(params) if params else "")

    def datos(self, codigo="camara_hiperbarica", cita=None, **overrides):
        data = control_fixtures.MobileControlsIntegrationTests.datos(self, codigo)
        del data["sesion_id"]
        cita = cita or self.cita(codigo)
        session = self.CONFIG[codigo][7].objects.filter(cita=cita).first()
        data["programa_id"] = str(session.programa_id) if session else ""
        data["numero_sesion_adicional"] = ""
        data.update(overrides)
        return data

    def formularios(self, response, tipo):
        suffix = self.CONFIG[tipo][3]
        return response.context[f"programa_{suffix}_form"], response.context[f"sesion_{suffix}_form"]

    def conteos(self):
        return tuple(model.objects.count() for model in (
            ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica,
            Paciente, ProfesionalSalud, ServicioClinico, OpcionServicioAgenda, ConfiguracionCRM, NotificacionCitaWhatsApp,
            PreconsultaClinica, HistoriaClinicaEspecialidad, RecetaMedica, CitaCliente, Factura, MovimientoInventario, RegistroAuditoria,
        ))

    def directo(self, tipo="camara_hiperbarica", cita=None, empresa=None, method="get", data=None, user=None):
        empresa = empresa or self.empresa
        factory = RequestFactory()
        url = self.url(tipo, cita, save=method == "post", empresa=empresa)
        request = factory.post(url, data or self.datos(tipo, cita)) if method == "post" else factory.get(url)
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        kwargs = {"empresa_slug": empresa.slug}
        if method == "post":
            kwargs["cita_id"] = cita.pk
        view = (views.guardar_control_camara_hiperbarica if tipo == self.TIPOS[0] else views.guardar_terapia_postquirurgica) if method == "post" else (views.camara_hiperbarica if tipo == self.TIPOS[0] else views.terapias_postquirurgicas)
        return view(request, **kwargs)

    def assert_no_clinical_selects(self, captured):
        selects = [query["sql"] for query in captured if query["sql"].lstrip().upper().startswith("SELECT")]
        for model in (CitaCliente, ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica, Paciente):
            self.assertFalse(any(f'"{model._meta.db_table}"' in query for query in selects), model._meta.label)

    def test_mobile_day_and_selected_control_are_distinct_from_unchanged_web(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            for cita in (None, self.cita(tipo), self.cita(tipo, "draft"), self.cita(tipo, "locked")):
                response = self.client.get(self.url(tipo, cita))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "crm/mobile_appointment_control.html")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertNotContains(response, "<table")
                web = self.client.get(self.url(tipo, cita, mobile=False))
                self.assertEqual(web.status_code, 200)
                self.assertTemplateUsed(web, f"crm/{'camara_hiperbarica' if tipo == self.TIPOS[0] else 'terapias_postquirurgicas'}.html")
        self.assertEqual(self.conteos(), before)

    def test_selected_forms_reuse_all_original_fields_choices_date_and_time(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            response = self.client.get(self.url(tipo, cita))
            original = self.client.get(self.url(tipo, cita, mobile=False))
            for form, web_form, expected in zip(self.formularios(response, tipo), self.formularios(original, tipo), self.CONFIG[tipo][8:10]):
                self.assertIsInstance(form, expected)
                self.assertEqual(list(form.fields), list(web_form.fields))
                for name, field in form.fields.items():
                    self.assertContains(response, f'name="{name}"')
                    if hasattr(field, "choices"):
                        self.assertEqual(list(field.choices), list(web_form.fields[name].choices))
            session_html = response.content.decode().split('id="mobileAppointmentControlForm"', 1)[1].split("</form>", 1)[0]
            self.assertEqual(session_html.count('name="app" value="1"'), 1)
            self.assertEqual(session_html.count('name="programa_id"'), 1)
            self.assertNotIn('name="sesion_id"', session_html)
            self.assertIn("novalidate", session_html)

    def test_reader_only_can_consult_but_forms_disabled_and_post_denied_directly(self):
        for tipo in self.TIPOS:
            self.asignar_rol(**{self.CONFIG[tipo][4]: True})
            cita = self.cita(tipo)
            response = self.client.get(self.url(tipo, cita))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["can_register_appointment_control"])
            self.assertTrue(response.context["appointment_control_readonly"])
            self.assertTrue(all(field.disabled for form in self.formularios(response, tipo) for field in form.fields.values()))
            self.assertNotContains(response, 'method="post"')
            before = self.conteos()
            data = self.datos(tipo, cita)
            with CaptureQueriesContext(connection) as captured:
                with self.assertRaises(PermissionDenied):
                    self.directo(tipo, cita, method="post", data=data)
            self.assert_no_clinical_selects(captured)
            self.assertEqual(self.conteos(), before)

    def test_writer_only_has_no_mobile_read_or_write_grant_before_queries(self):
        for tipo in self.TIPOS:
            self.asignar_rol(**{self.CONFIG[tipo][5]: True})
            before = self.conteos()
            data = self.datos(tipo, self.cita(tipo))
            for method in ("get", "post"):
                with CaptureQueriesContext(connection) as captured:
                    with self.assertRaises(PermissionDenied):
                        self.directo(tipo, self.cita(tipo), method=method, data=data)
                self.assert_no_clinical_selects(captured)
            self.assertEqual(self.conteos(), before)

    def test_specific_type_permissions_do_not_authorize_other_type_or_calendar(self):
        for tipo in self.TIPOS:
            self.asignar_rol(**{self.CONFIG[tipo][4]: True, self.CONFIG[tipo][5]: True})
            response = self.client.get(self.url(tipo, self.cita(tipo)))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            otro = next(item for item in self.TIPOS if item != tipo)
            with self.assertRaises(PermissionDenied):
                self.directo(otro, self.cita(otro))
            with self.assertRaises(PermissionDenied):
                self.directo(otro, self.cita(otro), method="post")

    def test_legacy_citas_access_is_preserved_without_granting_legacy_expediente(self):
        self.asignar_rol(granular=False, puede_citas=True)
        for tipo in self.TIPOS:
            self.assertEqual(self.client.get(self.url(tipo, self.cita(tipo))).status_code, 200)
            self.assertEqual(self.client.post(self.url(tipo, self.cita(tipo), save=True), self.datos(tipo)).status_code, 302)
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True, puede_expediente_clinico=True)
        for tipo in self.TIPOS:
            with self.assertRaises(PermissionDenied):
                self.directo(tipo, self.cita(tipo))

    def test_other_three_companies_remain_unadapted_original_404(self):
        before = self.conteos()
        for empresa in list(self.empresas.values())[1:]:
            for tipo in self.TIPOS:
                cita = self.cita(tipo, empresa=empresa)
                for mobile in (False, True):
                    self.assertEqual(self.client.get(self.url(tipo, cita, empresa=empresa, mobile=mobile)).status_code, 404)
                    data = self.datos(tipo, cita)
                    if not mobile:
                        del data["app"]
                    self.assertEqual(self.client.post(self.url(tipo, cita, save=True, empresa=empresa, mobile=mobile), data).status_code, 404)
        self.assertEqual(self.conteos(), before)

    def test_active_agenda_module_company_and_membership_are_checked_without_middleware(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo_agenda).update(activo=False)
        for tipo in self.TIPOS:
            with self.assertRaises(Http404):
                self.directo(tipo, self.cita(tipo))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo_agenda).update(activo=True)
        outsider = Usuario.objects.create_user(username="appointment-outsider", password="test", empresa=self.empresas["medical_spa"], rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            self.directo(cita=self.cita(), user=outsider)
        self.empresa.activa = False
        self.empresa.save(update_fields=["activa"])
        with self.assertRaises(Http404):
            self.directo(cita=self.cita())

    def test_get_and_render_errors_never_seed_catalog_patients_professionals_or_notifications(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            self.assertEqual(self.client.get(self.url(tipo)).status_code, 200)
            self.assertEqual(self.client.get(self.url(tipo, self.cita(tipo))).status_code, 200)
            data = self.datos(tipo, programa="otro", programa_otro="") if tipo == self.TIPOS[0] else self.datos(tipo, fase="3", cuidado_otro="", cuidados_realizados=["otro"])
            self.assertEqual(self.client.post(self.url(tipo, self.cita(tipo), save=True), data).status_code, 200)
        self.assertEqual(self.conteos(), before)

    def test_selected_foreign_unknown_malformed_or_overflow_appointment_is_not_silent_fallback(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            for value in ("abc", "0", "-1", "999999", str(2**90), "00"+str(self.cita(tipo).pk), str(self.cita(tipo, empresa=self.empresas["medical_spa"]).pk)):
                response = self.client.get(self.url(tipo, **{self.CONFIG[tipo][2]: value}))
                self.assertIn(response.status_code, (403, 404))
            foreign = self.cita(tipo, empresa=self.empresas["medical_spa"])
            self.assertEqual(self.client.post(self.url(tipo, foreign, save=True), self.datos(tipo, foreign)).status_code, 404)
        self.assertEqual(self.conteos(), before)

    def test_strict_date_selection_duplicate_queries_and_wrong_control_type_are_rejected(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            for extra in ({"fecha": "fecha-inválida"}, {"fecha": "2026-02-30"}, {"fecha": (self.fecha+timedelta(days=1)).isoformat()}, {"historia_id": "1"}, {"programa": "1"}):
                self.assertIn(self.client.get(self.url(tipo, cita, **extra)).status_code, (403, 404))
            url = self.url(tipo, cita)
            self.assertIn(self.client.get(url+f"&{self.CONFIG[tipo][2]}={cita.pk}").status_code, (403, 404))
            self.assertIn(self.client.get(url+"&app=1").status_code, (403, 404))
            wrong = next(item for item in self.TIPOS if item != tipo)
            self.assertIn(self.client.get(self.url(tipo, self.cita(wrong))).status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_post_strict_metadata_foreign_program_duplicates_and_mixed_direct_action(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            foreign_program = self.programas[("medical_spa", tipo, "draft")]
            for extra in ({"programa_id": foreign_program.pk}, {"programa_id": "abc"}, {"programa_id": str(2**90)}, {"programa_id": "999999"},
                          {"accion": "eliminar"}, {"sesion_id": "1"}, {"cita_id": self.cita(tipo, "draft").pk}, {"paciente_id": foreign_program.paciente_id},
                          {"tipo_historia": "capilar"}, {"app": "0"}, {"fecha_sesion": "2026-01-01"}):
                self.assertIn(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, **extra)).status_code, (403, 404))
            base = QueryDict(mutable=True)
            for key, value in self.datos(tipo, cita).items():
                base.setlist(key, value if isinstance(value, list) else [str(value)])
            for key in ("app", "accion", "programa_id", "numero_sesion", "cirugia"):
                data = base.copy()
                data.setlist(key, [base[key], base[key]])
                self.assertIn(self.client.post(self.url(tipo, cita, save=True), data.urlencode(), content_type="application/x-www-form-urlencoded").status_code, (403, 404))
            self.assertEqual(self.client.post(self.url(tipo, cita, save=True, app="0"), self.datos(tipo, cita)).status_code, 403)
        self.assertEqual(self.conteos(), before)

    def test_blank_program_with_existing_program_cannot_create_or_fallback(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo, "draft")
            before = self.conteos()
            self.assertEqual(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, programa_id="")).status_code, 403)
            self.assertEqual(self.conteos(), before)

    def test_suggested_active_program_requires_explicit_id_without_creating_another(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            program = self.CONFIG[tipo][6].objects.create(empresa=self.empresa, paciente=cita.paciente, cirugia="Programa activo sugerido", fecha_cirugia="2026-10-01")
            response = self.client.get(self.url(tipo, cita))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context[f"programa_{self.CONFIG[tipo][3]}"].pk, program.pk)
            before = self.conteos()
            self.assertEqual(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita)).status_code, 403)
            self.assertEqual(self.conteos(), before)
            self.assertEqual(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, programa_id=str(program.pk))).status_code, 302)
            self.assertEqual(self.CONFIG[tipo][7].objects.get(cita=cita).programa_id, program.pk)
            self.assertEqual(self.CONFIG[tipo][6].objects.filter(empresa=self.empresa, paciente=cita.paciente).count(), 1)

    def test_foreign_patient_professional_and_service_relationships_fail_before_ui_or_save(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            fields = {"paciente": self.cita(tipo, empresa=self.empresas["medical_spa"]).paciente,
                      "profesional_salud": self.profesionales["medical_spa"], "servicio_clinico": self.servicios[("medical_spa", tipo)]}
            for field, value in fields.items():
                original = getattr(cita, field)
                CitaCliente.objects.filter(pk=cita.pk).update(**{field: value})
                before = self.conteos()
                self.assertIn(self.client.get(self.url(tipo, cita)).status_code, (403, 404))
                self.assertIn(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita)).status_code, (403, 404))
                self.assertEqual(self.conteos(), before)
                CitaCliente.objects.filter(pk=cita.pk).update(**{field: original})

    def test_corrupt_existing_session_and_prior_program_relations_are_denied(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo, "draft")
            session = self.sesiones[(self.empresa.slug, tipo, "draft")]
            model = self.CONFIG[tipo][7]
            for extra in ({"empresa": self.empresas["medical_spa"]}, {"paciente": self.cita(tipo, empresa=self.empresas["medical_spa"]).paciente}, {"programa": self.programas[("medical_spa", tipo, "draft")]}):
                model.objects.filter(pk=session.pk).update(**extra, **({"numero_sesion": 11} if "programa" in extra else {}))
                before = self.conteos()
                self.assertIn(self.client.get(self.url(tipo, cita)).status_code, (403, 404))
                self.assertIn(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita)).status_code, (403, 404))
                self.assertEqual(self.conteos(), before)
                model.objects.filter(pk=session.pk).update(empresa=self.empresa, paciente=cita.paciente, programa=self.programas[(self.empresa.slug, tipo, "draft")], numero_sesion=cita.sesion_servicio)

    def test_corrupt_other_appointment_same_day_is_denied_before_valid_or_autosave_post(self):
        for tipo in self.TIPOS:
            current = self.cita(tipo)
            other = self.cita(tipo, "draft")
            foreign_patient = self.cita(tipo, empresa=self.empresas["medical_spa"]).paciente
            CitaCliente.objects.filter(pk=other.pk).update(paciente=foreign_patient)
            before = self.conteos()
            for selected in (None, current):
                response = self.client.get(self.url(tipo, selected))
                self.assertIn(response.status_code, (403, 404))
                self.assertNotContains(response, foreign_patient.nombre, status_code=response.status_code)
            for extra in ({}, {"accion": "finalizar", "firma_enfermeria": ""}):
                response = self.client.post(self.url(tipo, current, save=True), self.datos(tipo, current, **extra))
                self.assertIn(response.status_code, (403, 404))
            self.assertEqual(self.conteos(), before)
            CitaCliente.objects.filter(pk=other.pk).update(paciente=other.paciente)

    def test_corrupt_other_session_in_selected_program_is_denied_before_any_save(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo, "draft")
            program = self.programas[(self.empresa.slug, tipo, "draft")]
            model = self.CONFIG[tipo][7]
            other = model.objects.create(programa=program, empresa=self.empresa, paciente=cita.paciente, numero_sesion=9, **({"fase": 1} if tipo == self.TIPOS[1] else {}))
            foreign_patient = self.cita(tipo, empresa=self.empresas["medical_spa"]).paciente
            for extra in ({"empresa": self.empresas["medical_spa"]}, {"paciente": foreign_patient}):
                model.objects.filter(pk=other.pk).update(**extra)
                before = self.conteos()
                response = self.client.get(self.url(tipo, cita))
                self.assertIn(response.status_code, (403, 404))
                self.assertNotContains(response, foreign_patient.nombre, status_code=response.status_code)
                for changes in ({}, {"accion": "finalizar", "firma_enfermeria": ""}):
                    self.assertIn(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, **changes)).status_code, (403, 404))
                self.assertEqual(self.conteos(), before)
                model.objects.filter(pk=other.pk).update(empresa=self.empresa, paciente=cita.paciente)

    def test_new_draft_preserves_appointment_fk_authority_actors_auto_date_and_audit(self):
        started = timezone.now()
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            original_cita = (cita.fecha_hora, cita.sesion_servicio, cita.fase_servicio, cita.paciente_id, cita.profesional_salud_id)
            response = self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, numero_sesion="19", fase="1") if tipo == self.TIPOS[1] else self.datos(tipo, cita, numero_sesion="19"))
            self.assertEqual(response.status_code, 302)
            session = self.CONFIG[tipo][7].objects.get(cita=cita)
            self.assertEqual(session.numero_sesion, cita.sesion_servicio)
            if tipo == self.TIPOS[1]:
                self.assertEqual(session.fase, cita.fase_servicio)
            self.assertEqual((session.cita_id, session.paciente_id, session.empresa_id), (cita.pk, cita.paciente_id, self.empresa.pk))
            self.assertEqual((session.creado_por_id, session.actualizado_por_id), (self.usuario.pk,)*2)
            self.assertGreaterEqual(session.fecha_sesion, started)
            self.assertEqual(session.estado, "borrador")
            self.assertTrue(RegistroAuditoria.objects.filter(modelo=session._meta.model_name, objeto_id=str(session.pk), usuario=self.usuario, accion="crear", metodo_http="POST").exists())
            cita.refresh_from_db()
            self.assertEqual((cita.fecha_hora, cita.sesion_servicio, cita.fase_servicio, cita.paciente_id, cita.profesional_salud_id), original_cita)
            self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])

    def test_original_additional_sessions_and_postoperative_checkbox_lists_are_preserved(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            response = self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, numero_sesion_adicional="7"))
            self.assertEqual(response.status_code, 302)
            session = self.CONFIG[tipo][7].objects.get(cita=cita)
            self.assertEqual((session.numero_sesion, session.numero_sesion_adicional), (6, 7))
            if tipo == self.TIPOS[1]:
                self.assertEqual(session.estado_paciente, ["bueno", "edema"])
                self.assertEqual(session.cuidados_realizados, ["drenaje_linfatico", "otro"])

    def test_valid_finalize_locks_both_forms_and_later_post_keeps_appointment_and_audit(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            self.assertEqual(self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, accion="finalizar")).status_code, 302)
            session = self.CONFIG[tipo][7].objects.get(cita=cita)
            self.assertEqual(session.estado, "finalizada")
            before = self.conteos()
            response = self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, nota_enfermeria="Intento de modificación"))
            self.assertEqual(response.status_code, 302)
            session.refresh_from_db()
            self.assertEqual(session.cita_id, cita.pk)
            self.assertEqual(session.nota_enfermeria, self.datos(tipo, cita)["nota_enfermeria"])
            self.assertEqual(self.conteos(), before)
            locked = self.client.get(self.url(tipo, cita))
            self.assertTrue(locked.context["appointment_control_readonly"])
            self.assertTrue(all(field.disabled for form in self.formularios(locked, tipo) for field in form.fields.values()))
            self.assertNotContains(locked, 'method="post"')

    def test_incomplete_finalize_keeps_original_automatic_draft_and_bound_errors(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            data = self.datos(tipo, cita, accion="finalizar", firma_enfermeria="", nota_enfermeria="Trabajo conservado automáticamente")
            response = self.client.post(self.url(tipo, cita, save=True), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["appointment_control_draft_autosaved"])
            program_form, session_form = self.formularios(response, tipo)
            self.assertTrue(program_form.is_bound)
            self.assertTrue(session_form.is_bound)
            self.assertIn("firma_enfermeria", session_form.errors)
            self.assertEqual(session_form.data["nota_enfermeria"], data["nota_enfermeria"])
            session = self.CONFIG[tipo][7].objects.get(cita=cita)
            self.assertEqual((session.estado, session.cita_id, session.numero_sesion), ("borrador", cita.pk, cita.sesion_servicio))
            self.assertEqual(session.nota_enfermeria, data["nota_enfermeria"])
            self.assertEqual(session.firma_enfermeria, "")

    def test_invalid_program_or_invalid_original_draft_does_not_autosave(self):
        before = self.conteos()
        cases = [(self.TIPOS[0], {"programa": "otro", "programa_otro": ""}), (self.TIPOS[1], {"cuidados_realizados": ["otro"], "cuidado_otro": ""})]
        for tipo, extra in cases:
            response = self.client.post(self.url(tipo, self.cita(tipo), save=True), self.datos(tipo, accion="finalizar", **extra))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["appointment_control_draft_autosaved"])
            self.assertTrue(any(form.errors for form in self.formularios(response, tipo)))
        self.assertEqual(self.conteos(), before)

    def test_duplicate_primary_or_additional_sessions_prevent_autosave_and_keep_other_record(self):
        for tipo in self.TIPOS:
            cita = self.cita(tipo, "draft")
            program = self.programas[(self.empresa.slug, tipo, "draft")]
            self.CONFIG[tipo][7].objects.create(programa=program, empresa=self.empresa, paciente=cita.paciente, numero_sesion=7, **({"fase": 1} if tipo == self.TIPOS[1] else {}))
            before = self.conteos()
            response = self.client.post(self.url(tipo, cita, save=True), self.datos(tipo, cita, numero_sesion_adicional="7", accion="finalizar", firma_enfermeria=""))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["appointment_control_draft_autosaved"])
            self.assertTrue(self.formularios(response, tipo)[1].errors)
            self.assertEqual(self.conteos(), before)

    def test_save_and_automatic_draft_transactions_roll_back_audit_on_original_session_failure(self):
        for tipo in self.TIPOS:
            for overrides in ({}, {"accion": "finalizar", "firma_enfermeria": ""}):
                before = self.conteos()
                with patch.object(self.CONFIG[tipo][7], "save", side_effect=RuntimeError("Fallo sintético de sesión original")):
                    with self.assertRaises(RuntimeError):
                        self.client.post(self.url(tipo, self.cita(tipo), save=True), self.datos(tipo, **overrides))
                self.assertEqual(self.conteos(), before)

    def test_app_and_original_web_save_equivalent_forms_state_and_appointment_authority(self):
        for tipo in self.TIPOS:
            app_cita = self.cita(tipo)
            web_patient = Paciente.objects.create(empresa=self.empresa, nombre="Paciente web atención sintética", expediente_codigo="MAC-WEB-"+tipo)
            web_cita = CitaCliente.objects.create(empresa=self.empresa, paciente=web_patient, servicio_clinico=app_cita.servicio_clinico, profesional_salud=app_cita.profesional_salud, titulo=app_cita.titulo, fecha_hora=app_cita.fecha_hora, sesion_servicio=app_cita.sesion_servicio, fase_servicio=app_cita.fase_servicio)
            data = self.datos(tipo)
            self.assertEqual(self.client.post(self.url(tipo, app_cita, save=True), data).status_code, 302)
            del data["app"]
            web = self.client.post(self.url(tipo, web_cita, save=True, mobile=False), data)
            self.assertEqual(web.status_code, 302)
            self.assertNotIn("app=1", web.url)
            app_session, web_session = self.CONFIG[tipo][7].objects.get(cita=app_cita), self.CONFIG[tipo][7].objects.get(cita=web_cita)
            for name in self.CONFIG[tipo][9]().fields:
                self.assertEqual(getattr(app_session, name), getattr(web_session, name), name)
            for name in self.CONFIG[tipo][8]().fields:
                self.assertEqual(getattr(app_session.programa, name), getattr(web_session.programa, name), name)

    def test_phase_nine_direct_control_route_and_template_remain_unchanged(self):
        self.asignar_rol(puede_escribir_camara_hiperbarica=True, puede_escribir_postquirurgicas=True)
        for tipo in self.TIPOS:
            paciente = self.cita(tipo).paciente
            direct = reverse("clinica_registrar_control_especial", args=[self.empresa.slug, paciente.pk, tipo])+"?app=1"
            response = self.client.get(direct)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_control_form.html")
            self.assertNotContains(response, 'name="cita_id"')

    def test_selected_forms_do_not_modify_inventory_patients_recipes_or_appointments(self):
        models = (Paciente, ProfesionalSalud, ServicioClinico, OpcionServicioAgenda, ConfiguracionCRM, NotificacionCitaWhatsApp, PreconsultaClinica, HistoriaClinicaEspecialidad, RecetaMedica, CitaCliente, Factura, MovimientoInventario)
        before = tuple(model.objects.count() for model in models)
        for tipo in self.TIPOS:
            self.assertEqual(self.client.post(self.url(tipo, self.cita(tipo), save=True), self.datos(tipo)).status_code, 302)
        self.assertEqual(tuple(model.objects.count() for model in models), before)

    def test_both_mobile_mutations_keep_csrf_and_save_routes_post_only(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        for tipo in self.TIPOS:
            cita = self.cita(tipo)
            self.assertEqual(protected.get(self.url(tipo, cita)).status_code, 200)
            before = self.conteos()
            denied = protected.post(self.url(tipo, cita, save=True), self.datos(tipo))
            self.assertEqual(denied.status_code, 302)
            self.assertEqual(denied.url, reverse("empresa_login", args=[self.empresa.slug]))
            self.assertEqual(self.conteos(), before)
            self.assertIn(protected.get(self.url(tipo, cita, save=True)).status_code, (403, 405))
            token = protected.cookies["csrftoken"].value
            self.assertEqual(protected.post(self.url(tipo, cita, save=True), self.datos(tipo), HTTP_X_CSRFTOKEN=token).status_code, 302)

    def test_new_hub_cards_require_read_without_calendar_or_patient_permissions(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for tipo, key in zip(self.TIPOS, ("camara_citas", "postquirurgicas_citas")):
            self.asignar_rol(**{self.CONFIG[tipo][4]: True})
            response = self.client.get(hub)
            self.assertEqual(response.status_code, 200)
            card = next(item for item in response.context["modulos_app"] if item["key"] == key)
            self.assertTrue(card["url"])
            self.assertIn("app=1", card["url"])
            self.assertEqual(self.client.get(card["url"]).status_code, 200)
            self.asignar_rol(**{self.CONFIG[tipo][5]: True})
            response = self.client.get(hub)
            self.assertFalse(any(item["key"] == key for item in response.context["modulos_app"]))
