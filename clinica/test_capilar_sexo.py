from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema

from .forms import PacienteForm, PreconsultaClinicaPublicaForm
from .models import ClasificacionAlopecia, HistoriaClinicaEspecialidad, Paciente


class CapilarSexoBlindadoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital MIA",
            slug="hospital_mia",
            tipo_solucion="clinica",
        )
        modulo, _ = Modulo.objects.get_or_create(
            nombre="Clinica Medica",
            codigo="clinica_medica",
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa)
        rol = RolSistema.objects.create(
            nombre="Clinica Capilar Sexo",
            codigo="clinica-capilar-sexo",
            activo=True,
            puede_clinica=True,
            puede_pacientes=True,
            puede_expediente_clinico=True,
        )
        self.usuario = get_user_model().objects.create_user(
            username="clinica-capilar-sexo",
            password="pass",
            empresa=self.empresa,
            rol_sistema=rol,
        )
        self.client.force_login(self.usuario)

    def _url_capilar(self, paciente):
        return reverse(
            "clinica_crear_historia_especialidad",
            args=[self.empresa.slug, paciente.id, "capilar"],
        )

    def test_formularios_nuevos_solo_ofrecen_femenino_y_masculino(self):
        paciente_choices = list(PacienteForm(empresa=self.empresa).fields["sexo"].choices)
        preconsulta_choices = list(PreconsultaClinicaPublicaForm(empresa=self.empresa).fields["sexo"].choices)

        self.assertEqual(paciente_choices, [("femenino", "Femenino"), ("masculino", "Masculino")])
        self.assertEqual(
            preconsulta_choices,
            [("", "Seleccione una opcion"), ("femenino", "Femenino"), ("masculino", "Masculino")],
        )

    def test_femenino_normalizado_abre_ludwig_sin_respaldo_masculino(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-FEM",
            nombre="Paciente Femenina",
            identidad="0801198500101",
            sexo=" Femenino ",
        )

        response = self.client.get(self._url_capilar(paciente))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escala sugerida por sexo registrado: <strong>Ludwig</strong>", html=True)
        self.assertContains(response, 'data-initial-scale="ludwig"')
        self.assertContains(response, 'data-scale="hamilton_norwood" hidden')
        self.assertNotContains(response, 'data-initial-scale="hamilton_norwood"')

    def test_sexo_historico_debe_confirmarse_y_actualiza_el_expediente(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-PEND",
            nombre="Paciente Sexo Pendiente",
            identidad="0801198500102",
            sexo="no_indicado",
        )
        url = self._url_capilar(paciente)

        pagina = self.client.get(url)
        self.assertContains(pagina, "Especifique el sexo del paciente")
        self.assertContains(pagina, 'data-scale="hamilton_norwood" hidden')
        self.assertContains(pagina, 'data-scale="ludwig" hidden')

        response = self.client.post(
            url,
            {
                "fecha_atencion": "2026-10-03T10:30",
                "paciente_sexo_confirmado": "femenino",
                "alopecia_escala": "hamilton_norwood",
                "alopecia_grado": "II",
                "plan_tratamiento": "Control capilar.",
                "estado": "borrador",
            },
        )

        self.assertEqual(response.status_code, 302)
        paciente.refresh_from_db()
        self.assertEqual(paciente.sexo, "femenino")
        clasificacion = ClasificacionAlopecia.objects.get(paciente=paciente)
        self.assertEqual(clasificacion.escala, ClasificacionAlopecia.ESCALA_LUDWIG)
        self.assertEqual(clasificacion.grado, "II")

    def test_formulario_inline_tambien_impide_hamilton_en_paciente_femenina(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-INLINE",
            nombre="Paciente Femenina Inline",
            identidad="0801198500103",
            sexo="femenino",
        )
        url = reverse(
            "clinica_historial_clinico_consolidado",
            args=[self.empresa.slug, paciente.id],
        )

        response = self.client.post(
            url,
            {
                "tipo_historia": "capilar",
                "historia_capilar-fecha_atencion": "2026-10-03T11:00",
                "historia_capilar-alopecia_escala": "hamilton_norwood",
                "historia_capilar-alopecia_grado": "II",
                "historia_capilar-plan_tratamiento": "Control desde expediente.",
                "historia_capilar-estado": "borrador",
            },
        )

        self.assertEqual(response.status_code, 302)
        clasificacion = ClasificacionAlopecia.objects.get(paciente=paciente)
        self.assertEqual(clasificacion.escala, ClasificacionAlopecia.ESCALA_LUDWIG)

    def test_clasificacion_antigua_contraria_no_domina_al_editar(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-HIST",
            nombre="Paciente Femenina Histórica",
            identidad="0801198500106",
            sexo="femenino",
        )
        historia = HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="capilar",
            plan_tratamiento="Evaluación anterior.",
            creado_por=self.usuario,
            actualizado_por=self.usuario,
        )
        anterior = ClasificacionAlopecia.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            historia=historia,
            escala=ClasificacionAlopecia.ESCALA_HAMILTON_NORWOOD,
            grado="II",
            creado_por=self.usuario,
        )
        url = reverse(
            "clinica_editar_historia_especialidad",
            args=[self.empresa.slug, paciente.id, historia.id],
        )

        pagina = self.client.get(url)
        self.assertContains(pagina, 'data-initial-scale="ludwig"')
        self.assertContains(pagina, 'data-scale="hamilton_norwood" hidden')

        respuesta = self.client.post(
            url,
            {
                "fecha_atencion": "2026-10-03T11:30",
                "alopecia_escala": "hamilton_norwood",
                "alopecia_grado": "II",
                "plan_tratamiento": "Control corregido.",
                "estado": "borrador",
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        anterior.refresh_from_db()
        self.assertEqual(anterior.escala, ClasificacionAlopecia.ESCALA_HAMILTON_NORWOOD)
        self.assertTrue(
            ClasificacionAlopecia.objects.filter(
                paciente=paciente,
                escala=ClasificacionAlopecia.ESCALA_LUDWIG,
                grado="II",
            ).exists()
        )

    def test_datos_generales_muestran_el_sexo(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-DATOS",
            nombre="Paciente Datos Generales",
            identidad="0801198500104",
            sexo="femenino",
        )
        url = reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<span>Sexo</span><strong>Femenino</strong>", html=True)
        self.assertNotContains(response, "Debe modificar el sexo del paciente")

    def test_datos_generales_alertan_si_el_sexo_historico_no_esta_definido(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-CAP-ALERTA",
            nombre="Paciente Sexo Histórico",
            identidad="0801198500105",
            sexo="otro",
        )

        response = self.client.get(
            reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Debe modificar el sexo del paciente y seleccionar Femenino o Masculino.",
        )
        self.assertContains(response, "Confirme el sexo del paciente")
        self.assertContains(response, "Guardar sexo")

        guardar = self.client.post(
            reverse("clinica_confirmar_sexo_paciente", args=[self.empresa.slug, paciente.id]),
            {"sexo": "femenino"},
        )

        self.assertRedirects(
            guardar,
            reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id]),
        )
        paciente.refresh_from_db()
        self.assertEqual(paciente.sexo, "femenino")
        corregido = self.client.get(
            reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id])
        )
        self.assertNotContains(corregido, "Confirme el sexo del paciente")
