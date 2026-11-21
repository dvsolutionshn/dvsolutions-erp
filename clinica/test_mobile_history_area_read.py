"""Un permiso de lectura de área abre únicamente sus notas en la app."""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from .mobile_preconsultas import autorizar_preconsulta_app
from .models import HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica


class MobileHistoryAreaReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.casos = []
        for numero, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"LECTURA-AREA-{numero}", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente área {slug}", expediente_codigo=f"AREA-{numero}",
                telefono=f"CONTACTO-PRIVADO-{slug}", correo=f"privado-{slug}@example.invalid", identidad=f"DOC-PRIVADO-{slug}",
            )
            notas = {}
            for tipo in ("medicina_estetica", "capilar"):
                notas[tipo] = HistoriaClinicaEspecialidad.objects.create(
                    empresa=empresa, paciente=paciente, tipo=tipo, estado="borrador",
                    plan_tratamiento=f"NOTA-{slug}-{tipo}",
                )
            preconsulta = PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, tipo="medicina_estetica", estado="completada",
                token_hash=f"area-read-{slug}", fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
                fecha_completada=timezone.now(), motivo_consulta=f"MOTIVO-AREA-{slug}",
                datos_generales={"telefono": "CONTACTO-FORMULARIO-PRIVADO", "formulario_general": {
                    "diagnostico_medico_detalle": "RESPUESTA-COMPLETA-PRIVADA",
                }},
            )
            for v3 in (False, True):
                rol = RolSistema.objects.create(
                    nombre=f"Sólo leer medicina estética {slug} {v3}", codigo=f"area-read-{numero}-{v3}",
                    es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
                    usa_permisos_acciones_v3=v3, puede_ver_medicina_estetica=True,
                )
                usuario = Usuario.objects.create_user(username=f"area-reader-{numero}-{v3}", empresa=empresa, rol_sistema=rol)
                cls.casos.append((empresa, paciente, usuario, notas, preconsulta))

    def url(self, empresa, paciente, **parametros):
        return reverse("clinica_historias_especialidad", args=[empresa.slug, paciente.pk]) + "?" + urlencode({"app": "1", **parametros})

    def test_lectura_exacta_de_area_en_cuatro_clinicas_v2_y_v3_sin_edicion_ni_contactos(self):
        for empresa, paciente, usuario, notas, preconsulta in self.casos:
            with self.subTest(empresa=empresa.slug, v3=usuario.rol_sistema.usa_permisos_acciones_v3):
                self.client.force_login(usuario)
                respuesta = self.client.get(self.url(empresa, paciente, tipo="medicina_estetica"))
                self.assertEqual(respuesta.status_code, 200)
                self.assertTemplateUsed(respuesta, "clinica/mobile_patient_histories.html")
                self.assertContains(respuesta, notas["medicina_estetica"].plan_tratamiento)
                self.assertNotContains(respuesta, notas["capilar"].plan_tratamiento)
                self.assertEqual([nota["id"] for nota in respuesta.context["mobile_history_notes"]], [notas["medicina_estetica"].pk])
                self.assertFalse(respuesta.context["mobile_history_create_types"])
                self.assertFalse(respuesta.context["can_edit_history"])
                self.assertFalse(respuesta.context["can_view_history"])
                for valor in (paciente.telefono, paciente.correo, paciente.identidad, paciente.expediente_codigo, "CONTACTO-FORMULARIO-PRIVADO", "RESPUESTA-COMPLETA-PRIVADA"):
                    self.assertNotContains(respuesta, valor)
                self.assertNotContains(respuesta, "Ver todas las áreas")
                items = respuesta.context["mobile_preconsulta_items"]
                self.assertEqual([item["id"] for item in items], [preconsulta.pk])
                self.assertEqual(items[0]["url"], "")
                self.assertEqual(self.client.head(self.url(empresa, paciente, tipo="medicina_estetica")).status_code, 200)
                editar = reverse("clinica_editar_historia_especialidad", args=[empresa.slug, paciente.pk, notas["medicina_estetica"].pk]) + "?app=1"
                crear = reverse("clinica_crear_historia_especialidad", args=[empresa.slug, paciente.pk, "medicina_estetica"]) + "?app=1"
                self.assertIn(self.client.get(editar).status_code, (302, 403))
                self.assertIn(self.client.get(crear).status_code, (302, 403))
                self.assertNotIn(editar.replace("&", "&amp;"), respuesta.content.decode())

    def test_sin_tipo_otras_areas_parametros_invalidos_y_post_no_abren_acceso(self):
        empresa, paciente, usuario, _notas, _preconsulta = self.casos[0]
        self.client.force_login(usuario)
        base = self.url(empresa, paciente)
        for url in (
            base, self.url(empresa, paciente, tipo="capilar"), self.url(empresa, paciente, tipo="no_existe"),
            self.url(empresa, paciente, tipo=""), base + "&tipo=medicina_estetica&tipo=medicina_estetica",
            base + "&tipo=medicina_estetica&app=1", base + "&tipo=medicina_estetica&paciente_id=9",
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        antes = HistoriaClinicaEspecialidad.objects.values().get(pk=_notas["medicina_estetica"].pk)
        self.assertEqual(self.client.post(self.url(empresa, paciente, tipo="medicina_estetica"), {"app": "1"}).status_code, 403)
        self.assertEqual(HistoriaClinicaEspecialidad.objects.values().get(pk=_notas["medicina_estetica"].pk), antes)

    def test_permiso_de_area_no_habilita_detalle_completo_de_preconsulta(self):
        for empresa, paciente, usuario, _notas, preconsulta in self.casos:
            self.client.force_login(usuario)
            detalle = reverse("clinica_preconsulta_detalle", args=[empresa.slug, paciente.pk, preconsulta.pk]) + "?app=1"
            for metodo in (self.client.get, self.client.head):
                respuesta = metodo(detalle)
                self.assertEqual(respuesta.status_code, 403)
                self.assertNotIn("RESPUESTA-COMPLETA-PRIVADA", respuesta.content.decode())

    def test_permiso_y_paciente_pertenecen_a_la_empresa_de_la_ruta(self):
        empresa, paciente, usuario, _notas, _preconsulta = self.casos[0]
        otra, otro_paciente, _otro_usuario, otras_notas, _otra_preconsulta = self.casos[2]
        self.client.force_login(usuario)
        self.assertIn(self.client.get(self.url(otra, otro_paciente, tipo="medicina_estetica")).status_code, (302, 403))
        self.assertEqual(self.client.get(self.url(empresa, otro_paciente, tipo="medicina_estetica")).status_code, 404)
        usuario.empresas_acceso.add(otra)
        rol_otra = RolSistema.objects.create(
            nombre="Área distinta en otra empresa", codigo="area-read-otra-empresa", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True, puede_ver_capilar=True,
        )
        UsuarioEmpresaPermiso.objects.create(usuario=usuario, empresa=otra, rol_sistema=rol_otra)
        self.assertEqual(self.client.get(self.url(otra, otro_paciente, tipo="medicina_estetica")).status_code, 403)
        respuesta = self.client.get(self.url(otra, otro_paciente, tipo="capilar"))
        self.assertContains(respuesta, otras_notas["capilar"].plan_tratamiento)
        self.assertNotContains(respuesta, _notas["medicina_estetica"].plan_tratamiento)

    def test_guardas_de_licencia_modulo_empresa_y_cuerpo_se_aplican_sin_middleware(self):
        empresa, paciente, usuario, _notas, _preconsulta = self.casos[0]
        url = self.url(empresa, paciente, tipo="medicina_estetica")

        def autorizar(request=None):
            request = request or RequestFactory().get(url)
            request.user = Usuario.objects.get(pk=usuario.pk)
            return autorizar_preconsulta_app(request, Empresa.objects.get(pk=empresa.pk), paciente.pk)

        self.assertIsNone(autorizar())
        EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(PermissionDenied):
            autorizar()
        EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=True)
        Empresa.objects.filter(pk=empresa.pk).update(estado_licencia="suspendida")
        with self.assertRaises(PermissionDenied):
            autorizar()
        Empresa.objects.filter(pk=empresa.pk).update(estado_licencia="activa", activa=False)
        with self.assertRaises(PermissionDenied):
            autorizar()
        Empresa.objects.filter(pk=empresa.pk).update(activa=True)
        with self.assertRaises(PermissionDenied):
            autorizar(RequestFactory().generic("GET", url, data="contenido", content_type="text/plain"))
        otra = self.casos[2][0]
        Usuario.objects.filter(pk=usuario.pk).update(empresa=otra)
        with self.assertRaises(PermissionDenied):
            autorizar()

    def test_roles_v1_y_enfermeria_limitada_conservan_restricciones(self):
        empresa, paciente, usuario, _notas, _preconsulta = self.casos[0]
        self.client.force_login(usuario)
        rol = usuario.rol_sistema
        rol.usa_permisos_clinicos_v2 = False
        rol.save(update_fields=["usa_permisos_clinicos_v2"])
        respuesta = self.client.get(self.url(empresa, paciente, tipo="medicina_estetica"))
        self.assertIn(respuesta.status_code, (302, 403))
        self.assertNotIn(_notas["medicina_estetica"].plan_tratamiento, respuesta.content.decode())
        request = RequestFactory().get(self.url(empresa, paciente, tipo="medicina_estetica"))
        request.user = Usuario.objects.get(pk=usuario.pk)
        with self.assertRaises(PermissionDenied):
            autorizar_preconsulta_app(request, empresa, paciente.pk)
        rol.usa_permisos_clinicos_v2 = True
        rol.puede_ver_medicina_estetica = False
        rol.puede_ver_pacientes = True
        rol.puede_ver_enfermeria = True
        rol.save(update_fields=["usa_permisos_clinicos_v2", "puede_ver_medicina_estetica", "puede_ver_pacientes", "puede_ver_enfermeria"])
        self.assertIn(self.client.get(self.url(empresa, paciente, tipo="enfermeria")).status_code, (302, 403))
        from .views import historias_especialidad

        request = RequestFactory().get(self.url(empresa, paciente, tipo="enfermeria"))
        request.user = Usuario.objects.get(pk=usuario.pk)
        with self.assertRaises(PermissionDenied):
            historias_especialidad(request, empresa.slug, paciente.pk)
