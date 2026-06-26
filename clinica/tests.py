from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from facturacion.models import Cliente
from .forms import PreconsultaClinicaPublicaForm
from .models import CitaClinica, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, ServicioClinico
from .tokens import hash_token_preconsulta


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

    def test_nueva_cita_clinica_usa_control_unificado_am_pm(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa, expediente_codigo="HM-CITA", nombre="Paciente Cita"
        )
        profesional = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Cita")
        servicio = ServicioClinico.objects.create(empresa=self.empresa, nombre="Consulta General")
        url = reverse("clinica_crear_cita", args=[self.empresa.slug])

        response = self.client.get(url)
        self.assertContains(response, "clinic-datetime")
        self.assertContains(response, "Fecha y hora")

        response = self.client.post(url, {
            "paciente": paciente.id,
            "profesional": profesional.id,
            "servicio": servicio.id,
            "fecha_cita": "2026-06-26",
            "hora_cita": "03:15",
            "periodo_cita": "PM",
            "estado": "solicitada",
            "canal": "recepcion",
            "motivo": "Consulta de prueba",
            "sala": "1",
            "observaciones": "",
        })

        self.assertEqual(response.status_code, 302)
        cita = CitaClinica.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 15)
        self.assertEqual(timezone.localtime(cita.fecha_hora).minute, 15)

    def test_nueva_cita_clinica_permite_crear_paciente_sin_salir(self):
        url = reverse("clinica_crear_cita", args=[self.empresa.slug])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+ Nuevo paciente")
        self.assertContains(response, "patientQuickModal")

        response = self.client.post(
            reverse("clinica_crear_paciente_rapido", args=[self.empresa.slug]),
            {
                "tipo_id": "dni",
                "identidad": "0801198812345",
                "primer_nombre": "Laura",
                "primer_apellido": "Martínez",
                "fecha_nacimiento": "1988-07-10",
                "sexo": "femenino",
                "whatsapp": "99887766",
                "correo": "laura@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        paciente = Paciente.objects.get(id=response.json()["paciente"]["id"])
        self.assertEqual(paciente.nombre, "Laura Martínez")
        self.assertEqual(paciente.creado_por, self.user)
        self.assertIsNotNone(paciente.cliente_id)
        self.assertEqual(paciente.cliente.telefono_whatsapp, "99887766")

    def test_servicios_clinicos_incluyen_categoria_spa_estetica_no_medica(self):
        response = self.client.get(reverse("clinica_servicios", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Faciales, masajes, hidrataciones, tratamientos esteticos no medicos",
        )

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

    def test_formulario_general_masculino_limpia_campos_ginecologicos(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0401",
            primer_nombre="Carlos",
            primer_apellido="Diaz",
            nombre="Carlos Diaz",
            identidad="0801199200002",
            whatsapp="99990003",
        )
        form = PreconsultaClinicaPublicaForm(
            data={
                "primer_nombre": "Carlos",
                "segundo_nombre": "",
                "primer_apellido": "Diaz",
                "segundo_apellido": "",
                "identidad": "0801199200002",
                "fecha_nacimiento": "1992-05-10",
                "sexo": "masculino",
                "estado_civil": "soltero",
                "correo": "carlos@example.com",
                "telefono": "99990003",
                "direccion": "Tegucigalpa",
                "lugar_nacimiento": "Tegucigalpa",
                "ocupacion": "Ingeniero",
                "contacto_emergencia": "Ana Diaz",
                "telefono_emergencia": "99990004",
                "referido_por": "facebook",
                "motivo_consulta": "Valoracion",
                "procedimientos_interes": ["aumento_mamario", "braquioplastia"],
                "gine_gestas": "2",
                "gine_embarazada": ["si"],
                "gine_lactancia": ["si"],
                "gine_mamografia": ["si"],
                "gine_mamografia_fecha": "2026-01-10",
                "consentimiento_datos": "on",
            },
            paciente=paciente,
        )

        self.assertTrue(form.is_valid(), form.errors)
        datos = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(datos["procedimientos_interes"], ["aumento_mamario", "braquioplastia"])
        self.assertNotIn("gine_gestas", datos)
        self.assertNotIn("gine_embarazada", datos)
        self.assertNotIn("gine_mamografia_fecha", datos)

    def test_preconsulta_publica_se_genera_completa_y_actualiza_expediente(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0400",
            primer_nombre="Laura",
            primer_apellido="Perez",
            nombre="Laura Perez",
            identidad="0801199600001",
            whatsapp="99990001",
        )
        generar_url = reverse(
            "clinica_generar_enlace_preconsulta",
            args=[self.empresa.slug, paciente.id],
        )
        response = self.client.post(generar_url)

        self.assertEqual(response.status_code, 200)
        enlace = response.context["enlace_publico"]
        token_raw = enlace.rstrip("/").rsplit("/", 1)[-1]
        preconsulta = PreconsultaClinica.objects.get(paciente=paciente)
        self.assertEqual(preconsulta.token_hash, hash_token_preconsulta(token_raw))
        self.assertNotEqual(preconsulta.token_hash, token_raw)
        self.assertContains(response, "Enviar por WhatsApp")

        self.client.logout()
        publica_url = reverse("clinica_preconsulta_publica", args=[token_raw])
        response = self.client.get(publica_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preparemos su consulta")
        self.assertContains(response, "Laura")
        self.assertContains(response, "Paso 6 de 6")
        self.assertContains(response, "Braquioplastia (Brazos)")
        self.assertContains(response, "Musloplastia (Piernas)")
        self.assertContains(response, "Gluteoplastia (Gluteos)")
        self.assertContains(response, "Facebook")
        self.assertContains(response, "TikTok")
        self.assertContains(response, "YouTube")
        self.assertContains(response, "Referencia")
        self.assertContains(response, "Cocaina")
        self.assertContains(response, "Marihuana")
        self.assertContains(response, "Crack")
        self.assertNotContains(response, "Estado de salud actual")
        self.assertNotContains(response, "Otras sustancias o drogas")
        self.assertNotContains(response, "Otro medicamento")

        response = self.client.post(
            publica_url,
            {
                "primer_nombre": "Laura Maria",
                "segundo_nombre": "",
                "primer_apellido": "Perez",
                "segundo_apellido": "Lopez",
                "identidad": "0801199600001",
                "fecha_nacimiento": "1996-04-10",
                "sexo": "femenino",
                "estado_civil": "soltero",
                "correo": "laura@example.com",
                "telefono": "99990001",
                "direccion": "Tegucigalpa",
                "lugar_nacimiento": "Tegucigalpa",
                "ocupacion": "Administradora",
                "lugar_trabajo": "Empresa privada",
                "redes_sociales": "@laura",
                "informante": "Paciente",
                "contacto_emergencia": "Maria Perez",
                "telefono_emergencia": "99990002",
                "referido_por": "instagram",
                "motivo_consulta": "Valoracion de cirugia facial",
                "procedimientos_interes": ["rinoplastia", "prp_capilar"],
                "procedimientos_interes_otros": "Revision de cicatriz previa",
                "historia_mejorar": "Perfil facial y densidad capilar",
                "historia_tiempo_preocupacion": "2 anos",
                "historia_tratamientos_previos": "Mesoterapia capilar",
                "historia_expectativas": "Resultado natural",
                "funciones_organicas": "normal",
                "funciones_detalle": "",
                "revision_sistemas": "normal",
                "revision_sistemas_detalle": "",
                "antecedentes_hospitalarios": "on",
                "antecedentes_hospitalarios_detalle": "Apendicectomia en 2018",
                "antecedentes_personales": ["asma", "hipertension"],
                "antecedentes_personales_detalle": "Asma controlada",
                "medicamentos_habituales": ["anticonceptivos"],
                "medicamentos_habituales_detalle": "Uso diario",
                "antecedentes_familiares": ["diabetes"],
                "antecedentes_familiares_detalle": "Madre",
                "alergias_seleccion": ["medicamentos", "latex"],
                "alergias_otras": "Penicilina",
                "medicamentos_actuales_seleccion": ["anticonceptivos", "multivitaminicos"],
                "medicamentos_actuales_otros": "Vitamina D",
                "quirurgicos_operado": "si",
                "quirurgicos_detalle": "Apendicectomia en 2018",
                "tabaco_frecuencia": "nunca",
                "alcohol_frecuencia": "ocasional",
                "drogas_recreativas": ["si"],
                "drogas_recreativas_tipos": ["marihuana"],
                "drogas_recreativas_detalle": "Uso ocasional historico",
                "riesgo_tromboembolico": ["ninguno"],
                "gine_gestas": "0",
                "gine_embarazada": "no",
                "gine_lactancia": "no",
                "decision_cirugia": ["usted"],
                "expectativas_realistas": "si",
                "busca_perfeccion": "no",
                "multiples_cirugias_insatisfaccion": "no",
                "examen_peso": "64",
                "examen_talla": "165",
                "examen_imc": "23.5",
                "examen_pa": "120/80",
                "examen_fc": "72",
                "examen_sato2": "98",
                "dieta": "Balanceada",
                "ejercicio": "Tres veces por semana",
                "habitos": "No fuma",
                "alergias": "Penicilina",
                "antecedentes_infecciosos": "COVID-19 en 2022",
                "consentimiento_datos": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información recibida")
        preconsulta.refresh_from_db()
        paciente.refresh_from_db()
        self.assertEqual(preconsulta.estado, "completada")
        self.assertEqual(preconsulta.antecedentes_personales, ["asma", "hipertension"])
        formulario_general = preconsulta.datos_generales["formulario_general"]
        self.assertEqual(formulario_general["procedimientos_interes"], ["rinoplastia", "prp_capilar"])
        self.assertEqual(formulario_general["alergias_seleccion"], ["medicamentos", "latex"])
        self.assertEqual(formulario_general["medicamentos_actuales_seleccion"], ["anticonceptivos", "multivitaminicos"])
        self.assertEqual(formulario_general["drogas_recreativas"], ["si"])
        self.assertEqual(formulario_general["drogas_recreativas_tipos"], ["marihuana"])
        self.assertEqual(formulario_general["examen_peso"], "64")
        self.assertEqual(formulario_general["examen_sato2"], "98")
        self.assertEqual(paciente.nombre, "Laura Maria Perez Lopez")
        self.assertEqual(paciente.correo, "laura@example.com")
        self.assertTrue(paciente.es_alergico)
        self.assertIn("Asma bronquial", paciente.antecedentes_medicos)

        response = self.client.get(publica_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información recibida")
