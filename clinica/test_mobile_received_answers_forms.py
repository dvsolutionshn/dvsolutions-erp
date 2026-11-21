from datetime import timedelta
from urllib.parse import urlencode

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from .models import AntecedenteAdicionalPaciente, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica


class MobileReceivedAnswersFormsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.doctora = Usuario.objects.create_user(username="answers-form-doctor", es_administrador_empresa=True)
        cls.casos = []
        for numero, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"RESP-MOVIL-{numero}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo)
            cls.doctora.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(empresa=empresa, nombre=f"Paciente {slug}", expediente_codigo=f"RM-{numero}")
            for version in ("anterior", "actual"):
                PreconsultaClinica.objects.create(
                    empresa=empresa, paciente=paciente, estado="completada", tipo="general",
                    token_hash=f"respuestas-mobile-{slug}-{version}",
                    fecha_expiracion=timezone.now() + timedelta(days=7),
                    fecha_completada=timezone.now() - timedelta(days=version == "anterior"),
                    funciones_detalle=f"Funciones paciente {slug} {version}",
                    antecedentes_personales_detalle=f"Antecedentes paciente {slug} {version}",
                    datos_generales={"identidad": f"DOCUMENTO-PRIVADO-{slug}", "formulario_general": {
                        "examen_peso": f"Medición paciente {slug} {version}",
                    }},
                )
            PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, estado="pendiente", tipo="general",
                token_hash=f"respuestas-mobile-{slug}-pendiente", fecha_expiracion=timezone.now() + timedelta(days=7),
                funciones_detalle="RESPUESTA NO ENVIADA", antecedentes_personales_detalle="RESPUESTA NO ENVIADA",
            )
            cls.casos.append((empresa, paciente))

    def setUp(self):
        self.client.force_login(self.doctora)

    def url(self, empresa, paciente, formulario):
        return reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk]) + "?" + urlencode({
            "app": "1", "formulario": formulario,
        })

    def conteos(self):
        return tuple(modelo.objects.count() for modelo in (
            PreconsultaClinica, HistoriaClinicaEspecialidad, AntecedenteAdicionalPaciente, Paciente,
        ))

    def test_respuestas_actuales_y_anteriores_en_formularios_reales_de_cuatro_clinicas(self):
        antes = self.conteos()
        for empresa, paciente in self.casos:
            for formulario, textos in (
                ("antecedentes", ("Antecedentes",)),
                ("funciones_organicas", ("Funciones",)),
                ("examen_fisico", ("Medición",)),
                ("evaluacion_integral", ("Funciones", "Medición")),
            ):
                with self.subTest(empresa=empresa.slug, formulario=formulario):
                    respuesta = self.client.get(self.url(empresa, paciente, formulario))
                    self.assertEqual(respuesta.status_code, 200)
                    self.assertContains(respuesta, "Ver todas las respuestas del paciente")
                    contenido = respuesta.content.decode()
                    for texto in textos:
                        actual = f"{texto} paciente {empresa.slug} actual"
                        anterior = f"{texto} paciente {empresa.slug} anterior"
                        self.assertIn(actual, contenido)
                        self.assertIn(anterior, contenido)
                        self.assertLess(contenido.index(actual), contenido.index(anterior))
                    self.assertNotContains(respuesta, "RESPUESTA NO ENVIADA")
                    for otra_empresa, _ in self.casos:
                        if otra_empresa != empresa:
                            self.assertNotContains(respuesta, f"paciente {otra_empresa.slug}")
                    form = respuesta.context["form"]
                    self.assertFalse(form.is_bound)
                    campo = "contenido" if formulario == "antecedentes" else (
                        "examen_peso" if formulario == "examen_fisico" else "funciones_generales"
                    )
                    self.assertIn(form[campo].value(), (None, ""))
        self.assertEqual(self.conteos(), antes)

    def test_error_de_formulario_profesional_conserva_su_texto_y_respuestas_recibidas(self):
        empresa, paciente = self.casos[0]
        for formulario, datos, campo, valor, respuesta in (
            ("antecedentes", {
                "accion": "guardar_antecedente_adicional", "antecedente_adicional-contenido": "Texto profesional\x00conservado",
            }, "contenido", "Texto profesional\x00conservado", "Antecedentes"),
            ("funciones_organicas", {
                "accion": "guardar_funciones_organicas", "funciones-funciones_generales": "Texto profesional conservado",
                "funciones-estado": "estado-invalido",
            }, "funciones_generales", "Texto profesional conservado", "Funciones"),
        ):
            with self.subTest(formulario=formulario):
                antes = self.conteos()
                result = self.client.post(self.url(empresa, paciente, formulario), {
                    "app": "1", "formulario": formulario, **datos,
                })
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.context["form"].errors)
                self.assertEqual(result.context["form"][campo].value(), valor)
                self.assertContains(result, f"{respuesta} paciente {empresa.slug} actual")
                self.assertEqual(self.conteos(), antes)

    def test_especialidad_independiente_muestra_historia_general_y_su_area_en_web_y_app(self):
        empresa, paciente = self.casos[0]
        for tipo, texto in (
            ("medicina_estetica", "Respuesta completa de Medicina Estética"),
            ("capilar", "Respuesta reservada de otra área"),
        ):
            PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, estado="completada", tipo=tipo,
                token_hash=f"respuestas-standalone-{tipo}", fecha_expiracion=timezone.now() + timedelta(days=7),
                fecha_completada=timezone.now(), funciones_detalle=texto,
            )
        antes = self.conteos()
        url = reverse("clinica_crear_historia_especialidad", args=[empresa.slug, paciente.pk, "medicina_estetica"])
        for query in ({}, {"app": "1"}):
            with self.subTest(app=bool(query)):
                result = self.client.get(url, query)
                self.assertEqual(result.status_code, 200)
                self.assertContains(result, "Respuesta completa de Medicina Estética")
                self.assertContains(result, f"Funciones paciente {empresa.slug} actual")
                self.assertContains(result, f"Antecedentes paciente {empresa.slug} anterior")
                self.assertNotContains(result, "Respuesta reservada de otra área")
                self.assertNotContains(result, "RESPUESTA NO ENVIADA")
        self.assertEqual(self.conteos(), antes)

    def test_permisos_de_seccion_y_datos_generales_se_respetan_en_formularios(self):
        empresa, paciente = self.casos[0]
        rol = RolSistema.objects.create(
            codigo="respuestas-mobile-limitado", nombre="Lectura de antecedentes",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            puede_ver_historia_clinica=True, puede_editar_historia_clinica=True,
            puede_ver_antecedentes_clinicos=True,
        )
        usuario = Usuario.objects.create_user(username="answers-form-scoped", empresa=empresa, rol_sistema=rol)
        self.client.force_login(usuario)
        result = self.client.get(self.url(empresa, paciente, "antecedentes"))
        self.assertContains(result, f"Antecedentes paciente {empresa.slug} actual")
        self.assertNotContains(result, f"DOCUMENTO-PRIVADO-{empresa.slug}")
        for formulario in ("funciones_organicas", "examen_fisico", "evaluacion_integral"):
            result = self.client.get(self.url(empresa, paciente, formulario))
            self.assertEqual(result.status_code, 200)
            self.assertNotContains(result, f"Funciones paciente {empresa.slug}")
            self.assertNotContains(result, f"Medición paciente {empresa.slug}")
            self.assertNotContains(result, f"DOCUMENTO-PRIVADO-{empresa.slug}")

        rol.puede_ver_historia_clinica = False
        rol.puede_editar_historia_clinica = False
        rol.puede_ver_antecedentes_clinicos = False
        rol.save(update_fields=["puede_ver_historia_clinica", "puede_editar_historia_clinica", "puede_ver_antecedentes_clinicos"])
        for formulario in ("antecedentes", "funciones_organicas", "examen_fisico", "evaluacion_integral"):
            result = self.client.get(self.url(empresa, paciente, formulario))
            self.assertIn(result.status_code, (302, 403))
            self.assertNotIn(f"paciente {empresa.slug} actual", result.content.decode())
