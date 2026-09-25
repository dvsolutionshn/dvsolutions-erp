from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema

from .models import (
    AntecedenteAdicionalHistorial,
    AntecedenteAdicionalPaciente,
    Paciente,
    ProfesionalSalud,
)


class AntecedentesAdicionalesTests(TestCase):
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
        self.rol = RolSistema.objects.create(
            nombre="Clinica Admin Antecedentes",
            codigo="clinica-admin-antecedentes",
            activo=True,
            puede_clinica=True,
            puede_pacientes=True,
            puede_expediente_clinico=True,
        )
        self.usuario = get_user_model().objects.create_user(
            username="doctora-antecedentes",
            first_name="Candy",
            last_name="Luque",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            usuario=self.usuario,
            nombre="Dra. Candy Luque",
            activo=True,
        )
        self.paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-ANT-001",
            nombre="Paciente Antecedentes",
            identidad="0801199012345",
        )
        self.url = reverse(
            "clinica_historial_clinico_consolidado",
            args=[self.empresa.slug, self.paciente.id],
        )
        self.client.force_login(self.usuario)

    def _guardar(self, contenido):
        return self.client.post(
            f"{self.url}?seccion=antecedentes",
            {
                "accion": "guardar_antecedente_adicional",
                "antecedente_adicional-contenido": contenido,
            },
        )

    def test_muestra_un_solo_cuadro_editable_en_antecedentes(self):
        response = self.client.get(f"{self.url}?seccion=antecedentes")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Antecedentes adicionales")
        self.assertContains(response, 'name="antecedente_adicional-contenido"', count=1)
        self.assertContains(response, "Guardar cambios")
        self.assertNotContains(response, "Ver historial de cambios")

    def test_actualiza_el_mismo_texto_y_conserva_historial_completo(self):
        primera_version = "Antecedente inicial con detalle clínico."
        segunda_version = "Antecedente inicial actualizado con nueva observación."

        response = self._guardar(primera_version)
        self.assertEqual(response.status_code, 302)
        antecedente = AntecedenteAdicionalPaciente.objects.get(paciente=self.paciente)
        antecedente_id = antecedente.id
        primer_cambio = AntecedenteAdicionalHistorial.objects.get(antecedente=antecedente)
        self.assertEqual(primer_cambio.texto_anterior, "")
        self.assertEqual(primer_cambio.texto_nuevo, primera_version)
        self.assertEqual(primer_cambio.usuario, self.usuario)
        self.assertEqual(primer_cambio.profesional, self.profesional)
        self.assertEqual(primer_cambio.responsable_nombre, "Dra. Candy Luque")

        response = self._guardar(segunda_version)
        self.assertEqual(response.status_code, 302)
        antecedente.refresh_from_db()
        self.assertEqual(antecedente.id, antecedente_id)
        self.assertEqual(antecedente.contenido, segunda_version)
        self.assertEqual(AntecedenteAdicionalPaciente.objects.filter(paciente=self.paciente).count(), 1)
        self.assertEqual(AntecedenteAdicionalHistorial.objects.filter(antecedente=antecedente).count(), 2)
        ultimo_cambio = antecedente.historial.first()
        self.assertEqual(ultimo_cambio.texto_anterior, primera_version)
        self.assertEqual(ultimo_cambio.texto_nuevo, segunda_version)

        pagina = self.client.get(f"{self.url}?seccion=antecedentes")
        self.assertContains(pagina, segunda_version)
        self.assertContains(pagina, "Ver historial de cambios")
        self.assertContains(pagina, primera_version)
        self.assertContains(pagina, "Dra. Candy Luque")

    def test_guardar_sin_cambios_no_crea_otra_version(self):
        self._guardar("Texto sin cambios posteriores.")
        self._guardar("Texto sin cambios posteriores.")

        self.assertEqual(AntecedenteAdicionalPaciente.objects.filter(paciente=self.paciente).count(), 1)
        self.assertEqual(AntecedenteAdicionalHistorial.objects.filter(paciente=self.paciente).count(), 1)

    def test_usuario_de_solo_lectura_no_puede_modificar(self):
        rol_lectura = RolSistema.objects.create(
            nombre="Consulta historia antecedentes",
            codigo="consulta-historia-antecedentes",
            activo=True,
            puede_clinica=True,
            puede_pacientes=True,
            usa_permisos_clinicos_granulares=True,
            puede_ver_historia_clinica=True,
            puede_editar_historia_clinica=False,
        )
        lector = get_user_model().objects.create_user(
            username="lector-antecedentes",
            password="pass",
            empresa=self.empresa,
            rol_sistema=rol_lectura,
        )
        antecedente = AntecedenteAdicionalPaciente.objects.create(
            empresa=self.empresa,
            paciente=self.paciente,
            contenido="Contenido visible para lectura.",
            actualizado_por=self.usuario,
            profesional=self.profesional,
        )
        AntecedenteAdicionalHistorial.objects.create(
            antecedente=antecedente,
            empresa=self.empresa,
            paciente=self.paciente,
            texto_anterior="",
            texto_nuevo=antecedente.contenido,
            usuario=self.usuario,
            profesional=self.profesional,
        )
        self.client.force_login(lector)

        pagina = self.client.get(f"{self.url}?seccion=antecedentes")
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, "Contenido visible para lectura.")
        self.assertNotContains(pagina, "Guardar cambios")

        response = self.client.post(
            f"{self.url}?seccion=antecedentes",
            {
                "accion": "guardar_antecedente_adicional",
                "antecedente_adicional-contenido": "Intento no autorizado.",
            },
        )
        self.assertEqual(response.status_code, 403)
        antecedente.refresh_from_db()
        self.assertEqual(antecedente.contenido, "Contenido visible para lectura.")
