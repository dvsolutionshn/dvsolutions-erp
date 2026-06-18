from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from facturacion.models import Cliente
from .models import HistoriaClinicaEspecialidad, Paciente


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
        self.user = get_user_model().objects.create_user(
            username="clinica",
            password="pass",
            empresa=self.empresa,
            rol_sistema=rol,
        )
        self.client.force_login(self.user)

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
        self.assertIsNotNone(paciente.cliente)
        self.assertTrue(Cliente.objects.filter(empresa=self.empresa, rtn="0801199912345").exists())

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

    def test_lista_pacientes_prioriza_cumpleaneros_del_mes(self):
        hoy = timezone.localdate()
        paciente_normal = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0100",
            primer_nombre="Carlos",
            primer_apellido="Zuniga",
            nombre="Carlos Zuniga",
            identidad="0801199000001",
            fecha_nacimiento=hoy.replace(month=1 if hoy.month != 1 else 2, day=10),
        )
        cumpleanero = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0101",
            primer_nombre="Beatriz",
            primer_apellido="Aguilar",
            nombre="Beatriz Aguilar",
            identidad="0801199000002",
            fecha_nacimiento=hoy.replace(day=1),
            correo="beatriz@example.com",
            whatsapp="99990000",
        )

        response = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cumpleanos del mes")
        self.assertContains(response, "Promo")
        nombres = list(response.context["pacientes"])
        self.assertEqual(nombres[0], cumpleanero)
        self.assertIn(paciente_normal, nombres)

    def test_sugerencias_pacientes_busca_por_documento(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0200",
            primer_nombre="Maria",
            primer_apellido="Reyes",
            nombre="Maria Reyes",
            identidad="0801199413996",
            whatsapp="99991111",
        )

        response = self.client.get(
            reverse("clinica_pacientes_sugerencias", args=[self.empresa.slug]),
            {"q": "08011994"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["id"], paciente.id)
        self.assertEqual(data["results"][0]["documento"], "0801199413996")

        response = self.client.get(
            reverse("clinica_pacientes_sugerencias", args=[self.empresa.slug]),
            {"q": "0"},
        )
        self.assertEqual(response.json()["results"], [])

    def test_historias_especialidad_permite_crear_y_editar_en_hospital_mia(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0300",
            primer_nombre="Andrea",
            primer_apellido="Lopez",
            nombre="Andrea Lopez",
            identidad="0801199500001",
        )

        selector = self.client.get(
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id])
        )
        self.assertEqual(selector.status_code, 200)
        for nombre in ["Capilar", "Cirugia plastica y reconstructiva", "Enfermeria", "Terapias", "Camara hiperbarica"]:
            self.assertContains(selector, nombre)

        crear_url = reverse(
            "clinica_crear_historia_especialidad",
            args=[self.empresa.slug, paciente.id, "capilar"],
        )
        response = self.client.post(
            crear_url,
            {
                "fecha_atencion": "2026-06-17T10:30",
                "motivo_consulta": "Caida de cabello",
                "antecedentes": "Sin antecedentes relevantes",
                "signos_vitales": "PA 120/80",
                "evaluacion_clinica": "Evaluacion capilar inicial",
                "diagnostico": "Alopecia en estudio",
                "procedimiento": "Tricoscopia",
                "plan_tratamiento": "Control en 30 dias",
                "indicaciones": "Aplicar tratamiento indicado",
                "observaciones": "Sin complicaciones",
                "estado": "borrador",
            },
        )
        self.assertRedirects(
            response,
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id]),
        )
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente)
        self.assertEqual(historia.tipo, "capilar")
        self.assertEqual(historia.creado_por, self.user)

        editar_url = reverse(
            "clinica_editar_historia_especialidad",
            args=[self.empresa.slug, paciente.id, historia.id],
        )
        response = self.client.post(
            editar_url,
            {
                "fecha_atencion": "2026-06-17T10:30",
                "motivo_consulta": "Caida de cabello actualizada",
                "antecedentes": historia.antecedentes,
                "signos_vitales": historia.signos_vitales,
                "evaluacion_clinica": historia.evaluacion_clinica,
                "diagnostico": historia.diagnostico,
                "procedimiento": historia.procedimiento,
                "plan_tratamiento": historia.plan_tratamiento,
                "indicaciones": historia.indicaciones,
                "observaciones": historia.observaciones,
                "estado": "finalizada",
            },
        )
        self.assertEqual(response.status_code, 302)
        historia.refresh_from_db()
        self.assertEqual(historia.estado, "finalizada")
        self.assertEqual(historia.motivo_consulta, "Caida de cabello actualizada")
        self.assertEqual(historia.actualizado_por, self.user)

    def test_historias_especialidad_no_estan_disponibles_para_otra_empresa(self):
        otra_empresa = Empresa.objects.create(
            nombre="Mia Medical Spa",
            slug="medical_spa",
            rtn="08011999000999",
        )
        modulo = Modulo.objects.get(codigo="clinica_medica")
        EmpresaModulo.objects.create(empresa=otra_empresa, modulo=modulo, activo=True)
        paciente = Paciente.objects.create(
            empresa=otra_empresa,
            expediente_codigo="MMS-0001",
            nombre="Paciente Spa",
            identidad="0801199500002",
        )
        otro_usuario = get_user_model().objects.create_user(
            username="clinica_spa",
            password="pass",
            empresa=otra_empresa,
            rol_sistema=self.user.rol_sistema,
        )
        self.client.force_login(otro_usuario)

        response = self.client.get(
            reverse("clinica_historias_especialidad", args=[otra_empresa.slug, paciente.id])
        )

        self.assertEqual(response.status_code, 404)
