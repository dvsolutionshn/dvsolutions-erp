from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Factura, MovimientoInventario
from . import views
from .forms import HistoriaClinicaEspecialidadForm
from .models import ClasificacionAlopecia, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileHistoriesIntegrationTests(TestCase):
    TIPOS = ("capilar", "cirugia_plastica", "medicina_estetica", "enfermeria", "terapias")

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales, cls.historias = {}, {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=f"Historias móvil {slug}", slug=slug, rtn=f"MH-{indice}", tipo_solucion="clinica")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=cls.modulo, defaults={"activo": True})
            paciente = Paciente.objects.create(empresa=empresa, nombre=f"Paciente historia {slug}", expediente_codigo=f"MH-{indice}", sexo="masculino")
            cls.pacientes[slug] = paciente
            profesional = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional {slug}")
            cls.profesionales[slug] = profesional
            for tipo in cls.TIPOS:
                cls.historias[(slug, tipo)] = HistoriaClinicaEspecialidad.objects.create(
                    empresa=empresa, paciente=paciente, profesional=profesional, tipo=tipo, estado="borrador",
                    plan_tratamiento=f"Plan anterior privado {slug} {tipo}", observaciones=f"Nota anterior {slug} {tipo}",
                )
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Historias móvil completo", codigo="mobile-histories-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_ver_historia_clinica=True, puede_crear_historia_clinica=True, puede_editar_historia_clinica=True,
            puede_escribir_enfermeria=True, puede_escribir_terapias=True,
        )
        cls.usuario = Usuario.objects.create_user(username="mobile-histories-user", password="test", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        ProfesionalSalud.objects.filter(pk=cls.profesionales[cls.empresa.slug].pk).update(usuario=cls.usuario)
        cls.otro_paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Otro paciente misma empresa", expediente_codigo="MH-OTRO", sexo="femenino")
        cls.mujer = Paciente.objects.create(empresa=cls.empresa, nombre="Paciente femenino de prueba", expediente_codigo="MH-FEM", sexo="femenino")
        cls.sin_sexo = Paciente.objects.create(empresa=cls.empresa, nombre="Paciente sexo histórico no indicado", expediente_codigo="MH-SEX", sexo="no_indicado")
        cls.profesional_inactivo = ProfesionalSalud.objects.create(empresa=cls.empresa, nombre="Profesional inactivo", activo=False)

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="dv-mobile-histories-")
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        for target in ("clinica.views.EmailMultiAlternatives.send", "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            patcher = patch(target)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mock.assert_not_called)

    def url(self, tipo="capilar", historia=None, paciente=None, empresa=None, mobile=True):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        args = [empresa.slug, paciente.pk, historia.pk if historia is not None else tipo]
        route = "clinica_editar_historia_especialidad" if historia is not None else "clinica_crear_historia_especialidad"
        return reverse(route, args=args) + ("?app=1" if mobile else "")

    def datos(self, tipo="capilar", empresa=None, paciente=None, **overrides):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        data = {
            "app": "1", "profesional": self.profesionales[empresa.slug].pk, "fecha_atencion": "2026-10-11T10:15",
            "plan_tratamiento": "Texto de historia, diagnóstico y plan conservado\nSegunda línea de prueba", "observaciones": "Nota de bitácora conservada",
            "estado": "borrador",
        }
        if tipo == "capilar":
            data.update({"alopecia_grado": "I", "alopecia_escala": "ludwig" if paciente.sexo == "femenino" else "hamilton_norwood",
                         "capilar_motivo": ["cejas"], "capilar_hallazgos_tricologicos": ["caida_activa"],
                         "capilar_distribucion_tricologica": ["frontal"], "capilar_patron_clinico": ["androgenetico"]})
            if paciente.sexo not in ("masculino", "femenino"):
                data["paciente_sexo_confirmado"] = "femenino"
        data.update(overrides)
        return data

    def asignar_rol(self, empresa=None, granular=True, **permisos):
        rol = RolSistema.objects.create(
            nombre="Historias móvil restringido", codigo=f"mobile-histories-role-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def preconsulta(self, paciente=None, tipo="capilar", **overrides):
        paciente = paciente or self.paciente
        data = {
            "empresa": paciente.empresa, "paciente": paciente, "tipo": tipo, "estado": "completada",
            "token_hash": f"mobile-history-{paciente.pk}-{PreconsultaClinica.objects.count()}", "token_preview": "MH",
            "fecha_expiracion": timezone.now()+timedelta(days=20), "fecha_completada": timezone.now(),
            "motivo_consulta": f"Motivo privado preconsulta {paciente.pk} {tipo}", "antecedentes_personales_detalle": "Antecedente confidencial previo",
        }
        data.update(overrides)
        return PreconsultaClinica.objects.create(**data)

    def conteos(self):
        return tuple(model.objects.count() for model in (
            HistoriaClinicaEspecialidad, ClasificacionAlopecia, Paciente, ProfesionalSalud, PreconsultaClinica, RecetaMedica,
            MovimientoInventario, Factura, RegistroAuditoria,
        ))

    def directo(self, tipo="capilar", historia=None, empresa=None, paciente=None, method="get", data=None, user=None):
        request = getattr(RequestFactory(), method)(self.url(tipo, historia, paciente, empresa), data or {"app": "1"})
        request.user = user or Usuario.objects.get(pk=self.usuario.pk)
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug, self.paciente)
        kwargs = {"empresa_slug": empresa.slug, "paciente_id": paciente.pk}
        kwargs["historia_id" if historia is not None else "tipo"] = historia.pk if historia is not None else tipo
        return (views.editar_historia_especialidad if historia is not None else views.crear_historia_especialidad)(request, **kwargs)

    def test_exact_four_companies_five_forms_keep_original_web_template(self):
        before = self.conteos()
        for empresa in self.empresas.values():
            for tipo in self.TIPOS:
                for historia in (None, self.historias[(empresa.slug, tipo)]):
                    with self.subTest(empresa=empresa.slug, tipo=tipo, edit=historia is not None):
                        response = self.client.get(self.url(tipo, historia, empresa=empresa))
                        self.assertEqual(response.status_code, 200)
                        self.assertTemplateUsed(response, "clinica/mobile_history_form.html")
                        self.assertIn("no-store", response["Cache-Control"])
                        self.assertNotContains(response, "<table")
                        web = self.client.get(self.url(tipo, historia, empresa=empresa, mobile=False))
                        self.assertEqual(web.status_code, 200)
                        self.assertTemplateUsed(web, "clinica/historia_especialidad_form.html")
                        self.assertTemplateNotUsed(web, "clinica/mobile_history_form.html")
        self.assertEqual(self.conteos(), before)

    def test_other_company_app_flag_keeps_original_history_form(self):
        empresa = Empresa.objects.create(nombre="Otra clínica historias", slug="otra_clinica_historias", rtn="MH-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.modulo)
        self.usuario.empresas_acceso.add(empresa)
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente fuera de cuatro", expediente_codigo="MH-OUT", sexo="masculino")
        self.asignar_rol(empresa=empresa, granular=False, puede_expediente_clinico=True)
        response = self.client.get(self.url(paciente=paciente, empresa=empresa))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/historia_especialidad_form.html")
        self.assertFalse(response.context.get("modo_app", False))

    def test_unsupported_special_flows_never_fall_back_to_web_from_app(self):
        self.asignar_rol(puede_crear_historia_clinica=True, puede_editar_historia_clinica=True, puede_escribir_camara_hiperbarica=True, puede_escribir_postquirurgicas=True)
        unsupported = {
            tipo: HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo=tipo, observaciones="Nota especial existente")
            for tipo in ("evaluacion_integral", "tricopigmentacion", "camara_hiperbarica", "terapias_postquirurgicas")
        }
        before = self.conteos()
        # Structured creation has dedicated mobile flows. Evaluation/trico
        # now allow generic text/date editing, never editing their JSON here.
        self.assertEqual(self.client.get(self.url("forged")).status_code, 404)
        with self.assertRaises(Http404):
            self.directo("forged")
        for tipo in ("camara_hiperbarica", "terapias_postquirurgicas"):
            response = self.client.get(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_control_board.html")
            self.assertEqual(self.directo(tipo).status_code, 200)
        for tipo, historia in unsupported.items():
            response = self.client.get(self.url(tipo, historia))
            if tipo in {"evaluacion_integral", "tricopigmentacion"}:
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "clinica/mobile_history_form.html")
                self.assertTrue(response.context["mobile_history_generic_only"])
            else:
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.client.post(self.url(tipo, historia), self.datos(tipo)).status_code, 404)
        for tipo, status, template in (("evaluacion_integral", 302, None), ("tricopigmentacion", 302, None),
                                       ("camara_hiperbarica", 200, "historia_camara_hiperbarica.html"),
                                       ("terapias_postquirurgicas", 200, "historia_terapias_postquirurgicas.html")):
            response = self.client.get(self.url(tipo, mobile=False))
            self.assertEqual(response.status_code, status)
            if template:
                self.assertTemplateUsed(response, "clinica/"+template)
        self.assertEqual(self.conteos(), before)

    def test_exact_original_form_fields_choices_minute_date_and_scoped_professionals(self):
        for tipo in self.TIPOS:
            with self.subTest(tipo=tipo):
                app = self.client.get(self.url(tipo))
                web = self.client.get(self.url(tipo, mobile=False))
                form = app.context["form"]
                self.assertIsInstance(form, HistoriaClinicaEspecialidadForm)
                self.assertEqual(list(form.fields), list(web.context["form"].fields))
                self.assertEqual(form.fields["fecha_atencion"].input_formats, ["%Y-%m-%dT%H:%M"])
                self.assertEqual(form.fields["fecha_atencion"].widget.format, "%Y-%m-%dT%H:%M")
                self.assertEqual(list(form.fields["profesional"].queryset.values_list("pk", flat=True)), [self.profesionales[self.empresa.slug].pk])
                self.assertContains(app, 'name="app" value="1"')
                self.assertContains(app, 'name="fecha_atencion"', count=1)
                if tipo in ("enfermeria", "terapias"):
                    self.assertEqual(list(form.fields), ["profesional", "fecha_atencion", "observaciones", "estado"])

    def test_passive_get_never_creates_preconsulta_professional_history_or_audit(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            self.assertEqual(self.client.get(self.url(tipo)).status_code, 200)
            self.assertEqual(self.client.get(self.url(tipo, self.historias[(self.empresa.slug, tipo)])).status_code, 200)
        self.assertEqual(self.conteos(), before)

    def test_create_permissions_are_specific_to_general_nursing_and_therapy_types(self):
        for permiso, permitted in (("puede_crear_historia_clinica", set(self.TIPOS[:3])), ("puede_escribir_enfermeria", {"enfermeria"}),
                                   ("puede_escribir_terapias", {"terapias"})):
            self.asignar_rol(**{permiso: True})
            before = self.conteos()
            for tipo in self.TIPOS:
                with self.subTest(permiso=permiso, tipo=tipo):
                    response = self.client.get(self.url(tipo))
                    if tipo in permitted:
                        self.assertEqual(response.status_code, 200)
                    else:
                        self.assertIn(response.status_code, (302, 403))
                        with self.assertRaises(PermissionDenied):
                            self.directo(tipo, method="post", data=self.datos(tipo))
            self.assertEqual(self.conteos(), before)

    def test_editor_only_can_edit_five_types_but_cannot_create_or_read_other_histories(self):
        self.asignar_rol(puede_editar_historia_clinica=True)
        for tipo in self.TIPOS:
            historia = self.historias[(self.empresa.slug, tipo)]
            response = self.client.get(self.url(tipo, historia))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["can_view_history"])
            self.assertFalse(response.context.get("ultima_preconsulta"))
            self.assertFalse(response.context.get("historias_previas"))
            self.assertFalse(response.context.get("preconsultas_tipo"))
            self.assertFalse(response.context.get("clasificaciones_alopecia"))
            with self.assertRaises(PermissionDenied):
                self.directo(tipo, method="post", data=self.datos(tipo))
        for route in ("clinica_historias_especialidad", "clinica_historial_clinico_consolidado"):
            self.assertIn(self.client.get(reverse(route, args=[self.empresa.slug, self.paciente.pk])+"?app=1").status_code, (302, 403))

    def test_history_reader_cannot_create_or_edit_and_mobile_actions_stay_hidden(self):
        self.asignar_rol(puede_ver_historia_clinica=True)
        url = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        for tipo in self.TIPOS:
            self.assertNotContains(response, f'href="{self.url(tipo)}"')
            historia = self.historias[(self.empresa.slug, tipo)]
            self.assertNotContains(response, f'href="{self.url(tipo, historia)}"')
            for record in (None, historia):
                with self.assertRaises(PermissionDenied):
                    self.directo(tipo, record, method="post", data=self.datos(tipo))

    def test_legacy_expediente_alone_keeps_existing_histories_mapper_access(self):
        rol = self.asignar_rol(granular=False, puede_expediente_clinico=True)
        for tipo in self.TIPOS:
            self.assertEqual(self.client.get(self.url(tipo)).status_code, 200)
        response = self.client.post(self.url("terapias"), self.datos("terapias"))
        self.assertEqual(response.status_code, 302)
        rol.refresh_from_db()
        self.assertFalse(rol.puede_pacientes)

    def test_legacy_patients_only_does_not_grant_history_create_or_edit(self):
        self.asignar_rol(granular=False, puede_pacientes=True)
        before = self.conteos()
        for tipo in self.TIPOS:
            for historia in (None, self.historias[(self.empresa.slug, tipo)]):
                self.assertIn(self.client.get(self.url(tipo, historia)).status_code, (302, 403))
                with self.assertRaises(PermissionDenied):
                    self.directo(tipo, historia, method="post", data=self.datos(tipo))
        self.assertEqual(self.conteos(), before)

    def test_company_membership_missing_role_active_company_and_clinic_module_guards(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresas["medical_spa"], rol_sistema=None)
        with self.assertRaises(PermissionDenied):
            self.directo(empresa=self.empresas["medical_spa"])
        no_access = Usuario.objects.create_user(username="mobile-histories-no-company", rol_sistema=self.rol)
        with self.assertRaises(PermissionDenied):
            self.directo(user=no_access)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            self.directo(method="post", data=self.datos())
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        with self.assertRaises(Http404):
            self.directo()

    def test_foreign_patient_record_and_same_company_wrong_patient_are_scoped(self):
        before = self.conteos()
        for paciente, historia in ((self.pacientes["medical_spa"], None), (self.paciente, self.historias[("medical_spa", "capilar")]),
                                    (self.otro_paciente, self.historias[(self.empresa.slug, "capilar")])):
            for method in ("get", "post"):
                response = getattr(self.client, method)(self.url(historia=historia, paciente=paciente), self.datos() if method == "post" else {})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.conteos(), before)

    def test_foreign_inactive_professional_is_rejected_without_losing_fields(self):
        before = self.conteos()
        for profesional in (self.profesionales["medical_spa"], self.profesional_inactivo):
            response = self.client.post(self.url("cirugia_plastica"), self.datos("cirugia_plastica", profesional=profesional.pk))
            self.assertEqual(response.status_code, 200)
            self.assertIn("profesional", response.context["form"].errors)
            self.assertEqual(response.context["form"]["plan_tratamiento"].value(), self.datos()["plan_tratamiento"])
        self.assertEqual(self.conteos(), before)

    def test_create_five_types_uses_original_form_helpers_model_actor_and_audit(self):
        before = self.conteos()
        for tipo in self.TIPOS:
            with self.subTest(tipo=tipo), patch("clinica.views._guardar_clasificacion_alopecia", wraps=views._guardar_clasificacion_alopecia) as classify, \
                 patch("clinica.views._actualizar_sexo_paciente_desde_capilar", wraps=views._actualizar_sexo_paciente_desde_capilar) as sync:
                response = self.client.post(self.url(tipo), self.datos(tipo))
                self.assertEqual(response.status_code, 302)
                self.assertIn("no-store", response["Cache-Control"])
                historia = HistoriaClinicaEspecialidad.objects.filter(empresa=self.empresa, paciente=self.paciente, tipo=tipo).latest("pk")
                self.assertEqual((historia.creado_por_id, historia.actualizado_por_id, historia.profesional_id), (self.usuario.pk, self.usuario.pk, self.profesionales[self.empresa.slug].pk))
                self.assertEqual(timezone.localtime(historia.fecha_atencion).strftime("%Y-%m-%dT%H:%M"), "2026-10-11T10:15")
                self.assertEqual(historia.observaciones if tipo in ("enfermeria", "terapias") else historia.plan_tratamiento,
                                 self.datos(tipo)["observaciones" if tipo in ("enfermeria", "terapias") else "plan_tratamiento"])
                classify.assert_called_once()
                sync.assert_called_once()
                audit = RegistroAuditoria.objects.get(modelo="historiaclinicaespecialidad", objeto_id=str(historia.pk), accion="crear", metodo_http="POST")
                self.assertEqual((audit.usuario_id, audit.empresa_id), (self.usuario.pk, self.empresa.pk))
        self.assertEqual(self.conteos()[2:-1], before[2:-1])

    def test_edit_keeps_creator_creation_date_hidden_history_fields_and_updates_audit(self):
        historia = self.historias[(self.empresa.slug, "cirugia_plastica")]
        original_creator = Usuario.objects.create_user(username="mobile-histories-original-creator", empresa=self.empresa)
        old_date = timezone.now()-timedelta(days=10)
        HistoriaClinicaEspecialidad.objects.filter(pk=historia.pk).update(
            creado_por=original_creator, fecha_creacion=old_date, motivo_consulta="Motivo histórico que no se muestra",
            notas_privadas_doctor="Nota histórica interna retenida", datos_especialidad={"retirado": ["valor antiguo"]},
        )
        response = self.client.post(self.url("cirugia_plastica", historia), self.datos("cirugia_plastica", plan_tratamiento="Plan actualizado"))
        self.assertEqual(response.status_code, 302)
        historia.refresh_from_db()
        self.assertEqual(historia.creado_por_id, original_creator.pk)
        self.assertEqual(historia.fecha_creacion, old_date)
        self.assertEqual(historia.actualizado_por_id, self.usuario.pk)
        self.assertEqual(historia.motivo_consulta, "Motivo histórico que no se muestra")
        self.assertEqual(historia.notas_privadas_doctor, "Nota histórica interna retenida")
        self.assertEqual(historia.datos_especialidad["retirado"], ["valor antiguo"])
        audit = RegistroAuditoria.objects.get(modelo="historiaclinicaespecialidad", objeto_id=str(historia.pk), accion="modificar", metodo_http="POST")
        self.assertEqual(audit.cambios["plan_tratamiento"]["nuevo"], "Plan actualizado")

    def test_capilar_removed_questions_and_retired_choices_remain_after_edit(self):
        historia = self.historias[(self.empresa.slug, "capilar")]
        legacy = {"capilar_inicio": "1_3_anos", "capilar_estado_actual": "empeorando", "capilar_mujeres": ["cambios_hormonales"]}
        HistoriaClinicaEspecialidad.objects.filter(pk=historia.pk).update(datos_especialidad={**legacy, "capilar_motivo": ["caida_excesiva", "entradas"]})
        response = self.client.get(self.url(historia=historia))
        for field in legacy:
            self.assertNotContains(response, f'name="{field}"')
        self.assertEqual(self.client.post(self.url(historia=historia), self.datos(capilar_motivo=["cejas"])).status_code, 302)
        historia.refresh_from_db()
        for field, value in legacy.items():
            self.assertEqual(historia.datos_especialidad[field], value)
        self.assertEqual(historia.datos_especialidad["capilar_motivo"], ["cejas", "entradas"])

    def test_unknown_sex_requires_confirmation_and_invalid_post_preserves_work(self):
        before = self.conteos()
        data = self.datos(paciente=self.sin_sexo)
        del data["paciente_sexo_confirmado"]
        response = self.client.post(self.url(paciente=self.sin_sexo), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("paciente_sexo_confirmado", response.context["form"].errors)
        self.assertEqual(response.context["form"]["plan_tratamiento"].value(), data["plan_tratamiento"])
        self.assertEqual(response.context["form"]["capilar_hallazgos_tricologicos"].value(), ["caida_activa"])
        self.assertEqual(self.conteos(), before)

    def test_capilar_other_pattern_validation_preserves_selections_notes_and_no_writes(self):
        before = self.conteos()
        response = self.client.post(self.url(), self.datos(capilar_patron_clinico=["otro"], capilar_patron_clinico_otros=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("capilar_patron_clinico_otros", response.context["form"].errors)
        self.assertEqual(response.context["form"]["capilar_patron_clinico"].value(), ["otro"])
        self.assertEqual(response.context["form"]["plan_tratamiento"].value(), self.datos()["plan_tratamiento"])
        self.assertEqual(self.conteos(), before)

    def test_capilar_invalid_grade_for_confirmed_sex_never_writes(self):
        before = self.conteos()
        response = self.client.post(self.url(paciente=self.sin_sexo), self.datos(paciente=self.sin_sexo, paciente_sexo_confirmado="femenino", alopecia_grado="VII"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("alopecia_grado", response.context["form"].errors)
        self.assertEqual(self.conteos(), before)

    def test_known_patient_sex_selects_canonical_scale_and_cannot_be_overwritten_by_post(self):
        for paciente, expected in ((self.paciente, "hamilton_norwood"), (self.mujer, "ludwig")):
            response = self.client.post(self.url(paciente=paciente), self.datos(paciente=paciente, alopecia_escala="ludwig" if expected == "hamilton_norwood" else "hamilton_norwood", paciente_sexo_confirmado="femenino" if paciente == self.paciente else "masculino"))
            self.assertEqual(response.status_code, 302)
            clasificacion = ClasificacionAlopecia.objects.filter(paciente=paciente).latest("pk")
            self.assertEqual(clasificacion.escala, expected)
            original = paciente.sexo
            paciente.refresh_from_db()
            self.assertEqual(paciente.sexo, original)

    def test_unknown_sex_classification_and_patient_sync_reuse_original_helper_and_audit(self):
        self.asignar_rol(puede_crear_historia_clinica=True)
        response = self.client.post(self.url(paciente=self.sin_sexo), self.datos(paciente=self.sin_sexo, paciente_sexo_confirmado="femenino", alopecia_grado="II"))
        self.assertEqual(response.status_code, 302)
        self.sin_sexo.refresh_from_db()
        self.assertEqual(self.sin_sexo.sexo, "femenino")
        clasificacion = ClasificacionAlopecia.objects.get(paciente=self.sin_sexo)
        self.assertEqual((clasificacion.escala, clasificacion.grado, clasificacion.creado_por_id), ("ludwig", "II", self.usuario.pk))
        self.assertEqual(clasificacion.historia.profesional_id, self.profesionales[self.empresa.slug].pk)
        audit = RegistroAuditoria.objects.get(modelo="paciente", objeto_id=str(self.sin_sexo.pk), accion="modificar", metodo_http="POST")
        self.assertEqual(audit.cambios["sexo"], {"anterior": "no_indicado", "nuevo": "femenino"})

    def test_classification_is_additive_and_repeated_edit_same_grade_is_idempotent(self):
        historia = self.historias[(self.empresa.slug, "capilar")]
        for grado in ("I", "IV", "IV"):
            self.assertEqual(self.client.post(self.url(historia=historia), self.datos(alopecia_grado=grado)).status_code, 302)
        self.assertEqual(list(ClasificacionAlopecia.objects.filter(historia=historia).order_by("pk").values_list("grado", flat=True)), ["I", "IV"])

    def test_finalized_nursing_form_disabled_no_submit_and_post_cannot_mutate(self):
        historia = self.historias[(self.empresa.slug, "enfermeria")]
        HistoriaClinicaEspecialidad.objects.filter(pk=historia.pk).update(estado="finalizada")
        self.asignar_rol(puede_editar_historia_clinica=True)
        before = self.conteos()
        response = self.client.get(self.url("enfermeria", historia))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["bloqueada"])
        self.assertTrue(all(field.disabled for field in response.context["form"].fields.values()))
        self.assertNotContains(response, 'type="submit"')
        response = self.client.post(self.url("enfermeria", historia), self.datos("enfermeria", observaciones="Cambio bloqueado", estado="borrador"))
        self.assertEqual(response.status_code, 302)
        historia.refresh_from_db()
        self.assertEqual(historia.observaciones, "Nota anterior hospital_mia enfermeria")
        self.assertEqual(historia.estado, "finalizada")
        self.assertEqual(self.conteos(), before)

    def test_date_invalid_and_choice_invalid_keep_exact_form_data_no_writes(self):
        before = self.conteos()
        for data in (self.datos("terapias", fecha_atencion="fecha-inválida"), self.datos("terapias", estado="forged")):
            response = self.client.post(self.url("terapias"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertEqual(response.context["form"]["observaciones"].value(), data["observaciones"])
            self.assertEqual(response.context["form"]["fecha_atencion"].value(), data["fecha_atencion"])
        self.assertEqual(self.conteos(), before)

    def test_writer_without_history_read_does_not_load_preconsultations_prior_notes_or_classifications(self):
        preconsulta = self.preconsulta()
        old = ClasificacionAlopecia.objects.create(empresa=self.empresa, paciente=self.paciente, historia=self.historias[(self.empresa.slug, "capilar")], escala="hamilton_norwood", grado="IV", fecha=timezone.now())
        self.asignar_rol(puede_crear_historia_clinica=True)
        before = self.conteos()
        response = self.client.get(self.url())
        for key in ("ultima_preconsulta", "resumen_preconsulta", "preconsultas_tipo", "historias_previas", "clasificaciones_alopecia"):
            self.assertFalse(response.context.get(key))
        self.assertNotContains(response, preconsulta.motivo_consulta)
        self.assertNotContains(response, self.historias[(self.empresa.slug, "capilar")].plan_tratamiento)
        self.assertFalse(response.context["can_view_history"])
        self.assertEqual(self.conteos(), before)
        old.refresh_from_db()

    def test_writer_and_editor_only_success_return_to_authorized_mobile_screen(self):
        for tipo, permiso in (("cirugia_plastica", "puede_crear_historia_clinica"), ("enfermeria", "puede_escribir_enfermeria"), ("terapias", "puede_escribir_terapias")):
            self.asignar_rol(**{permiso: True})
            response = self.client.post(self.url(tipo), self.datos(tipo))
            self.assertEqual(response.status_code, 302)
            self.assertIn("app=1", response.url)
            destination = self.client.get(response.url)
            self.assertEqual(destination.status_code, 200)
            self.assertTemplateUsed(destination, "clinica/mobile_history_form.html")
            self.assertFalse(destination.context["app_home_disponible"])
            self.assertNotContains(destination, 'rel="manifest"')
        self.asignar_rol(puede_editar_historia_clinica=True)
        historia = self.historias[(self.empresa.slug, "medicina_estetica")]
        response = self.client.post(self.url("medicina_estetica", historia), self.datos("medicina_estetica"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response.url).status_code, 200)
        self.assertIn("app=1", response.url)

    def test_reader_loads_only_same_patient_same_type_preconsultations(self):
        propia = self.preconsulta()
        other_type = self.preconsulta(tipo="medicina_estetica")
        foreign = self.preconsulta(paciente=self.pacientes["medical_spa"])
        response = self.client.get(self.url())
        self.assertEqual(response.context["ultima_preconsulta"].pk, propia.pk)
        self.assertEqual([item.pk for item in response.context["preconsultas_tipo"]], [propia.pk])
        self.assertNotContains(response, other_type.motivo_consulta)
        self.assertNotContains(response, foreign.motivo_consulta)

    def test_posted_company_patient_type_actors_and_creation_dates_are_not_authoritative(self):
        data = self.datos("terapias")
        data.update({"empresa": self.empresas["medical_spa"].pk, "paciente": self.pacientes["medical_spa"].pk,
                     "tipo": "enfermeria", "creado_por": 999999, "actualizado_por": 999999, "fecha_creacion": "2001-01-01"})
        response = self.client.post(self.url("terapias"), data)
        self.assertEqual(response.status_code, 302)
        historia = HistoriaClinicaEspecialidad.objects.filter(empresa=self.empresa, paciente=self.paciente, tipo="terapias").latest("pk")
        self.assertEqual((historia.empresa_id, historia.paciente_id, historia.tipo, historia.creado_por_id, historia.actualizado_por_id),
                         (self.empresa.pk, self.paciente.pk, "terapias", self.usuario.pk, self.usuario.pk))
        self.assertEqual(timezone.localtime(historia.fecha_creacion).date(), timezone.localdate())

    def test_original_web_create_edit_and_finalized_nursing_redirects_unchanged(self):
        data = self.datos("terapias")
        del data["app"]
        response = self.client.post(self.url("terapias", mobile=False), data)
        expected = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        historia = HistoriaClinicaEspecialidad.objects.filter(paciente=self.paciente, tipo="terapias").latest("pk")
        response = self.client.post(self.url("terapias", historia, mobile=False), {**data, "observaciones": "Edición web conservada"})
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        historia.refresh_from_db()
        self.assertEqual(historia.observaciones, "Edición web conservada")

    def test_patient_selector_requires_list_and_exact_read_or_type_write_permission(self):
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        self.asignar_rol(puede_ver_pacientes=True, puede_ver_historia_clinica=True)
        response = self.client.get(picker, {"app": "1", "destino": "historias"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mobile_patient_history_mode"], "historias")
        self.assertFalse(response.context["can_view_patient"])
        self.assertContains(response, reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])+"?app=1")
        self.asignar_rol(puede_ver_pacientes=True, puede_escribir_enfermeria=True)
        response = self.client.get(picker, {"app": "1", "destino": "crear_historia"})
        self.assertEqual(response.context["mobile_patient_history_create_type"]["codigo"], "enfermeria")
        self.assertEqual([item["codigo"] for item in response.context["mobile_history_create_types"]], ["enfermeria"])
        self.assertContains(response, self.url("enfermeria"))
        for params in ({"destino": "historias"}, {"destino": "crear_historia", "tipo": "capilar"},
                       {"destino": "crear_historia", "tipo": "camara_hiperbarica"}, {"destino": "crear_historia", "tipo": "forged"}):
            response = self.client.get(picker, {"app": "1", **params})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context.get("mobile_patient_history_mode"))
            self.assertNotContains(response, f'href="{self.url()}"')
        self.asignar_rol(puede_escribir_enfermeria=True)
        self.assertIn(self.client.get(picker, {"app": "1", "destino": "crear_historia"}).status_code, (302, 403))
        self.assertEqual(self.client.get(self.url("enfermeria")).status_code, 200)

    def test_selector_search_pagination_and_type_preserve_mode_and_tenant(self):
        self.asignar_rol(puede_ver_pacientes=True, puede_crear_historia_clinica=True)
        Paciente.objects.bulk_create([
            Paciente(empresa=self.empresa, nombre=f"Selector historia {index:02d}", expediente_codigo=f"MH-SEL-{index:02d}", sexo="masculino")
            for index in range(27)
        ])
        before = self.conteos()
        picker = reverse("clinica_pacientes", args=[self.empresa.slug])
        for page in ("1", "2"):
            response = self.client.get(picker, {"app": "1", "destino": "crear_historia", "tipo": "cirugia_plastica", "q": "Selector historia", "page": page})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["mobile_patient_history_mode"], "crear_historia")
            self.assertEqual(response.context["mobile_patient_history_create_type"]["codigo"], "cirugia_plastica")
            self.assertEqual(response.context["pacientes"].paginator.count, 27)
            self.assertContains(response, 'name="destino" value="crear_historia"')
            self.assertContains(response, 'name="tipo" value="cirugia_plastica"')
            self.assertContains(response, "destino=crear_historia&amp;tipo=cirugia_plastica")
            self.assertContains(response, "q=Selector%20historia")
            self.assertNotContains(response, self.pacientes["medical_spa"].nombre)
            for paciente in response.context["pacientes"]:
                self.assertContains(response, self.url("cirugia_plastica", paciente=paciente))
        self.assertEqual(self.conteos(), before)

    def test_hub_history_entries_follow_read_write_and_missing_patient_list_is_pending(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        metadata = reverse("agenda_mobile_modules", args=[self.empresa.slug])
        for permisos, keys, state in (
            ({"puede_ver_historia_clinica": True, "puede_ver_pacientes": True}, {"expediente"}, "disponible"),
            ({"puede_escribir_terapias": True, "puede_ver_pacientes": True}, {"registrar_historia"}, "disponible"),
            ({"puede_escribir_enfermeria": True}, {"registrar_historia"}, "pendiente"),
            ({"puede_ver_historia_clinica": True}, {"expediente"}, "pendiente"),
            ({"puede_ver_pacientes": True}, set(), None),
        ):
            with self.subTest(permisos=permisos):
                self.asignar_rol(**permisos)
                response = self.client.get(hub)
                self.assertEqual(response.status_code, 200)
                cards = {m["key"]: m for m in response.context["modulos_app"] if m["key"] in ("expediente", "registrar_historia")}
                self.assertEqual(set(cards), keys)
                for key, card in cards.items():
                    self.assertEqual(card["estado"], state)
                    self.assertEqual(bool(card["url"]), state != "pendiente")
                    if card["url"]:
                        self.assertIn("app=1&destino="+("historias" if key == "expediente" else "crear_historia"), card["url"])
                        self.assertEqual(self.client.get(card["url"]).status_code, 200)
                json = self.client.get(metadata)
                self.assertEqual(json.status_code, 200)
                self.assertEqual({m["key"] for m in json.json()["modulos"] if m["key"] in ("expediente", "registrar_historia")}, keys)

    def test_read_history_note_links_only_supported_unlocked_editor_and_keep_audit_identity(self):
        locked = self.historias[(self.empresa.slug, "enfermeria")]
        HistoriaClinicaEspecialidad.objects.filter(pk=locked.pk).update(estado="finalizada")
        unsupported = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="camara_hiperbarica", observaciones="Nota antigua no adaptada")
        route = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
        for editar in (False, True):
            self.asignar_rol(puede_ver_historia_clinica=True, puede_editar_historia_clinica=editar)
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            notes = {nota["id"]: nota for nota in response.context["mobile_history_notes"]}
            self.assertFalse(notes[locked.pk].get("edit_url"))
            self.assertFalse(notes[unsupported.pk].get("edit_url"))
            for tipo in ("capilar", "cirugia_plastica", "medicina_estetica", "terapias"):
                historia = self.historias[(self.empresa.slug, tipo)]
                self.assertEqual(bool(notes[historia.pk].get("edit_url")), editar)
                if editar:
                    self.assertEqual(notes[historia.pk]["edit_url"], self.url(tipo, historia))
                    self.assertEqual(self.client.get(notes[historia.pk]["edit_url"]).status_code, 200)

    def test_writer_without_read_does_not_select_prior_history_preconsulta_or_classification(self):
        self.preconsulta()
        ClasificacionAlopecia.objects.create(
            empresa=self.empresa, paciente=self.paciente, historia=self.historias[(self.empresa.slug, "capilar")],
            escala="hamilton_norwood", grado="IV", fecha=timezone.now(),
        )
        self.asignar_rol(puede_crear_historia_clinica=True)
        before = self.conteos()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        selects = [query["sql"] for query in captured if query["sql"].lstrip().upper().startswith("SELECT")]
        for model in (HistoriaClinicaEspecialidad, PreconsultaClinica, ClasificacionAlopecia):
            self.assertFalse(any(f'"{model._meta.db_table}"' in sql for sql in selects), model._meta.label)
        self.assertEqual(self.conteos(), before)

    def test_editor_without_read_selects_only_editable_record_and_its_bound_classification(self):
        self.preconsulta()
        historia = self.historias[(self.empresa.slug, "capilar")]
        ClasificacionAlopecia.objects.create(empresa=self.empresa, paciente=self.paciente, historia=historia, escala="hamilton_norwood", grado="I", fecha=timezone.now())
        prior = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="capilar", plan_tratamiento="Plan previo que no se puede leer")
        ClasificacionAlopecia.objects.create(empresa=self.empresa, paciente=self.paciente, historia=prior, escala="hamilton_norwood", grado="VII", fecha=timezone.now())
        self.asignar_rol(puede_editar_historia_clinica=True)
        before = self.conteos()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url(historia=historia))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["alopecia_grado"].value(), "I")
        selects = [query["sql"] for query in captured if query["sql"].lstrip().upper().startswith("SELECT")]
        self.assertFalse(any(f'"{PreconsultaClinica._meta.db_table}"' in sql for sql in selects))
        history_selects = [sql for sql in selects if f'"{HistoriaClinicaEspecialidad._meta.db_table}"' in sql]
        classification_selects = [sql for sql in selects if f'"{ClasificacionAlopecia._meta.db_table}"' in sql]
        self.assertTrue(history_selects)
        self.assertTrue(classification_selects)
        for sql in history_selects:
            self.assertIn(f'"{HistoriaClinicaEspecialidad._meta.db_table}"."id" = {historia.pk}', sql)
        for sql in classification_selects:
            self.assertIn(f'"{ClasificacionAlopecia._meta.db_table}"."historia_id" = {historia.pk}', sql)
        self.assertNotContains(response, prior.plan_tratamiento)
        self.assertEqual(self.conteos(), before)
