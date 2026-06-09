from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from .models import Paciente


class ClinicaPacienteTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia")
        modulo, _ = Modulo.objects.get_or_create(nombre="Clinica Medica", codigo="clinica_medica")
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        rol = RolSistema.objects.create(
            nombre="Clinica Admin",
            codigo="clinica-admin-test",
            activo=True,
            puede_clinica=True,
            puede_pacientes=True,
            puede_expediente_clinico=True,
            puede_tratamientos_clinicos=True,
            puede_configuracion_clinica=True,
        )
        user = get_user_model().objects.create_user(
            username="clinica",
            password="pass",
            empresa=self.empresa,
            rol_sistema=rol,
        )
        self.client.force_login(user)

    def test_crear_paciente_alergico_y_mostrar_alerta_en_lista(self):
        response = self.client.get(reverse("clinica_crear_paciente", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tomar foto")
        self.assertContains(response, "O+")

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            {
                "expediente_codigo": "HM-0001",
                "tipo_id": "cc",
                "identidad": "0801199912345",
                "primer_nombre": "Ana",
                "primer_apellido": "Mejia",
                "rh": "O+",
                "sexo": "femenino",
                "genero": "femenino",
                "estado_civil": "soltero",
                "prefijo_telefono": "Honduras (+504)",
                "zona_residencial": "urbana",
                "pais": "Honduras",
                "acompanante_relacion": "no_indicada",
                "responsable_relacion": "no_indicada",
                "escolaridad": "no_indicada",
                "pertenencia_etnica": "no_indicada",
                "nacionalidad": "Honduras",
                "es_alergico": "on",
                "alergias": "Penicilina",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        paciente = Paciente.objects.get(empresa=self.empresa, expediente_codigo="HM-0001")
        self.assertTrue(paciente.es_alergico)
        self.assertEqual(paciente.alergias, "Penicilina")

        response = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alergico")
        self.assertContains(response, "Ver")

        response = self.client.get(reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historial Clinico")
        self.assertContains(response, "Plan de tratamiento")
        self.assertContains(response, "Evolucion")
        self.assertContains(response, "Citas")
        self.assertContains(response, "Anexos")
        self.assertContains(response, "Consentimientos impresos")

    def test_no_permite_identidad_con_guiones_o_espacios(self):
        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            {
                "expediente_codigo": "HM-0002",
                "tipo_id": "cc",
                "identidad": "0801-1994-13996",
                "primer_nombre": "Luis",
                "primer_apellido": "Lopez",
                "sexo": "masculino",
                "genero": "masculino",
                "estado_civil": "soltero",
                "prefijo_telefono": "Honduras (+504)",
                "zona_residencial": "urbana",
                "pais": "Honduras",
                "acompanante_relacion": "no_indicada",
                "responsable_relacion": "no_indicada",
                "escolaridad": "no_indicada",
                "pertenencia_etnica": "no_indicada",
                "nacionalidad": "Honduras",
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solo debe contener numeros")
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, expediente_codigo="HM-0002").exists())
