from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.forms import ProgramaCamaraHiperbaricaForm, ProgramaTerapiaPostQuirurgicaForm, SesionCamaraHiperbaricaForm, SesionTerapiaPostQuirurgicaForm
from crm.models import CitaCliente, OpcionServicioAgenda, ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionCamaraHiperbarica, SesionTerapiaPostQuirurgica
from facturacion.models import Factura, MovimientoInventario
from . import views
from .models import HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica, ServicioClinico


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileControlsIntegrationTests(TestCase):
    TIPOS = ("camara_hiperbarica", "terapias_postquirurgicas")
    CONFIG = {
        "camara_hiperbarica": (ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaCamaraHiperbaricaForm, SesionCamaraHiperbaricaForm, "camara"),
        "terapias_postquirurgicas": (ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica, ProgramaTerapiaPostQuirurgicaForm, SesionTerapiaPostQuirurgicaForm, "terapia"),
    }

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.programas, cls.sesiones, cls.finalizadas = {}, {}, {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        for index, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Controles móvil {slug}", slug=slug, rtn=f"MC-{index}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=cls.modulo, defaults={"activo": True})
            paciente = Paciente.objects.create(empresa=empresa, nombre=f"Paciente control {slug}", expediente_codigo=f"MC-{index}", sexo="femenino")
            cls.pacientes[slug] = paciente
            for tipo, (program_model, session_model, *_rest) in cls.CONFIG.items():
                key = (slug, tipo)
                programa = program_model.objects.create(empresa=empresa, paciente=paciente, cirugia=f"Procedimiento sintético {slug}", fecha_cirugia="2026-10-01")
                cls.programas[key] = programa
                campos = {"fase": 1} if tipo == "terapias_postquirurgicas" else {}
                cls.sesiones[key] = session_model.objects.create(
                    programa=programa, empresa=empresa, paciente=paciente, numero_sesion=1, numero_sesion_adicional=2,
                    nota_enfermeria=f"Nota original privada {slug} {tipo}", **campos,
                )
                cls.finalizadas[key] = session_model.objects.create(
                    programa=programa, empresa=empresa, paciente=paciente, numero_sesion=3, estado="finalizada",
                    nota_enfermeria=f"Finalizada conservada {slug} {tipo}", **campos,
                )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Controles móvil completo", codigo="mobile-controls-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_escribir_camara_hiperbarica=True, puede_escribir_postquirurgicas=True,
            puede_ver_camara_hiperbarica=True, puede_ver_postquirurgicas=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-controls-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.otro_paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Otro paciente de control", expediente_codigo="MC-OTHER")

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("clinica.views.EmailMultiAlternatives.send", "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            patcher = patch(target)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mock.assert_not_called)

    def asignar_rol(self, empresa=None, granular=True, **permisos):
        rol = RolSistema.objects.create(
            nombre="Controles móvil restringido", codigo=f"mobile-controls-role-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, tipo="camara_hiperbarica", empresa=None, paciente=None, board=False, more=False, mobile=True, **params):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        route = "clinica_agregar_sesion_terapia_postquirurgica" if more else "clinica_crear_historia_especialidad" if board else "clinica_registrar_control_especial"
        args = [empresa.slug, paciente.pk] + ([] if more else [tipo])
        query = {"app": "1", **params} if mobile else params
        return reverse(route, args=args)+("?"+urlencode(query) if query else "")

    def datos(self, tipo="camara_hiperbarica", **overrides):
        data = {
            "app": "1", "accion": "borrador", "programa_id": "", "sesion_id": "", "cirugia": "Procedimiento sintético conservado", "fecha_cirugia": "2026-10-02",
            "numero_sesion": "6", "numero_sesion_adicional": "7", "nota_enfermeria": "Nota sintética conservada\nSegunda línea", "firma_enfermeria": "Personal de prueba",
        }
        if tipo == "camara_hiperbarica":
            data.update({name: "si" for name in SesionCamaraHiperbaricaForm.CAMPOS_SI_NO})
            data.update({"programa": "20x45", "indicacion": "Indicación sintética", "orden_medica": "Texto original de orden",
                         "observaciones_previas": "Control sintético", "firma_control_previo": "Personal de prueba",
                         "presion_arterial_antes": "120/80", "saturacion_oxigeno_antes": "98%", "presion_camara": "2 ATA", "tiempo_minutos": "45",
                         "compensacion_oidos": "Texto de prueba", "tolerancia": "buena", "presion_arterial_despues": "118/78",
                         "saturacion_oxigeno_despues": "99%", "evolucion_evento_adverso": "Texto de evolución sintético", "firma_parametros": "Personal de prueba"})
        else:
            data.update({"fase": "2", "hora_inicio": "10:00", "hora_finalizacion": "11:00", "presion_arterial": "120/80",
                         "frecuencia_cardiaca": "76", "frecuencia_respiratoria": "18", "saturacion_oxigeno": "98", "temperatura": "36.5", "escala_dolor": "3",
                         "estado_paciente": ["bueno", "edema"], "equipos_utilizados": ["usg", "presoterapia"], "minutos_area": "Texto de minutos y área",
                         "cuidados_realizados": ["drenaje_linfatico", "otro"], "cuidado_otro": "Otro cuidado sintético", "enfermera_nombre": "Personal de prueba"})
        data.update(overrides)
        return data

    def conteos(self):
        return tuple(model.objects.count() for model in (
            ProgramaCamaraHiperbarica, SesionCamaraHiperbarica, ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica,
            Paciente, ProfesionalSalud, PreconsultaClinica, HistoriaClinicaEspecialidad, RecetaMedica, ServicioClinico,
            OpcionServicioAgenda, CitaCliente, Factura, MovimientoInventario, RegistroAuditoria,
        ))

    def directo(self, tipo="camara_hiperbarica", board=False, more=False, empresa=None, paciente=None, method="get", data=None, user=None, **params):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        factory = RequestFactory()
        url = self.url(tipo, empresa, paciente, board=board, more=more, **params)
        request = factory.get(url) if method == "get" else factory.post(url, data or {"app": "1"})
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        kwargs = {"empresa_slug": empresa.slug, "paciente_id": paciente.pk}
        if not more:
            kwargs["tipo"] = tipo
        view = views.agregar_sesion_terapia_postquirurgica if more else views.crear_historia_especialidad if board else views.registrar_control_especial_desde_historial
        return view(request, **kwargs)

    def formularios(self, response, tipo):
        suffix = self.CONFIG[tipo][-1]
        return response.context[f"programa_{suffix}_form"], response.context[f"sesion_{suffix}_form"]

    def test_exact_four_companies_mobile_boards_forms_keep_original_web_templates(self):
        before = self.conteos()
        for empresa in self.empresas.values():
            for tipo in self.TIPOS:
                for board in (False, True):
                    with self.subTest(empresa=empresa.slug, tipo=tipo, board=board):
                        response = self.client.get(self.url(tipo, empresa=empresa, board=board))
                        self.assertEqual(response.status_code, 200)
                        self.assertTemplateUsed(response, "clinica/mobile_control_board.html" if board else "clinica/mobile_control_form.html")
                        self.assertIn("no-store", response["Cache-Control"])
                        self.assertNotContains(response, "<table")
                        web = self.client.get(self.url(tipo, empresa=empresa, board=board, mobile=False))
                        self.assertEqual(web.status_code, 200)
                        original = "historia_camara_hiperbarica.html" if tipo == self.TIPOS[0] else "historia_terapias_postquirurgicas.html"
                        self.assertTemplateUsed(web, f"clinica/{original}" if board else f"crm/{'camara_hiperbarica' if tipo == self.TIPOS[0] else 'terapias_postquirurgicas'}.html")
        self.assertEqual(self.conteos(), before)

    def test_other_clinic_app_flag_does_not_replace_original_web(self):
        empresa = Empresa.objects.create(nombre="Clínica ajena a cuatro", slug="otra_clinica_controls", rtn="MC-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente clínica adicional", expediente_codigo="MC-OUT")
        self.asignar_rol(empresa=empresa, granular=False, puede_pacientes=True, puede_expediente_clinico=True)
        for board in (False, True):
            response = self.client.get(self.url(empresa=empresa, paciente=paciente, board=board))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/historia_camara_hiperbarica.html" if board else "crm/camara_hiperbarica.html")
            self.assertFalse(response.context.get("modo_app", False))

    def test_passive_get_never_seeds_programs_patients_professionals_services_or_audit(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            for board in (False, True):
                for paciente in (self.paciente, self.otro_paciente):
                    self.assertEqual(self.client.get(self.url(tipo, paciente=paciente, board=board)).status_code, 200)
        self.assertEqual(self.conteos(), before)

    def test_forms_are_original_exact_fields_choices_and_iso_date_time(self):
        for tipo in self.TIPOS:
            params = {"sesion": 8, **({"fase": 2} if tipo == self.TIPOS[1] else {})}
            response = self.client.get(self.url(tipo, **params))
            web = self.client.get(self.url(tipo, mobile=False, **params))
            forms = self.formularios(response, tipo)
            original_forms = self.formularios(web, tipo)
            for form, original, expected in zip(forms, original_forms, self.CONFIG[tipo][2:4]):
                self.assertIsInstance(form, expected)
                self.assertEqual(list(form.fields), list(original.fields))
                for name in form.fields:
                    self.assertContains(response, f'name="{name}"')
                    if hasattr(form.fields[name], "choices"):
                        self.assertEqual(list(form.fields[name].choices), list(original.fields[name].choices))
            self.assertEqual(forms[0].fields["fecha_cirugia"].input_formats, ["%Y-%m-%d"])
            self.assertEqual(forms[1].initial["numero_sesion"], 8)
            if tipo == self.TIPOS[1]:
                self.assertEqual(forms[1].fields["hora_inicio"].input_formats, ["%H:%M"])
                self.assertEqual(forms[1].initial["fase"], 2)
            self.assertContains(response, 'name="app" value="1"', count=1)

    def test_specific_writers_do_not_grant_other_type_general_history_or_calendar(self):
        for permission, allowed in (("puede_escribir_camara_hiperbarica", self.TIPOS[0]), ("puede_escribir_postquirurgicas", self.TIPOS[1])):
            self.asignar_rol(**{permission: True})
            for tipo in self.TIPOS:
                for board in (False, True):
                    response = self.client.get(self.url(tipo, board=board))
                    if tipo == allowed:
                        self.assertEqual(response.status_code, 200)
                        self.assertFalse(response.context["app_home_disponible"])
                        self.assertNotContains(response, 'rel="manifest"')
                        self.assertFalse(response.context["can_view_history"])
                    else:
                        self.assertIn(response.status_code, (302, 403))
                        with self.assertRaises(PermissionDenied):
                            self.directo(tipo, board=board)

    def test_reader_or_generic_history_permissions_cannot_write_even_without_middleware(self):
        for grants in ({"puede_ver_camara_hiperbarica": True, "puede_ver_postquirurgicas": True},
                       {"puede_crear_historia_clinica": True, "puede_editar_historia_clinica": True, "puede_ver_historia_clinica": True}):
            self.asignar_rol(**grants)
            before = self.conteos()
            for tipo in self.TIPOS:
                with self.assertRaises(PermissionDenied):
                    self.directo(tipo, method="post", data=self.datos(tipo))
                with self.assertRaises(PermissionDenied):
                    self.directo(tipo, board=True)
            with self.assertRaises(PermissionDenied):
                self.directo(more=True, method="post", data={"app": "1", "programa_id": self.programas[(self.empresa.slug, self.TIPOS[1])].pk, "fase": "1"})
            self.assertEqual(self.conteos(), before)

    def test_legacy_board_expediente_and_control_patients_are_distinct_safe_returns(self):
        self.asignar_rol(granular=False, puede_clinica=True, puede_expediente_clinico=True)
        for tipo in self.TIPOS:
            response = self.client.get(self.url(tipo, board=True))
            self.assertEqual(response.status_code, 200)
            with self.assertRaises(PermissionDenied):
                self.directo(tipo)
            self.assertNotContains(response, self.url(tipo), html=False)
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True)
        for tipo in self.TIPOS:
            response = self.client.get(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            with self.assertRaises(PermissionDenied):
                self.directo(tipo, board=True)
            saved = self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo))
            self.assertEqual(saved.status_code, 302)
            self.assertEqual(urlsplit(saved.url).path, urlsplit(self.url(tipo, paciente=self.otro_paciente)).path)
            self.assertEqual(parse_qs(urlsplit(saved.url).query)["app"], ["1"])

    def test_direct_guards_require_membership_active_company_and_clinic_module(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        before = self.conteos()
        for board in (False, True):
            with self.assertRaises(Http404):
                self.directo(board=board)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        outsider = Usuario.objects.create_user(username="controls-outsider", password="test", empresa=self.empresas["medical_spa"], rol_sistema=self.rol)
        for board in (False, True):
            with self.assertRaises(PermissionDenied):
                self.directo(board=board, user=outsider)
        self.empresa.activa = False
        self.empresa.save(update_fields=["activa"])
        with self.assertRaises(Http404):
            self.directo()
        self.assertEqual(self.conteos()[:-1], before[:-1])

    def test_company_patient_and_program_session_ids_are_always_scoped(self):
        before = self.conteos()
        foreign_patient = self.pacientes["medical_spa"]
        for tipo in self.TIPOS:
            foreign_program = self.programas[("medical_spa", tipo)]
            foreign_session = self.sesiones[("medical_spa", tipo)]
            for board in (False, True):
                self.assertEqual(self.client.get(self.url(tipo, paciente=foreign_patient, board=board)).status_code, 404)
                response = self.client.get(self.url(tipo, board=board, programa=foreign_program.pk))
                self.assertIn(response.status_code, (403, 404))
            response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=foreign_program.pk, sesion_id=foreign_session.pk))
            self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_malformed_nonexistent_overflow_ids_and_get_post_mismatch_never_create(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            own_program = self.programas[(self.empresa.slug, tipo)]
            for value in ("abc", "0", "-1", "999999", str(2**90), "00"+str(own_program.pk)):
                for board in (False, True):
                    with self.subTest(tipo=tipo, board=board, value=value):
                        self.assertIn(self.client.get(self.url(tipo, board=board, programa=value)).status_code, (403, 404))
                self.assertIn(self.client.post(self.url(tipo), self.datos(tipo, programa_id=value)).status_code, (403, 404))
                self.assertIn(self.client.post(self.url(tipo), self.datos(tipo, programa_id=own_program.pk, sesion_id=value)).status_code, (403, 404))
            mismatch = self.client.post(self.url(tipo, programa=own_program.pk), self.datos(tipo, programa_id=self.programas[("medical_spa", tipo)].pk))
            self.assertIn(mismatch.status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_same_patient_session_cannot_be_substituted_into_another_program(self):
        for tipo in self.TIPOS:
            program_model, *_ = self.CONFIG[tipo]
            other = program_model.objects.create(empresa=self.empresa, paciente=self.paciente, cirugia="Otro programa conservado")
            before = self.conteos()
            own = self.sesiones[(self.empresa.slug, tipo)]
            response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=other.pk, sesion_id=own.pk))
            self.assertIn(response.status_code, (403, 404))
            own.refresh_from_db()
            self.assertNotEqual(own.programa_id, other.pk)
            self.assertEqual(self.conteos(), before)

    def test_mixed_unknown_metadata_and_duplicate_actions_are_rejected_before_writes(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            for extra in ({"accion": "anular"}, {"accion": ""}, {"tipo_historia": "capilar"}, {"paciente": self.otro_paciente.pk},
                          {"empresa": self.empresas["medical_spa"].pk}, {"cita_id": "1"}, {"fecha_sesion": "2000-01-01"}, {"estado": "finalizada"}):
                self.assertIn(self.client.post(self.url(tipo), self.datos(tipo, **extra)).status_code, (403, 404))
            base = QueryDict(mutable=True)
            for key, value in self.datos(tipo).items():
                base.setlist(key, value if isinstance(value, list) else [str(value)])
            for key, values in (("accion", ["borrador", "finalizar"]), ("accion", ["borrador", "borrador"]), ("programa_id", ["1", "2"]), ("sesion_id", ["1", "2"])):
                repeated = base.copy()
                repeated.setlist(key, values)
                response = self.client.post(self.url(tipo), repeated.urlencode(), content_type="application/x-www-form-urlencoded")
                self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.conteos(), before)

    def test_mobile_board_post_cannot_fall_through_to_web_or_mutate(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            self.assertIn(self.client.post(self.url(tipo, board=True), self.datos(tipo)).status_code, (403, 405))
        self.assertEqual(self.conteos(), before)

    def test_cita_linked_session_is_readonly_and_direct_control_never_unlinks_it(self):
        for tipo in self.TIPOS:
            session = self.sesiones[(self.empresa.slug, tipo)]
            cita = CitaCliente.objects.create(empresa=self.empresa, paciente=self.paciente, titulo="Cita vinculada sintética", fecha_hora=timezone.now(), duracion_minutos=30)
            self.CONFIG[tipo][1].objects.filter(pk=session.pk).update(cita=cita)
            before = self.conteos()
            control = self.url(tipo, programa=session.programa_id, sesion=session.numero_sesion, **({"fase": 1} if tipo == self.TIPOS[1] else {}))
            self.assertIn(self.client.get(control).status_code, (403, 404))
            response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=session.programa_id, sesion_id=session.pk))
            self.assertIn(response.status_code, (403, 404))
            board = self.client.get(self.url(tipo, board=True, programa=session.programa_id))
            self.assertEqual(board.status_code, 200)
            self.assertNotContains(board, f"control_camara={cita.pk}")
            self.assertNotContains(board, f"control_terapia={cita.pk}")
            session.refresh_from_db()
            self.assertEqual(session.cita_id, cita.pk)
            self.assertEqual(self.conteos(), before)

    def test_camera_draft_uses_original_program_and_session_fields_actors_and_auto_date(self):
        started = timezone.now()
        response = self.client.post(self.url(paciente=self.otro_paciente), self.datos())
        self.assertEqual(response.status_code, 302)
        session = SesionCamaraHiperbarica.objects.get(paciente=self.otro_paciente)
        self.assertIsNone(session.cita_id)
        self.assertEqual((session.numero_sesion, session.numero_sesion_adicional), (6, 7))
        self.assertEqual(session.nota_enfermeria, self.datos()["nota_enfermeria"])
        self.assertEqual(session.estado, "borrador")
        self.assertGreaterEqual(session.fecha_sesion, started)
        self.assertLessEqual(session.fecha_sesion, timezone.now())
        self.assertEqual((session.creado_por_id, session.actualizado_por_id, session.programa.creado_por_id, session.programa.actualizado_por_id), (self.usuario.pk,)*4)
        self.assertEqual(session.programa.fecha_cirugia.isoformat(), "2026-10-02")
        self.assertEqual(session.programa.programa, "20x45")
        self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo="sesioncamarahiperbarica", objeto_id=str(session.pk), usuario=self.usuario, accion="crear").exists())
        self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])

    def test_postoperative_two_sessions_phase_lists_and_extended_number_reuse_original_save(self):
        response = self.client.post(self.url(self.TIPOS[1], paciente=self.otro_paciente), self.datos(self.TIPOS[1], numero_sesion="31", numero_sesion_adicional="32"))
        self.assertEqual(response.status_code, 302)
        session = SesionTerapiaPostQuirurgica.objects.get(paciente=self.otro_paciente)
        self.assertEqual((session.fase, session.numero_sesion, session.numero_sesion_adicional), (2, 31, 32))
        self.assertEqual(session.programa.sesiones_habilitadas, 32)
        self.assertEqual(session.estado_paciente, ["bueno", "edema"])
        self.assertEqual(session.equipos_utilizados, ["usg", "presoterapia"])
        self.assertEqual(session.cuidados_realizados, ["drenaje_linfatico", "otro"])
        self.assertEqual(session.cuidado_otro, "Otro cuidado sintético")
        self.assertIsNone(session.cita_id)
        self.assertEqual(session.creado_por_id, self.usuario.pk)
        self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo="sesionterapiapostquirurgica", objeto_id=str(session.pk), usuario=self.usuario, accion="crear").exists())

    def test_app_and_original_web_save_equivalent_canonical_fields(self):
        for tipo, (program_model, session_model, program_form, session_form, _suffix) in self.CONFIG.items():
            web_patient = Paciente.objects.create(empresa=self.empresa, nombre="Paciente web sintético", expediente_codigo="MC-WEB-"+tipo)
            data = self.datos(tipo)
            app_response = self.client.post(self.url(tipo, paciente=self.otro_paciente), data)
            web_data = {key: value for key, value in data.items() if key != "app"}
            web_data["accion"] = "borrador"
            web_response = self.client.post(self.url(tipo, paciente=web_patient, mobile=False), web_data)
            self.assertEqual((app_response.status_code, web_response.status_code), (302, 302))
            app_session = session_model.objects.get(paciente=self.otro_paciente)
            web_session = session_model.objects.get(paciente=web_patient)
            for field in session_form().fields:
                self.assertEqual(getattr(app_session, field), getattr(web_session, field), field)
            for field in program_form().fields:
                self.assertEqual(getattr(app_session.programa, field), getattr(web_session.programa, field), field)
            self.assertEqual((app_session.estado, web_session.estado), ("borrador", "borrador"))
            self.assertNotIn("app=1", web_response.url)

    def test_finalize_then_locked_post_preserves_session_program_and_audit(self):
        for tipo in self.TIPOS:
            response = self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo, accion="finalizar"))
            self.assertEqual(response.status_code, 302)
            session = self.CONFIG[tipo][1].objects.get(paciente=self.otro_paciente)
            self.assertEqual(session.estado, "finalizada")
            before = self.conteos()
            post = self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo, programa_id=session.programa_id, sesion_id=session.pk, nota_enfermeria="Intento de modificación", cirugia="Intento cirugía"))
            self.assertIn(post.status_code, (200, 302))
            session.refresh_from_db()
            self.assertEqual(session.nota_enfermeria, self.datos(tipo)["nota_enfermeria"])
            self.assertEqual(session.programa.cirugia, self.datos(tipo)["cirugia"])
            self.assertEqual(session.estado, "finalizada")
            self.assertEqual(self.conteos(), before)
            locked = self.client.get(self.url(tipo, paciente=self.otro_paciente, programa=session.programa_id, sesion=session.numero_sesion, **({"fase": session.fase} if tipo == self.TIPOS[1] else {})))
            self.assertEqual(locked.status_code, 200)
            self.assertTrue(all(field.disabled for field in self.formularios(locked, tipo)[1].fields.values()))

    def test_invalid_finalize_keeps_bound_fields_but_does_not_autosave_direct_control(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            response = self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo, accion="finalizar", firma_enfermeria=""))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_control_form.html")
            program_form, session_form = self.formularios(response, tipo)
            self.assertTrue(program_form.is_bound)
            self.assertTrue(session_form.is_bound)
            self.assertIn("firma_enfermeria", session_form.errors)
            self.assertEqual(session_form.data["nota_enfermeria"], self.datos(tipo)["nota_enfermeria"])
            self.assertEqual(program_form.data["fecha_cirugia"], "2026-10-02")
            if tipo == self.TIPOS[1]:
                self.assertEqual(session_form.data.getlist("equipos_utilizados"), ["usg", "presoterapia"])
            else:
                self.assertEqual(session_form.data.getlist("sin_fiebre"), ["si"])
        self.assertEqual(self.conteos(), before)

    def test_invalid_existing_draft_preserves_record_and_submitted_work(self):
        for tipo in self.TIPOS:
            session = self.sesiones[(self.empresa.slug, tipo)]
            before = self.conteos()
            response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=session.programa_id, sesion_id=session.pk, accion="finalizar", firma_enfermeria=""))
            self.assertEqual(response.status_code, 200)
            self.assertIn("firma_enfermeria", self.formularios(response, tipo)[1].errors)
            session.refresh_from_db()
            self.assertEqual(session.nota_enfermeria, f"Nota original privada {self.empresa.slug} {tipo}")
            self.assertEqual(session.estado, "borrador")
            self.assertEqual(self.conteos(), before)

    def test_original_number_limits_duplicates_additional_overlap_and_phase_isolation(self):
        for tipo in self.TIPOS:
            program = self.programas[(self.empresa.slug, tipo)]
            before = self.conteos()
            samples = [{"numero_sesion": "0"}, {"numero_sesion": "6", "numero_sesion_adicional": "6"},
                       {"numero_sesion": "1", "numero_sesion_adicional": "6"}, {"numero_sesion": "6", "numero_sesion_adicional": "2"}]
            if tipo == self.TIPOS[0]:
                samples += [{"numero_sesion": "23"}, {"numero_sesion_adicional": "23"}]
            for extra in samples:
                response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=program.pk, **{"fase": "1"} if tipo == self.TIPOS[1] else {}, **extra))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(self.formularios(response, tipo)[1].errors)
            self.assertEqual(self.conteos(), before)
        program = self.programas[(self.empresa.slug, self.TIPOS[1])]
        response = self.client.post(self.url(self.TIPOS[1]), self.datos(self.TIPOS[1], programa_id=program.pk, fase="2", numero_sesion="1", numero_sesion_adicional=""))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SesionTerapiaPostQuirurgica.objects.filter(programa=program, numero_sesion=1).count(), 2)

    def test_original_program_other_care_pain_and_phase_validations_are_not_replaced(self):
        before = self.conteos()
        cases = [(self.TIPOS[0], {"programa": "otro", "programa_otro": ""}, "programa_otro", 0),
                 (self.TIPOS[1], {"cuidados_realizados": ["otro"], "cuidado_otro": ""}, "cuidado_otro", 1),
                 (self.TIPOS[1], {"escala_dolor": "11"}, "escala_dolor", 1), (self.TIPOS[1], {"fase": "3"}, "fase", 1)]
        for tipo, extra, field, form_index in cases:
            response = self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo, **extra))
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, self.formularios(response, tipo)[form_index].errors)
        self.assertEqual(self.conteos(), before)

    def test_more_sessions_is_post_only_authorized_and_does_not_create_a_session(self):
        program = self.programas[(self.empresa.slug, self.TIPOS[1])]
        before = self.conteos()
        self.assertIn(self.client.get(self.url(more=True)).status_code, (403, 405))
        response = self.client.post(self.url(more=True), {"app": "1", "programa_id": program.pk, "fase": "2"})
        self.assertEqual(response.status_code, 302)
        program.refresh_from_db()
        self.assertEqual(program.sesiones_habilitadas, 31)
        self.assertEqual(self.conteos()[:-1], before[:-1])
        self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])
        self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo="programaterapiapostquirurgica", objeto_id=str(program.pk), usuario=self.usuario, accion="modificar").exists())

    def test_more_sessions_rejects_forged_explicit_missing_and_malformed_program_ids(self):
        before = self.conteos()
        for value in ("abc", "0", "999999", str(2**90), str(self.programas[("medical_spa", self.TIPOS[1])].pk)):
            response = self.client.post(self.url(more=True), {"app": "1", "programa_id": value, "fase": "1"})
            self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.client.post(self.url(more=True), {"app": "1", "fase": "1"}).status_code, 403)
        self.assertEqual(self.conteos(), before)

    def test_more_sessions_blank_id_preserves_original_authorized_first_program_creation(self):
        before_sessions = SesionTerapiaPostQuirurgica.objects.count()
        response = self.client.post(self.url(more=True, paciente=self.otro_paciente), {"app": "1", "programa_id": "", "fase": "1"})
        self.assertEqual(response.status_code, 302)
        programa = ProgramaTerapiaPostQuirurgica.objects.get(paciente=self.otro_paciente)
        self.assertEqual(programa.sesiones_habilitadas, 31)
        self.assertEqual(programa.creado_por_id, self.usuario.pk)
        self.assertEqual(SesionTerapiaPostQuirurgica.objects.count(), before_sessions)

    def test_existing_draft_edit_preserves_creator_and_timestamp_using_same_audit(self):
        for tipo in self.TIPOS:
            session = self.sesiones[(self.empresa.slug, tipo)]
            created_at = session.fecha_sesion
            original_creator = session.creado_por_id
            response = self.client.post(self.url(tipo), self.datos(tipo, programa_id=session.programa_id, sesion_id=session.pk, numero_sesion="1", numero_sesion_adicional="2", **({"fase": "1"} if tipo == self.TIPOS[1] else {})))
            self.assertEqual(response.status_code, 302)
            session.refresh_from_db()
            self.assertEqual(session.fecha_sesion, created_at)
            self.assertEqual(session.creado_por_id, original_creator)
            self.assertEqual(session.actualizado_por_id, self.usuario.pk)
            self.assertEqual(session.nota_enfermeria, self.datos(tipo)["nota_enfermeria"])
            self.assertTrue(RegistroAuditoria.objects.filter(empresa=self.empresa, modelo=session._meta.model_name, objeto_id=str(session.pk), usuario=self.usuario, accion="modificar").exists())

    def test_patient_picker_requires_list_and_exact_control_permission_not_general_history(self):
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        self.asignar_rol(puede_ver_pacientes=True, puede_escribir_camara_hiperbarica=True)
        response = self.client.get(picker, {"app": "1", "destino": "control_clinico", "tipo": self.TIPOS[0]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["mobile_patient_control_mode"])
        self.assertEqual(response.context["mobile_patient_control_type"]["codigo"], self.TIPOS[0])
        self.assertFalse(response.context["can_view_patient"])
        self.assertFalse(response.context["can_create_history"])
        self.assertContains(response, self.url(board=True).replace("&", "&amp;"))
        for tipo in (self.TIPOS[1], "forged", "capilar"):
            response = self.client.get(picker, {"app": "1", "destino": "control_clinico", "tipo": tipo})
            self.assertFalse(response.context.get("mobile_patient_control_mode"))
        self.asignar_rol(puede_escribir_camara_hiperbarica=True)
        self.assertIn(self.client.get(picker, {"app": "1", "destino": "control_clinico"}).status_code, (302, 403))
        self.assertEqual(self.client.get(self.url(board=True)).status_code, 200)

    def test_control_selector_search_pagination_and_type_links_preserve_intent_and_tenant(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_escribir_camara_hiperbarica=True, puede_escribir_postquirurgicas=True)
        Paciente.objects.bulk_create([Paciente(empresa=self.empresa, nombre=f"Selector control {index:02d}", expediente_codigo=f"MC-SEL-{index:02d}") for index in range(27)])
        before = self.conteos()
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        for page in ("1", "2"):
            response = self.client.get(picker, {"app": "1", "destino": "control_clinico", "tipo": self.TIPOS[1], "q": "Selector control", "page": page})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["mobile_patient_control_mode"])
            self.assertEqual(response.context["mobile_patient_control_type"]["codigo"], self.TIPOS[1])
            self.assertEqual(response.context["pacientes"].paginator.count, 27)
            self.assertContains(response, 'name="destino" value="control_clinico"')
            self.assertContains(response, 'name="tipo" value="terapias_postquirurgicas"')
            self.assertContains(response, "destino=control_clinico&amp;tipo=terapias_postquirurgicas")
            self.assertContains(response, "q=Selector%20control")
            self.assertNotContains(response, self.pacientes["medical_spa"].nombre)
            for paciente in response.context["pacientes"]:
                self.assertContains(response, self.url(self.TIPOS[1], paciente=paciente, board=True).replace("&", "&amp;"))
        self.assertEqual(self.conteos(), before)

    def test_module_cards_never_turn_reader_permissions_into_writing_or_desktop_links(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for permisos, states in (
            ({"puede_ver_camara_hiperbarica": True, "puede_ver_postquirurgicas": True, "puede_ver_pacientes": True}, {"camara": "pendiente", "postquirurgicas": "pendiente"}),
            ({"puede_escribir_camara_hiperbarica": True}, {"camara": "pendiente"}),
            ({"puede_escribir_camara_hiperbarica": True, "puede_ver_pacientes": True}, {"camara": "parcial"}),
            ({"puede_escribir_postquirurgicas": True, "puede_ver_pacientes": True}, {"postquirurgicas": "parcial"}),
        ):
            self.asignar_rol(**permisos)
            response = self.client.get(hub)
            self.assertEqual(response.status_code, 200)
            cards = {item["key"]: item for item in response.context["modulos_app"] if item["key"] in {"camara", "postquirurgicas"}}
            self.assertEqual(set(cards), set(states))
            for key, state in states.items():
                self.assertEqual(cards[key]["estado"], state)
                self.assertEqual(bool(cards[key]["url"]), state == "parcial")
                if cards[key]["url"]:
                    self.assertIn("app=1&destino=control_clinico", cards[key]["url"])
                    self.assertEqual(self.client.get(cards[key]["url"]).status_code, 200)

    def test_legacy_patient_only_picker_goes_to_control_not_forbidden_history_board(self):
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True)
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        response = self.client.get(picker, {"app": "1", "destino": "control_clinico", "tipo": self.TIPOS[1]})
        self.assertEqual(response.status_code, 200)
        chosen = response.context["mobile_patient_control_type"]
        self.assertFalse(chosen["puede_tablero"])
        self.assertTrue(chosen["puede_registrar"])
        self.assertContains(response, self.url(self.TIPOS[1]).replace("&", "&amp;"))
        self.assertNotContains(response, self.url(self.TIPOS[1], board=True).replace("&", "&amp;"))
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertIn("/app/modulos/", response.context["app_modules_url"])

    def test_inconsistent_legacy_session_relationships_cannot_cross_company_on_direct_entry(self):
        for tipo in self.TIPOS:
            session = self.sesiones[(self.empresa.slug, tipo)]
            model = self.CONFIG[tipo][1]
            foreign_program = self.programas[("medical_spa", tipo)]
            model.objects.filter(pk=session.pk).update(programa=foreign_program, numero_sesion=11, numero_sesion_adicional=12)
            before = self.conteos()
            self.assertIn(self.client.post(self.url(tipo), self.datos(tipo, sesion_id=session.pk)).status_code, (403, 404))
            self.assertEqual(self.conteos(), before)
            model.objects.filter(pk=session.pk).update(programa=self.programas[(self.empresa.slug, tipo)], empresa=self.empresas["medical_spa"], paciente=self.pacientes["medical_spa"])
            response = self.client.get(self.url(tipo, sesion=11, **({"fase": 1} if tipo == self.TIPOS[1] else {})))
            self.assertIn(response.status_code, (403, 404))
            board = self.client.get(self.url(tipo, board=True))
            self.assertIn(board.status_code, (403, 404))
            self.assertNotContains(board, self.pacientes["medical_spa"].nombre, status_code=board.status_code)
            self.assertEqual(self.conteos(), before)

    def test_original_save_transaction_rolls_back_program_and_audit_if_session_save_fails(self):
        for tipo in self.TIPOS:
            before = self.conteos()
            with patch.object(self.CONFIG[tipo][1], "save", side_effect=RuntimeError("Fallo sintético del guardado original")):
                with self.assertRaises(RuntimeError):
                    self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo))
            self.assertEqual(self.conteos(), before)

    def test_valid_mobile_mutations_still_require_csrf(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        get = protected.get(self.url(paciente=self.otro_paciente))
        self.assertEqual(get.status_code, 200)
        before = self.conteos()
        denied = protected.post(self.url(paciente=self.otro_paciente), self.datos())
        # The existing CSRF failure handler redirects to the company login.
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.url, reverse("empresa_login", args=[self.empresa.slug]))
        self.assertEqual(self.conteos(), before)
        token = protected.cookies["csrftoken"].value
        self.assertEqual(protected.post(self.url(paciente=self.otro_paciente), self.datos(), HTTP_X_CSRFTOKEN=token).status_code, 302)

    def test_controls_do_not_write_histories_inventory_recipes_patients_or_appointments(self):
        unchanged = tuple(model.objects.count() for model in (HistoriaClinicaEspecialidad, PreconsultaClinica, ProfesionalSalud, RecetaMedica, MovimientoInventario, Factura, Paciente, CitaCliente, ServicioClinico, OpcionServicioAgenda))
        for tipo in self.TIPOS:
            self.assertEqual(self.client.post(self.url(tipo, paciente=self.otro_paciente), self.datos(tipo)).status_code, 302)
        self.assertEqual(tuple(model.objects.count() for model in (HistoriaClinicaEspecialidad, PreconsultaClinica, ProfesionalSalud, RecetaMedica, MovimientoInventario, Factura, Paciente, CitaCliente, ServicioClinico, OpcionServicioAgenda)), unchanged)
