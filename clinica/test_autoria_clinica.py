"""La autoría clínica se obtiene de la sesión, nunca del formulario enviado."""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, Usuario

from .autoria import asignar_autoria_clinica, profesional_del_usuario
from .forms import (
    CitaClinicaForm,
    EvaluacionClinicaIntegralForm,
    ExpedienteEventoForm,
    FUNCIONES_ORGANICAS_SISTEMAS,
    HistoriaClinicaEspecialidadForm,
    IncapacidadClinicaForm,
    RecetaMedicaForm,
    TratamientoPacienteForm,
    TricopigmentacionForm,
)
from .models import (
    DocumentoClinicoPaciente,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    Paciente,
    ProfesionalSalud,
    RecetaMedica,
    TratamientoPaciente,
)


class AutoriaClinicaTests(TestCase):
    EMPRESAS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ESPECIALIDADES = ("capilar", "cirugia_plastica", "medicina_estetica", "enfermeria", "terapias")

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas, cls.pacientes = {}, {}
        for indice, slug in enumerate(cls.EMPRESAS, 1):
            empresa = Empresa.objects.create(nombre=f"Autoría {slug}", slug=slug, tipo_solucion="clinica", rtn=f"AUT-{indice}")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo)
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente autoría {slug}", expediente_codigo=f"AUT-{indice}", sexo="femenino",
            )
        cls.empresa = cls.empresas[cls.EMPRESAS[0]]
        cls.autor = Usuario.objects.create_user(username="autoria-creador", empresa=cls.empresa, es_administrador_empresa=True)
        cls.editor = Usuario.objects.create_user(username="autoria-editor", empresa=cls.empresa, es_administrador_empresa=True)
        cls.sin_vinculo = Usuario.objects.create_user(
            username="autoria-sin-vinculo", first_name="Nombre", last_name="Coincidente", empresa=cls.empresa,
            es_administrador_empresa=True,
        )
        cls.profesionales, cls.editores = {}, {}
        for slug, empresa in cls.empresas.items():
            for usuario in (cls.autor, cls.editor, cls.sin_vinculo):
                usuario.empresas_acceso.add(empresa)
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, usuario=cls.autor, nombre=f"Autor explícito {slug}")
            cls.editores[slug] = ProfesionalSalud.objects.create(empresa=empresa, usuario=cls.editor, nombre=f"Editor explícito {slug}")
            ProfesionalSalud.objects.create(empresa=empresa, nombre="Nombre Coincidente")

    def setUp(self):
        self.client.force_login(self.autor)

    def url_historia(self, empresa, tipo="capilar", historia=None, app=False):
        paciente = self.pacientes[empresa.slug]
        args = [empresa.slug, paciente.pk, historia.pk if historia else tipo]
        nombre = "clinica_editar_historia_especialidad" if historia else "clinica_crear_historia_especialidad"
        return reverse(nombre, args=args) + ("?app=1" if app else "")

    def datos_historia(self, tipo="capilar"):
        datos = {
            "fecha_atencion": "2026-11-04T10:00", "estado": "borrador",
            "plan_tratamiento": "Plan clínico escrito", "observaciones": "Nota clínica escrita",
        }
        if tipo == "capilar":
            datos.update(alopecia_escala="ludwig", alopecia_grado="II", capilar_patron_clinico=["androgenetico"])
        return datos

    def falsificacion(self):
        return {
            "profesional": self.editores[self.EMPRESAS[-1]].pk,
            "profesional_id": self.editores[self.EMPRESAS[-1]].pk,
            "creado_por": self.editor.pk, "creada_por": self.editor.pk, "actualizado_por": self.editor.pk,
        }

    def assert_autoria(self, registro, empresa, usuario=None):
        usuario = usuario or self.autor
        self.assertEqual(registro.empresa_id, empresa.pk)
        self.assertEqual(registro.profesional_id, profesional_del_usuario(empresa, usuario).pk)
        creador = "creada_por_id" if isinstance(registro, RecetaMedica) else "creado_por_id"
        if hasattr(registro, creador):
            self.assertEqual(getattr(registro, creador), usuario.pk)
        if hasattr(registro, "actualizado_por_id"):
            self.assertEqual(registro.actualizado_por_id, usuario.pk)

    def test_los_siete_formularios_no_ofrecen_seleccionar_autor_en_cuatro_empresas(self):
        clases = (
            TratamientoPacienteForm, ExpedienteEventoForm, IncapacidadClinicaForm, RecetaMedicaForm,
            EvaluacionClinicaIntegralForm, TricopigmentacionForm, HistoriaClinicaEspecialidadForm,
        )
        for empresa in self.empresas.values():
            for clase in clases:
                with self.subTest(empresa=empresa.slug, formulario=clase.__name__):
                    form = clase(empresa=empresa, usuario=self.autor)
                    self.assertNotIn("profesional", form.fields)
                    self.assertNotIn("creado_por", form.fields)
                    self.assertNotIn("actualizado_por", form.fields)
            # El médico que atenderá una cita sigue siendo una asignación de agenda.
            self.assertIn("profesional", CitaClinicaForm(empresa=empresa, usuario=self.autor).fields)

    def test_profesional_solo_se_resuelve_por_vinculo_activo_de_la_empresa(self):
        for slug, empresa in self.empresas.items():
            self.assertEqual(profesional_del_usuario(empresa, self.autor), self.profesionales[slug])
            self.assertIsNone(profesional_del_usuario(empresa, self.sin_vinculo))
        self.assertIsNone(profesional_del_usuario(self.empresa, AnonymousUser()))
        ProfesionalSalud.objects.filter(pk=self.profesionales[self.empresa.slug].pk).update(activo=False)
        self.assertIsNone(profesional_del_usuario(self.empresa, self.autor))
        self.assertEqual(profesional_del_usuario(self.empresas["medical_spa"], self.autor), self.profesionales["medical_spa"])

    def test_guardado_no_admite_autor_anonimo_ni_otra_empresa(self):
        historia = HistoriaClinicaEspecialidad(empresa=self.empresa, paciente=self.pacientes[self.empresa.slug], tipo="capilar")
        with self.assertRaises(PermissionDenied):
            asignar_autoria_clinica(historia, self.empresa, AnonymousUser())
        with self.assertRaises(PermissionDenied):
            asignar_autoria_clinica(historia, self.empresas["medical_spa"], self.autor)
        ajeno = Usuario.objects.create_user(username="autoria-ajeno", empresa=self.empresas["medical_spa"])
        with self.assertRaises(PermissionDenied):
            asignar_autoria_clinica(historia, self.empresa, ajeno)

    def test_especialidades_web_ignoran_identidad_falsificada_en_cuatro_empresas(self):
        for empresa in self.empresas.values():
            for tipo in self.ESPECIALIDADES:
                with self.subTest(empresa=empresa.slug, tipo=tipo):
                    respuesta = self.client.post(self.url_historia(empresa, tipo), self.datos_historia(tipo) | self.falsificacion())
                    self.assertEqual(respuesta.status_code, 302)
                    historia = HistoriaClinicaEspecialidad.objects.get(empresa=empresa, paciente=self.pacientes[empresa.slug], tipo=tipo)
                    self.assert_autoria(historia, empresa)
                    self.assertTrue(RegistroAuditoria.objects.filter(modelo="historiaclinicaespecialidad", objeto_id=str(historia.pk), accion="crear", usuario=self.autor, empresa=empresa).exists())

    def test_edicion_conserva_creador_original_y_registra_editor_autenticado(self):
        self.client.force_login(self.editor)
        for empresa in self.empresas.values():
            historia = HistoriaClinicaEspecialidad.objects.create(
                empresa=empresa, paciente=self.pacientes[empresa.slug], tipo="medicina_estetica",
                profesional=self.profesionales[empresa.slug], creado_por=self.autor, actualizado_por=self.autor,
                plan_tratamiento="Texto anterior", datos_especialidad={"dato_historico_retirado": "conservar"},
            )
            datos = self.datos_historia("medicina_estetica") | self.falsificacion()
            datos.update(creado_por=self.editor.pk, actualizado_por=self.autor.pk, profesional=self.profesionales[empresa.slug].pk)
            respuesta = self.client.post(self.url_historia(empresa, historia=historia), datos)
            self.assertEqual(respuesta.status_code, 302)
            historia.refresh_from_db()
            self.assertEqual(historia.creado_por_id, self.autor.pk)
            self.assertEqual(historia.actualizado_por_id, self.editor.pk)
            self.assertEqual(historia.profesional_id, self.editores[empresa.slug].pk)
            self.assertEqual(historia.datos_especialidad["dato_historico_retirado"], "conservar")
            self.assertTrue(RegistroAuditoria.objects.filter(modelo="historiaclinicaespecialidad", objeto_id=str(historia.pk), accion="modificar", usuario=self.editor, empresa=empresa).exists())

    def test_usuario_sin_profesional_vinculado_se_guarda_sin_adivinar_otro(self):
        self.client.force_login(self.sin_vinculo)
        respuesta = self.client.post(self.url_historia(self.empresa), self.datos_historia() | self.falsificacion())
        self.assertEqual(respuesta.status_code, 302)
        historia = HistoriaClinicaEspecialidad.objects.get(empresa=self.empresa)
        self.assertIsNone(historia.profesional_id)
        self.assertEqual(historia.creado_por_id, self.sin_vinculo.pk)
        self.assertEqual(historia.actualizado_por_id, self.sin_vinculo.pk)

    def test_inline_evaluacion_y_tricopigmentacion_tambien_usan_sesion(self):
        for empresa in self.empresas.values():
            url = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, self.pacientes[empresa.slug].pk])
            for tipo, prefijo, accion in (
                ("capilar", "historia_capilar", None),
                ("evaluacion_integral", "evaluacion", "guardar_evaluacion_integral"),
                ("tricopigmentacion", "tricopigmentacion", "guardar_tricopigmentacion"),
            ):
                datos = {f"{prefijo}-fecha_atencion": "2026-11-04T11:00", f"{prefijo}-estado": "borrador"}
                if tipo == "capilar":
                    datos.update({f"{prefijo}-{campo}": valor for campo, valor in self.datos_historia().items()})
                    datos["tipo_historia"] = tipo
                else:
                    datos["accion"] = accion
                if tipo == "evaluacion_integral":
                    datos.update({f"{prefijo}-funcion_{codigo}": "normal" for codigo, _ in FUNCIONES_ORGANICAS_SISTEMAS})
                datos.update(self.falsificacion())
                datos[f"{prefijo}-profesional"] = self.editores[self.EMPRESAS[-1]].pk
                with self.subTest(empresa=empresa.slug, tipo=tipo):
                    respuesta = self.client.post(url, datos)
                    self.assertEqual(respuesta.status_code, 302)
                    historia = HistoriaClinicaEspecialidad.objects.get(empresa=empresa, tipo=tipo)
                    self.assert_autoria(historia, empresa)

    def test_formularios_moviles_capilar_guardan_autor_sin_selector(self):
        for empresa in self.empresas.values():
            url = self.url_historia(empresa, app=True)
            pagina = self.client.get(url)
            self.assertEqual(pagina.status_code, 200)
            self.assertNotIn("profesional", pagina.context["form"].fields)
            self.assertNotContains(pagina, '<select name="profesional"')
            self.assertContains(pagina, f"<span>Profesional: <strong>{self.autor.username}</strong></span>", html=True)
            respuesta = self.client.post(url, self.datos_historia() | {"app": "1"})
            self.assertEqual(respuesta.status_code, 302)
            historia = HistoriaClinicaEspecialidad.objects.get(empresa=empresa, tipo="capilar")
            self.assert_autoria(historia, empresa)

    def test_evento_receta_incapacidad_y_tratamiento_ignoran_profesional_enviado(self):
        for empresa in self.empresas.values():
            paciente = self.pacientes[empresa.slug]
            casos = (
                ("clinica_crear_evento_paciente", [empresa.slug, paciente.pk], ExpedienteEvento,
                 {"paciente": paciente.pk, "tipo": "nota", "fecha": "2026-11-04T11:00", "titulo": "Evento clínico", "descripcion": "Detalle del evento"}),
                ("clinica_crear_receta_paciente", [empresa.slug, paciente.pk], RecetaMedica,
                 {"fecha": "2026-11-04", "indicaciones": "Indicaciones médicas"}),
                ("clinica_subir_documento_categoria_paciente", [empresa.slug, paciente.pk, "incapacidad"], DocumentoClinicoPaciente,
                 {"titulo": "Certificado", "fecha_documento": "2026-11-04", "fecha_inicio": "2026-11-04", "fecha_fin": "2026-11-05", "descripcion": "Reposo indicado"}),
                ("clinica_crear_tratamiento", [empresa.slug], TratamientoPaciente,
                 {"paciente": paciente.pk, "nombre": "Tratamiento clínico", "fecha_inicio": "2026-11-04", "estado": "planificado", "monto_estimado": "0"}),
            )
            for ruta, args, modelo, datos in casos:
                with self.subTest(empresa=empresa.slug, modelo=modelo.__name__):
                    respuesta = self.client.post(reverse(ruta, args=args), datos | self.falsificacion())
                    self.assertEqual(respuesta.status_code, 302)
                    registro = modelo.objects.get(empresa=empresa, paciente=paciente)
                    self.assert_autoria(registro, empresa)
                    self.assertTrue(RegistroAuditoria.objects.filter(
                        modelo=modelo._meta.model_name, objeto_id=str(registro.pk), accion="crear",
                        usuario=self.autor, empresa=empresa,
                    ).exists())

    def test_no_se_puede_guardar_en_expediente_de_otra_empresa(self):
        paciente_ajeno = self.pacientes["medical_spa"]
        url = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, paciente_ajeno.pk, "capilar"])
        respuesta = self.client.post(url, self.datos_historia() | self.falsificacion())
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(HistoriaClinicaEspecialidad.objects.exists())
