from io import BytesIO
from tempfile import TemporaryDirectory
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from crm.models import CitaCliente, ConfiguracionCRM, OpcionServicioAgenda
from facturacion.models import Cliente, Producto
from .forms import PreconsultaClinicaPublicaForm
from .models import CitaClinica, ConsentimientoClinico, DocumentoClinicoPaciente, ExamenPaciente, HistoriaClinicaEspecialidad, InvitacionRegistroPaciente, Paciente, PacienteFotoEvolucion, PlantillaReceta, PreconsultaClinica, ProfesionalSalud, RecetaMedica, RecetaMedicaDetalle, ServicioClinico
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

    def _foto_prueba(self, nombre="paciente.jpg"):
        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), color=(24, 130, 160)).save(image_buffer, format="JPEG")
        return SimpleUploadedFile(nombre, image_buffer.getvalue(), content_type="image/jpeg")

    def _datos_formulario_general(self, **overrides):
        identidad = overrides.pop("identidad", "0801199912345")
        data = {
            "nombres": "Ana",
            "apellidos": "Mejia",
            "identidad": identidad,
            "fecha_nacimiento": "1999-08-12",
            "sexo": "masculino",
            "estado_civil": "soltero",
            "correo": "ana@example.com",
            "telefono_codigo_area": "504",
            "telefono": "99998888",
            "direccion": "",
            "lugar_nacimiento": "Tegucigalpa",
            "ocupacion": "Administradora",
            "lugar_trabajo": "No aplica",
            "informante": "yo_mismo",
            "contacto_emergencia_completo": "Maria Perez - 9999-9999",
            "referido_por": "facebook",
            "motivo_categoria": ["no_aplica"],
            "procedimientos_interes": [],
            "procedimientos_interes_otros": "No aplica",
            "funciones_organicas": "normal",
            "funciones_detalle": "No aplica",
            "antecedentes_personales": ["no_aplica"],
            "antecedentes_personales_detalle": "No aplica",
            "alergias_seleccion": ["ninguna"],
            "alergias_otras": "No aplica",
            "alergias": "No aplica",
            "medicamentos_habituales": ["no_aplica"],
            "medicamentos_habituales_detalle": "No aplica",
            "medicamentos_actuales_seleccion": ["ninguno"],
            "medicamentos_actuales_otros": "No aplica",
            "antecedentes_infecciosos": "No aplica",
            "antecedentes_hospitalarios": ["no"],
            "antecedentes_hospitalarios_detalle": "No aplica",
            "quirurgicos_operado": ["no"],
            "quirurgicos_detalle": "No aplica",
            "consumo_riesgo": ["ninguno"],
            "consumo_riesgo_detalle": "No aplica",
            "dieta": ["balanceada"],
            "ejercicio": ["ocasional"],
            "antecedentes_familiares": ["no_aplica"],
            "antecedentes_familiares_detalle": "No aplica",
            "riesgo_tromboembolico": ["ninguno"],
            "riesgo_tromboembolico_otros": "No aplica",
            "evaluacion_psicologica": ["ninguna"],
            "evaluacion_psicologica_detalle": "No aplica",
            "expectativas_realistas": ["si"],
            "busca_perfeccion": ["no"],
            "multiples_cirugias_insatisfaccion": ["no"],
            "motivo_consulta": "Registro general",
            "consentimiento_datos": "on",
            "foto_perfil": self._foto_prueba(),
        }
        data.update(overrides)
        return data

    def test_vista_clinica_completa_renderiza_textos_sin_mojibake(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-TEST",
            nombre="Paciente Consolidado",
            identidad="1101200800619",
            fecha_nacimiento="2000-01-01",
            rh="O+",
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="summary-clinical-hero"')
        self.assertContains(response, 'id="resumen-paciente"')
        self.assertContains(response, 'id="informacion-paciente"')
        self.assertContains(response, 'id="trabajo-clinico"')
        self.assertContains(response, "Historia clínica completa")
        self.assertContains(response, "Cuadros clínicos para escribir sin salir de esta pantalla")
        self.assertContains(response, "Guardar historia clínica de Capilar")
        self.assertContains(response, "años")
        self.assertContains(response, "· RH")
        self.assertNotContains(response, "area-nav")
        self.assertNotContains(response, 'id="area-')
        self.assertNotContains(response, "Ã")
        self.assertNotContains(response, "Â")

    def test_historia_clinica_paciente_es_hoja_continua_sin_mojibake(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-HOJA",
            nombre="Paciente Hoja Clinica",
            identidad="1101200800620",
            fecha_nacimiento="1995-05-10",
            rh="A+",
        )
        url = reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historia clínica del paciente")
        self.assertContains(response, "Hoja clínica continua")
        self.assertContains(response, "Cuadro de trabajo")
        self.assertContains(response, "Escribir en esta área")
        self.assertContains(response, "años")
        self.assertNotContains(response, "Ã")
        self.assertNotContains(response, "Â")

    def test_vista_clinica_completa_guarda_nota_inline_por_area(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-INLINE",
            nombre="Paciente Nota Inline",
            identidad="1101200800633",
            fecha_nacimiento="1990-01-01",
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url, {
            "tipo_historia": "capilar",
            "historia_capilar-fecha_atencion": "2026-07-28T09:30",
            "historia_capilar-plan_tratamiento": "Historia actual, diagnostico y plan desde clinica completa.",
            "historia_capilar-estado": "borrador",
        })

        self.assertRedirects(response, url)
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente, tipo="capilar")
        self.assertEqual(historia.plan_tratamiento, "Historia actual, diagnostico y plan desde clinica completa.")
        self.assertEqual(historia.creado_por, self.user)

    def test_vista_clinica_completa_edita_texto_inline(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-EDIT-INLINE",
            nombre="Paciente Editar Texto",
            identidad="1101200800644",
            fecha_nacimiento="1990-01-01",
        )
        historia = HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="capilar",
            plan_tratamiento="Texto anterior",
            creado_por=self.user,
            actualizado_por=self.user,
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url, {
            "accion": "editar_texto_historia",
            "historia_id": historia.id,
            "texto_historia": "Texto actualizado desde la vista completa.",
        })

        self.assertRedirects(response, url)
        historia.refresh_from_db()
        self.assertEqual(historia.plan_tratamiento, "Texto actualizado desde la vista completa.")
        self.assertEqual(historia.actualizado_por, self.user)

    def test_boton_eliminar_nota_solo_aparece_para_dueno_erp(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-DEL-VIS",
            nombre="Paciente Delete Visible",
            identidad="1101200800670",
            fecha_nacimiento="1990-01-01",
        )
        HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="capilar",
            plan_tratamiento="Nota privada",
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)
        self.assertNotContains(response, "Eliminar nota")

        dueno = get_user_model().objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.user.rol_sistema,
        )
        self.client.force_login(dueno)
        response = self.client.get(url)

        self.assertContains(response, "Eliminar nota")

    def test_solo_dueno_erp_puede_eliminar_nota_clinica(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-DEL-NOTA",
            nombre="Paciente Delete Nota",
            identidad="1101200800671",
            fecha_nacimiento="1990-01-01",
        )
        historia = HistoriaClinicaEspecialidad.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="capilar",
            plan_tratamiento="Nota a eliminar",
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url, {
            "accion": "eliminar_historia",
            "historia_id": historia.id,
        })
        self.assertRedirects(response, url)
        self.assertTrue(HistoriaClinicaEspecialidad.objects.filter(id=historia.id).exists())

        dueno = get_user_model().objects.create_user(
            username="daniel.varela",
            email="dannyvarela25@gmail.com",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.user.rol_sistema,
        )
        self.client.force_login(dueno)
        response = self.client.post(url, {
            "accion": "eliminar_historia",
            "historia_id": historia.id,
        })

        self.assertRedirects(response, url)
        self.assertFalse(HistoriaClinicaEspecialidad.objects.filter(id=historia.id).exists())

    def test_seguimientos_paciente_tiene_pantalla_propia_y_edicion(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-SEG",
            nombre="Paciente Seguimiento",
            identidad="1101200800672",
            fecha_nacimiento="1990-01-01",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dra. Candy Luque",
            especialidad="Cirugia plastica",
        )
        cita = CitaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            profesional=profesional,
            fecha_hora=timezone.make_aware(datetime(2026, 8, 1, 14, 0)),
            estado="confirmada",
            canal="recepcion",
            motivo="Recordatorio: Botox",
            es_recordatorio_tratamiento=True,
            tratamiento_recordatorio="Botox",
        )
        url = reverse("clinica_seguimientos_paciente", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seguimiento estético / clínico")
        self.assertContains(response, "Recordatorios del paciente")

        response = self.client.post(url, {
            "accion": "editar_recordatorio",
            "recordatorio_id": cita.id,
            "tratamiento": "Retoque de Botox actualizado",
            "fecha": "2026-08-15",
            "hora": "03:30",
            "periodo": "PM",
            "profesional": str(profesional.id),
            "nota": "Ajustado desde modulo de seguimientos.",
        })

        self.assertRedirects(response, url)
        cita.refresh_from_db()
        self.assertEqual(cita.tratamiento_recordatorio, "Retoque de Botox actualizado")
        self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 15)
        self.assertEqual(timezone.localtime(cita.fecha_hora).minute, 30)

    def test_vista_clinica_completa_toma_profesional_del_usuario_vinculado(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-PRO-AUTO",
            nombre="Paciente Profesional Automatico",
            identidad="1101200800655",
            fecha_nacimiento="1990-01-01",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            usuario=self.user,
            nombre="Dra. Candy Luque",
            especialidad="Cirugia plastica",
        )
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url, {
            "tipo_historia": "capilar",
            "historia_capilar-fecha_atencion": "2026-07-29T09:30",
            "historia_capilar-plan_tratamiento": "Plan con profesional automatico.",
            "historia_capilar-estado": "borrador",
        })

        self.assertRedirects(response, url)
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente, tipo="capilar")
        self.assertEqual(historia.profesional, profesional)

    def test_vista_clinica_completa_detecta_profesional_luis_por_usuario(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-LUIS-AUTO",
            nombre="Paciente Luis Automatico",
            identidad="1101200800666",
            fecha_nacimiento="1990-01-01",
        )
        usuario_luis = get_user_model().objects.create_user(
            username="luis.gonzales",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.user.rol_sistema,
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dr. Luis Gonzales",
            especialidad="Cirugia",
        )
        self.client.force_login(usuario_luis)
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url, {
            "tipo_historia": "cirugia_plastica",
            "historia_cirugia_plastica-fecha_atencion": "2026-07-29T10:30",
            "historia_cirugia_plastica-plan_tratamiento": "Plan asignado a Luis.",
            "historia_cirugia_plastica-estado": "borrador",
        })

        self.assertRedirects(response, url)
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente, tipo="cirugia_plastica")
        self.assertEqual(historia.profesional, profesional)

    def test_editar_paciente_usa_formulario_general_moderno(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-EDIT",
            primer_nombre="Paciente",
            primer_apellido="Viejo",
            nombre="Paciente Viejo",
            identidad="0801199912345",
            fecha_nacimiento="1999-08-12",
            sexo="femenino",
            estado_civil="soltero",
            whatsapp="99990000",
            telefono="99990000",
        )
        url = reverse("clinica_editar_paciente", args=[self.empresa.slug, paciente.id])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Actualice los datos generales del paciente")
        self.assertContains(response, "Actualizar paciente")
        self.assertNotContains(response, "Foto inicial del paciente")

        data = self._datos_formulario_general(
            nombres="Paciente Actualizado",
            apellidos="Desde Formulario",
            identidad=paciente.identidad,
            telefono="98887777",
            correo="nuevo@example.com",
        )
        response = self.client.post(url, data)

        self.assertRedirects(response, reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id]))
        paciente.refresh_from_db()
        self.assertEqual(paciente.nombre, "Paciente Actualizado Desde Formulario")
        self.assertEqual(paciente.whatsapp, "50498887777")
        self.assertEqual(paciente.correo, "nuevo@example.com")

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
            "pagada": "on",
            "sala": "1",
            "observaciones": "",
        })

        self.assertEqual(response.status_code, 302)
        cita = CitaClinica.objects.get(empresa=self.empresa, paciente=paciente)
        self.assertTrue(cita.pagada)
        self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 15)
        self.assertEqual(timezone.localtime(cita.fecha_hora).minute, 15)
        agenda = CitaCliente.objects.get(empresa=self.empresa, cita_clinica=cita)
        self.assertTrue(agenda.pagada)
        self.assertTrue(agenda.enviar_confirmacion_whatsapp)
        self.assertTrue(agenda.recordatorio_semana_whatsapp)
        self.assertTrue(agenda.recordatorio_dia_whatsapp)

    def test_paciente_medico_exige_identidad_en_validacion(self):
        for slug in ("hospital_mia", "medical_spa"):
            with self.subTest(slug=slug):
                self.empresa.slug = slug
                self.empresa.save(update_fields=["slug"])
                paciente = Paciente(
                    empresa=self.empresa,
                    expediente_codigo=f"{slug}-SIN-ID",
                    primer_nombre="Paciente",
                    primer_apellido="Sin Documento",
                    nombre="Paciente Sin Documento",
                )

                with self.assertRaisesMessage(ValidationError, "La identidad es obligatoria"):
                    paciente.full_clean()

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

    def test_rol_con_expediente_puede_ver_preconsultas_sin_permiso_pacientes(self):
        rol_medico = RolSistema.objects.create(
            nombre="Medico expediente",
            codigo="medico-expediente-test",
            activo=True,
            puede_clinica=True,
            puede_pacientes=False,
            puede_expediente_clinico=True,
        )
        medico = get_user_model().objects.create_user(
            username="medico-expediente",
            password="pass",
            empresa=self.empresa,
            rol_sistema=rol_medico,
        )
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-PRE-001",
            nombre="Paciente Preconsulta",
            identidad="0801198800001",
        )
        preconsulta = PreconsultaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="general",
            token_hash="hash-preconsulta-detalle-test",
            token_preview="preview",
            estado="completada",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=1),
            fecha_completada=timezone.now(),
            motivo_consulta="Consulta completada",
            datos_generales={
                "nombres": "Paciente",
                "apellidos": "Preconsulta",
                "formulario_general": {"historia_mejorar": "Desea mejorar"},
            },
        )
        self.client.force_login(medico)

        response = self.client.get(
            reverse("clinica_preconsulta_detalle", args=[self.empresa.slug, paciente.id, preconsulta.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consulta completada")
        self.assertContains(response, "Formulario completado · General")
        self.assertContains(response, "Información privada y confidencial")
        self.assertNotContains(response, "Ã")
        self.assertNotContains(response, "Â")

    def test_servicios_clinicos_incluyen_categoria_spa_estetica_no_medica(self):
        response = self.client.get(reverse("clinica_servicios", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Faciales, masajes, hidrataciones, tratamientos esteticos no medicos",
        )

    def test_catalogo_tratamientos_de_citas_se_administra_desde_clinica(self):
        self.user.es_administrador_empresa = True
        self.user.save(update_fields=["es_administrador_empresa"])

        response = self.client.post(
            reverse("clinica_tratamientos", args=[self.empresa.slug]),
            {"accion": "crear_opcion_agenda", "nombre": "Terapia intravenosa"},
        )
        self.assertRedirects(response, reverse("clinica_tratamientos", args=[self.empresa.slug]))
        opcion = OpcionServicioAgenda.objects.get(
            empresa=self.empresa,
            categoria="tratamientos",
            nombre="Terapia intravenosa",
        )

        listado = self.client.get(reverse("clinica_tratamientos", args=[self.empresa.slug]))
        self.assertContains(listado, "Terapia intravenosa")
        self.assertContains(
            listado,
            reverse("clinica_tratamiento_agenda_editar", args=[self.empresa.slug, opcion.id]),
        )

        response = self.client.post(
            reverse("clinica_tratamiento_agenda_editar", args=[self.empresa.slug, opcion.id]),
            {"nombre": "Terapia intravenosa premium"},
        )
        self.assertRedirects(response, reverse("clinica_tratamientos", args=[self.empresa.slug]))
        opcion.refresh_from_db()
        self.assertEqual(opcion.nombre, "Terapia intravenosa premium")
        self.assertFalse(opcion.activo)

        response = self.client.post(
            reverse("clinica_tratamiento_agenda_eliminar", args=[self.empresa.slug, opcion.id]),
        )
        self.assertRedirects(response, reverse("clinica_tratamientos", args=[self.empresa.slug]))
        self.assertFalse(OpcionServicioAgenda.objects.filter(id=opcion.id).exists())

    def test_dueno_erp_puede_editar_y_eliminar_servicios_clinicos(self):
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Servicio temporal",
            categoria="tratamiento",
            duracion_minutos=60,
        )
        dueno = get_user_model().objects.create_user(
            username="dannyvarela25",
            email="dannyvarela25@gmail.com",
            password="pass",
            empresa=self.empresa,
            rol_sistema=self.user.rol_sistema,
        )
        self.client.force_login(dueno)

        listado = self.client.get(reverse("clinica_servicios", args=[self.empresa.slug]))
        self.assertContains(listado, reverse("clinica_servicio_editar", args=[self.empresa.slug, servicio.id]))
        self.assertContains(listado, reverse("clinica_servicio_eliminar", args=[self.empresa.slug, servicio.id]))

        response = self.client.post(
            reverse("clinica_servicio_editar", args=[self.empresa.slug, servicio.id]),
            {
                "nombre": "Servicio actualizado",
                "categoria": "tratamiento",
                "duracion_minutos": 45,
                "color_calendario": "#2563eb",
                "precio_referencia": "100.00",
                "activo": "on",
            },
        )
        self.assertRedirects(response, reverse("clinica_servicios", args=[self.empresa.slug]))
        servicio.refresh_from_db()
        self.assertEqual(servicio.nombre, "Servicio actualizado")

        response = self.client.post(
            reverse("clinica_servicio_eliminar", args=[self.empresa.slug, servicio.id])
        )
        self.assertRedirects(response, reverse("clinica_servicios", args=[self.empresa.slug]))
        self.assertFalse(ServicioClinico.objects.filter(id=servicio.id).exists())

    def test_usuario_ajeno_no_puede_editar_ni_eliminar_servicios_clinicos(self):
        servicio = ServicioClinico.objects.create(
            empresa=self.empresa,
            nombre="Servicio protegido",
            categoria="consulta",
        )

        response = self.client.post(
            reverse("clinica_servicio_editar", args=[self.empresa.slug, servicio.id]),
            {
                "nombre": "Cambio no autorizado",
                "categoria": "consulta",
                "duracion_minutos": 60,
                "precio_referencia": "0.00",
            },
        )
        self.assertRedirects(response, reverse("clinica_servicios", args=[self.empresa.slug]))
        servicio.refresh_from_db()
        self.assertEqual(servicio.nombre, "Servicio protegido")

        response = self.client.post(
            reverse("clinica_servicio_eliminar", args=[self.empresa.slug, servicio.id])
        )
        self.assertRedirects(response, reverse("clinica_servicios", args=[self.empresa.slug]))
        self.assertTrue(ServicioClinico.objects.filter(id=servicio.id).exists())

    def test_crear_paciente_alergico_y_mostrar_alerta_en_lista(self):
        response = self.client.get(reverse("clinica_crear_paciente", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formulario de historia clinica")
        self.assertContains(response, "Crear expediente")
        self.assertContains(response, "Subir archivo")

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            self._datos_formulario_general(
                alergias="Penicilina",
                alergias_seleccion=["medicamentos"],
            ),
        )

        self.assertEqual(response.status_code, 302)
        paciente = Paciente.objects.get(empresa=self.empresa, identidad="0801199912345")
        self.assertTrue(paciente.es_alergico)
        self.assertEqual(paciente.alergias, "Penicilina")
        self.assertIsNotNone(paciente.cliente)
        self.assertTrue(Cliente.objects.filter(empresa=self.empresa, rtn="0801199912345").exists())
        self.assertEqual(PreconsultaClinica.objects.filter(paciente=paciente, estado="completada").count(), 1)

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
        self.assertContains(response, "Plan de consentimiento")
        self.assertContains(response, "patient-evolution-carousel")
        self.assertContains(response, "patientPhotoModal")

    @patch("clinica.views._sincronizar_cliente_facturacion_paciente")
    def test_crear_paciente_muestra_error_si_falla_sincronizacion_cliente(self, sincronizar):
        sincronizar.side_effect = ValidationError({"rtn": "Ya existe un cliente con este RTN en la empresa."})

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            self._datos_formulario_general(identidad="0801199912350", nombres="Paciente", apellidos="ConError"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revise los campos marcados")
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, identidad="0801199912350").exists())

    def test_crear_paciente_no_se_bloquea_por_foto_inicial_mayor(self):
        image_buffer = BytesIO()
        Image.effect_noise((3200, 3200), 100).convert("RGB").save(image_buffer, format="JPEG", quality=95)
        self.assertGreater(len(image_buffer.getvalue()), 5 * 1024 * 1024)
        foto_grande = SimpleUploadedFile("grande.jpg", image_buffer.getvalue(), content_type="image/jpeg")

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            self._datos_formulario_general(identidad="0801199912351", nombres="Foto", apellidos="Grande", foto_perfil=foto_grande),
        )

        self.assertEqual(response.status_code, 302)
        paciente = Paciente.objects.get(empresa=self.empresa, identidad="0801199912351")
        self.assertFalse(bool(paciente.foto_perfil))

    def test_crear_paciente_refresca_expediente_si_el_codigo_ya_existe(self):
        Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-00001",
            nombre="Paciente Inicial",
            identidad="0801199900001",
        )
        Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-00118",
            nombre="Paciente Existente",
            identidad="0801199900118",
        )

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            self._datos_formulario_general(identidad="0801199912352", nombres="Codigo", apellidos="Nuevo"),
        )

        self.assertEqual(response.status_code, 302)
        paciente = Paciente.objects.get(empresa=self.empresa, identidad="0801199912352")
        self.assertEqual(paciente.expediente_codigo, "MIA-00119")

    def test_paciente_permite_subir_plan_consentimiento_pdf(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-CONS",
            nombre="Paciente Consentimiento",
            identidad="0801199900001",
        )
        pdf = SimpleUploadedFile(
            "consentimiento.pdf",
            b"%PDF-1.4\n%test\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
            content_type="application/pdf",
        )
        response = self.client.post(
            reverse("clinica_subir_consentimiento_paciente", args=[self.empresa.slug, paciente.id]),
            {
                "titulo": "Consentimiento cirugía capilar",
                "version": "2026-07",
                "firmado_por": "Paciente Consentimiento",
                "fecha_firma": "2026-07-09T09:30",
                "estado": "firmado",
                "archivo": pdf,
            },
        )

        self.assertEqual(response.status_code, 302)
        consentimiento = ConsentimientoClinico.objects.get(paciente=paciente)
        self.assertEqual(consentimiento.titulo, "Consentimiento cirugía capilar")
        self.assertEqual(consentimiento.estado, "firmado")
        self.assertTrue(consentimiento.archivo.name.endswith(".pdf"))

        detalle = self.client.get(reverse("clinica_consentimientos_paciente", args=[self.empresa.slug, paciente.id]))
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, "Biblioteca de PDF firmados")
        self.assertContains(detalle, "Consentimiento cirugía capilar")
        self.assertContains(detalle, "Abrir PDF")

    def test_paciente_permite_subir_examen_y_crear_receta_imprimible(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-RX",
            nombre="Paciente Receta",
            identidad="0801199900099",
        )
        producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Antibiotico demo",
            codigo="RX-001",
            precio=100,
        )
        archivo = SimpleUploadedFile("examen.pdf", b"%PDF-1.4 test", content_type="application/pdf")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("clinica_subir_examen_paciente", args=[self.empresa.slug, paciente.id]),
                {
                    "titulo": "Hemograma",
                    "tipo": "laboratorio",
                    "fecha_examen": "2026-07-11",
                    "laboratorio": "Lab Demo",
                    "descripcion": "Resultado preoperatorio",
                    "archivo": archivo,
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(ExamenPaciente.objects.filter(paciente=paciente, titulo="Hemograma").exists())
            response = self.client.get(reverse("clinica_examenes_paciente", args=[self.empresa.slug, paciente.id]))
            self.assertContains(response, "Hemograma")

        response = self.client.post(
            reverse("clinica_crear_receta_paciente", args=[self.empresa.slug, paciente.id]),
            {
                "fecha": "2026-07-11",
                "diagnostico": "Control postoperatorio",
                "productos": [producto.id],
                "indicaciones": "Tomar 1 tableta cada 12 horas por 5 dias.",
                "observaciones": "No suspender sin indicacion medica.",
            },
        )
        receta = RecetaMedica.objects.get(paciente=paciente)
        self.assertRedirects(response, reverse("clinica_receta_imprimir", args=[self.empresa.slug, paciente.id, receta.id]))
        response = self.client.get(reverse("clinica_receta_imprimir", args=[self.empresa.slug, paciente.id, receta.id]))
        self.assertContains(response, "Receta medica")
        self.assertContains(response, "Antibiotico demo")
        self.assertContains(response, "Tomar 1 tableta")

    def test_receta_avanzada_permite_busqueda_multiple_manual_y_plantillas(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-RX-ADV",
            nombre="Paciente Receta Avanzada",
            identidad="0801199900199",
        )
        producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Medicamento catálogo",
            codigo="RX-ADV-01",
            precio=125,
        )

        response = self.client.post(
            reverse("clinica_crear_plantilla_receta", args=[self.empresa.slug]),
            {
                "nombre": "Plantilla control",
                "diagnostico": "Control clínico",
                "indicaciones_generales": "Mantener hidratación.",
                "observaciones": "Revisar en cinco días.",
                "activa": "on",
                "medicamento_producto_id": [str(producto.id), ""],
                "medicamento_manual": ["", "Medicamento externo"],
                "medicamento_cantidad": ["1 caja", "10 tabletas"],
                "medicamento_indicaciones": ["Cada 12 horas", "Una diaria"],
                "medicamento_observaciones": ["Con alimentos", "Por la noche"],
            },
        )
        self.assertRedirects(response, reverse("clinica_plantillas_recetas", args=[self.empresa.slug]))
        plantilla = PlantillaReceta.objects.get(empresa=self.empresa, nombre="Plantilla control")
        self.assertEqual(plantilla.detalles.count(), 2)
        self.assertEqual(plantilla.detalles.filter(producto=producto).count(), 1)
        self.assertEqual(plantilla.detalles.filter(medicamento_manual="Medicamento externo").count(), 1)

        response = self.client.get(reverse("clinica_crear_receta_paciente", args=[self.empresa.slug, paciente.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscar por nombre o código")
        self.assertContains(response, "Plantilla control")

        response = self.client.post(
            reverse("clinica_crear_receta_paciente", args=[self.empresa.slug, paciente.id]),
            {
                "fecha": "2026-08-18",
                "diagnostico": "Control clínico",
                "indicaciones": "Mantener hidratación.",
                "observaciones": "Revisar en cinco días.",
                "medicamento_producto_id": [str(producto.id), ""],
                "medicamento_manual": ["", "Medicamento externo"],
                "medicamento_cantidad": ["1 caja", "10 tabletas"],
                "medicamento_indicaciones": ["Cada 12 horas", "Una diaria"],
                "medicamento_observaciones": ["Con alimentos", "Por la noche"],
            },
        )
        receta = RecetaMedica.objects.get(paciente=paciente)
        self.assertRedirects(response, reverse("clinica_receta_imprimir", args=[self.empresa.slug, paciente.id, receta.id]))
        self.assertEqual(RecetaMedicaDetalle.objects.filter(receta=receta).count(), 2)
        self.assertEqual(list(receta.productos.values_list("id", flat=True)), [producto.id])
        response = self.client.get(reverse("clinica_receta_imprimir", args=[self.empresa.slug, paciente.id, receta.id]))
        self.assertContains(response, "Medicamento catálogo")
        self.assertContains(response, "Medicamento externo")
        self.assertContains(response, "Cada 12 horas")

    def test_recetas_avanzadas_solo_estan_disponibles_en_las_tres_empresas_clinicas(self):
        modulo = Modulo.objects.get(codigo="clinica_medica")
        empresas = [self.empresa]
        for slug, nombre, rtn in [
            ("serviciosmedicos", "Servicios Médicos", "0801199900301"),
            ("luque_aestetic", "Luque Aesthetic", "0801199900302"),
        ]:
            empresa = Empresa.objects.create(nombre=nombre, slug=slug, rtn=rtn)
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            empresas.append(empresa)
        self.user.empresas_acceso.add(*empresas[1:])

        for indice, empresa in enumerate(empresas, start=1):
            paciente = Paciente.objects.create(
                empresa=empresa,
                expediente_codigo=f"RX-EMP-{indice}",
                nombre=f"Paciente {empresa.nombre}",
                identidad=f"08011999002{indice:02d}",
            )
            response = self.client.get(reverse("clinica_crear_receta_paciente", args=[empresa.slug, paciente.id]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Buscar por nombre o código")
            self.assertContains(response, "Administrar plantillas")

        empresa_fuera = Empresa.objects.create(
            nombre="Empresa sin recetas avanzadas",
            slug="empresa_general",
            rtn="0801199900303",
        )
        EmpresaModulo.objects.create(empresa=empresa_fuera, modulo=modulo, activo=True)
        self.user.empresas_acceso.add(empresa_fuera)
        paciente_fuera = Paciente.objects.create(
            empresa=empresa_fuera,
            expediente_codigo="RX-GENERAL",
            nombre="Paciente General",
            identidad="0801199900299",
        )
        response = self.client.get(
            reverse("clinica_crear_receta_paciente", args=[empresa_fuera.slug, paciente_fuera.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Buscar por nombre o código")
        response = self.client.get(reverse("clinica_plantillas_recetas", args=[empresa_fuera.slug]))
        self.assertEqual(response.status_code, 404)

    def test_paciente_permite_documentos_clinicos_e_incapacidad_imprimible(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-DOC",
            nombre="Paciente Documentos",
            identidad="0801199900101",
        )
        profesional = ProfesionalSalud.objects.create(
            empresa=self.empresa,
            nombre="Dra. Demo",
            especialidad="Medicina",
            activo=True,
        )
        archivo = SimpleUploadedFile("resultado.pdf", b"%PDF-1.4 test", content_type="application/pdf")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("clinica_subir_documento_categoria_paciente", args=[self.empresa.slug, paciente.id, "laboratorio"]),
                {
                    "titulo": "Quimica sanguinea",
                    "fecha_documento": "2026-07-12",
                    "entidad": "Lab Demo",
                    "descripcion": "Resultado externo",
                    "archivo": archivo,
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                DocumentoClinicoPaciente.objects.filter(
                    paciente=paciente,
                    categoria="laboratorio",
                    titulo="Quimica sanguinea",
                ).exists()
            )
            response = self.client.get(
                reverse("clinica_documentos_categoria_paciente", args=[self.empresa.slug, paciente.id, "laboratorio"])
            )
            self.assertContains(response, "Trabajos de laboratorio")
            self.assertContains(response, "Quimica sanguinea")

        response = self.client.post(
            reverse("clinica_subir_documento_categoria_paciente", args=[self.empresa.slug, paciente.id, "incapacidad"]),
            {
                "titulo": "Incapacidad medica",
                "fecha_documento": "2026-07-12",
                "fecha_inicio": "2026-07-12",
                "fecha_fin": "2026-07-14",
                "profesional": profesional.id,
                "descripcion": "Reposo medico por procedimiento ambulatorio.",
            },
        )
        self.assertEqual(response.status_code, 302)
        incapacidad = DocumentoClinicoPaciente.objects.get(paciente=paciente, categoria="incapacidad")
        self.assertEqual(incapacidad.dias, 3)
        response = self.client.get(
            reverse("clinica_incapacidad_imprimir", args=[self.empresa.slug, paciente.id, incapacidad.id])
        )
        self.assertContains(response, "Certificado de incapacidad")
        self.assertContains(response, "Reposo medico")

    def test_paciente_evolucion_muestra_fotos_y_videos_separados(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-EVO",
            nombre="Paciente Evolucion",
            identidad="0801199900002",
        )
        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), color=(24, 130, 160)).save(image_buffer, format="JPEG")
        foto = SimpleUploadedFile("control.jpg", image_buffer.getvalue(), content_type="image/jpeg")
        video = SimpleUploadedFile("control.mp4", b"video-test", content_type="video/mp4")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("clinica_registrar_foto_evolucion", args=[self.empresa.slug, paciente.id]),
                {
                    "tipo": "control",
                    "titulo": "Foto control inicial",
                    "descripcion": "Comparacion frontal",
                    "fecha": "2026-07-10T09:00",
                    "imagen": foto,
                },
            )
            self.assertRedirects(
                response,
                reverse("clinica_evolucion_paciente", args=[self.empresa.slug, paciente.id]),
            )

            response = self.client.post(
                reverse("clinica_registrar_foto_evolucion", args=[self.empresa.slug, paciente.id]),
                {
                    "tipo": "evolucion",
                    "titulo": "Video movilidad facial",
                    "descripcion": "Revision con movimiento",
                    "fecha": "2026-07-10T10:00",
                    "video": video,
                },
            )
            self.assertRedirects(
                response,
                reverse("clinica_evolucion_paciente", args=[self.empresa.slug, paciente.id]),
            )

            detalle = self.client.get(reverse("clinica_evolucion_paciente", args=[self.empresa.slug, paciente.id]))
            self.assertEqual(detalle.status_code, 200)
            self.assertContains(detalle, "Galeria fotografica")
            self.assertContains(detalle, "Registro audiovisual")
            self.assertContains(detalle, "Foto control inicial")
            self.assertContains(detalle, "Video movilidad facial")
            self.assertContains(detalle, "evoModal")

    def test_no_permite_identidad_con_guiones_o_espacios(self):
        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            self._datos_formulario_general(identidad="0801-1994-13996", nombres="Luis", apellidos="Lopez"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Utilice solamente numeros")
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, identidad="0801-1994-13996").exists())

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
        self.assertContains(response, "Cumpleaños del mes")
        self.assertContains(response, "Cumple")
        nombres = list(response.context["pacientes"])
        self.assertEqual(nombres[0], cumpleanero)
        self.assertIn(paciente_normal, nombres)

    def test_lista_pacientes_hospital_mia_usa_vista_premium_con_acciones_rapidas(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MIA-0550",
            primer_nombre="Sofia",
            primer_apellido="Reyes",
            nombre="Sofia Reyes",
            identidad="0801199600550",
            telefono="99990011",
            prefijo_telefono="504",
        )

        response = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["vista_premium_pacientes"])
        self.assertContains(response, "patients-web-premium")
        self.assertContains(response, "tel:+50499990011")
        self.assertContains(response, "https://wa.me/50499990011")
        self.assertContains(
            response,
            reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, paciente.id]),
        )
        self.assertContains(response, "Historia clínica")

    def test_directorio_premium_se_comparte_con_las_cuatro_empresas_clinicas(self):
        modulo = Modulo.objects.get(codigo="clinica_medica")
        empresas = [self.empresa]
        for indice, (slug, nombre) in enumerate(
            [
                ("medical_spa", "Mia Medical Spa"),
                ("luque_aestetic", "Luque Aestetic"),
                ("serviciosmedicos", "Servicios Médicos"),
            ],
            start=1,
        ):
            empresa = Empresa.objects.create(nombre=nombre, slug=slug, rtn=f"08011999000{indice}")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            self.user.empresas_acceso.add(empresa)
            empresas.append(empresa)

        for empresa in empresas:
            with self.subTest(empresa=empresa.slug):
                response = self.client.get(reverse("clinica_pacientes", args=[empresa.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["vista_premium_pacientes"])
                self.assertContains(response, "patients-web-premium")
                self.assertContains(response, f"Directorio clínico · {empresa.nombre}")

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
        for nombre in ["Capilar", "Cirugia plastica y reconstructiva", "Tratamiento Estetico / Piel", "Enfermeria", "Terapias", "Camara hiperbarica"]:
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
                "historia_enfermedad_actual": "Paciente refiere caida progresiva desde hace seis meses.",
                "signos_vitales": "PA 120/80",
                "examen_fisico": "Disminucion de densidad en region frontal.",
                "evaluacion_clinica": "Evaluacion capilar inicial",
                "diagnostico": "Alopecia en estudio",
                "analisis_clinico": "Probable alopecia androgenetica inicial.",
                "procedimiento": "Tricoscopia",
                "conducta": "Solicitar laboratorios y documentar fotografias.",
                "plan_tratamiento": "Paciente refiere caida progresiva desde hace seis meses.\nDisminucion de densidad en region frontal.\nProbable alopecia androgenetica inicial.\nSolicitar laboratorios y documentar fotografias.\nControl en 30 dias.\nPaciente ansiosa por evolucion del cuadro.",
                "indicaciones": "Aplicar tratamiento indicado",
                "observaciones": "Sin complicaciones",
                "notas_privadas_doctor": "Paciente ansiosa por evolucion del cuadro.",
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
        self.assertIn("caida progresiva", historia.plan_tratamiento)
        self.assertIn("region frontal", historia.plan_tratamiento)
        self.assertIn("alopecia androgenetica", historia.plan_tratamiento)
        self.assertIn("laboratorios", historia.plan_tratamiento)
        self.assertIn("ansiosa", historia.plan_tratamiento)

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
                "historia_enfermedad_actual": historia.historia_enfermedad_actual,
                "signos_vitales": historia.signos_vitales,
                "examen_fisico": historia.examen_fisico,
                "evaluacion_clinica": historia.evaluacion_clinica,
                "diagnostico": historia.diagnostico,
                "analisis_clinico": historia.analisis_clinico,
                "procedimiento": historia.procedimiento,
                "conducta": historia.conducta,
                "plan_tratamiento": "Caida de cabello actualizada\nControl en 30 dias",
                "indicaciones": historia.indicaciones,
                "observaciones": historia.observaciones,
                "notas_privadas_doctor": historia.notas_privadas_doctor,
                "estado": "finalizada",
            },
        )
        self.assertEqual(response.status_code, 302)
        historia.refresh_from_db()
        self.assertEqual(historia.estado, "finalizada")
        self.assertIn("Caida de cabello actualizada", historia.plan_tratamiento)
        self.assertEqual(historia.actualizado_por, self.user)

    def test_medicina_estetica_guarda_formulario_estructurado(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0301",
            primer_nombre="Diana",
            primer_apellido="Reyes",
            nombre="Diana Reyes",
            identidad="0801199600101",
        )
        crear_url = reverse(
            "clinica_crear_historia_especialidad",
            args=[self.empresa.slug, paciente.id, "medicina_estetica"],
        )
        response = self.client.post(
            crear_url,
            {
                "fecha_atencion": "2026-06-17T11:30",
                "motivo_consulta": "Desea mejorar textura facial",
                "antecedentes": "Sin antecedentes",
                "signos_vitales": "",
                "evaluacion_clinica": "",
                "diagnostico": "",
                "procedimiento": "Valoracion inicial",
                "plan_tratamiento": "Plan facial personalizado",
                "indicaciones": "",
                "observaciones": "",
                "estado": "borrador",
                "estetica_motivo": ["arrugas", "manchas_faciales"],
                "estetica_motivo_otros": "Poros dilatados",
                "estetica_objetivo_principal": ["verse_mas_joven", "calidad_piel"],
                "estetica_objetivo_principal_otros": "Mantener un resultado natural",
                "estetica_plan_recomendado": ["toxina", "hydrafacial"],
            },
        )

        self.assertRedirects(
            response,
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id]),
        )
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente)
        self.assertEqual(historia.tipo, "medicina_estetica")
        self.assertEqual(historia.datos_especialidad["estetica_motivo"], ["arrugas", "manchas_faciales"])
        self.assertEqual(historia.datos_especialidad["estetica_motivo_otros"], "Poros dilatados")
        self.assertEqual(
            historia.datos_especialidad["estetica_objetivo_principal"],
            ["verse_mas_joven", "calidad_piel"],
        )
        self.assertEqual(
            historia.datos_especialidad["estetica_objetivo_principal_otros"],
            "Mantener un resultado natural",
        )
        self.assertEqual(historia.datos_especialidad["estetica_plan_recomendado"], ["toxina", "hydrafacial"])

        response = self.client.get(
            reverse("clinica_editar_historia_especialidad", args=[self.empresa.slug, paciente.id, historia.id])
        )
        self.assertContains(response, "Motivo de consulta (puede marcar más de una opción)")
        self.assertContains(response, "Hiperhidrosis (sudoración excesiva)")
        self.assertContains(response, "Rejuvenecimiento íntimo femenino")
        self.assertContains(response, "Objetivo principal del paciente")
        self.assertContains(response, "Mantener resultados previos")

    def test_enfermeria_guarda_bitacora_simple(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0302",
            primer_nombre="Luis",
            primer_apellido="Mora",
            nombre="Luis Mora",
            identidad="0801199000102",
        )
        crear_url = reverse(
            "clinica_crear_historia_especialidad",
            args=[self.empresa.slug, paciente.id, "enfermeria"],
        )
        response = self.client.post(
            crear_url,
            {
                "fecha_atencion": "2026-06-17T12:00",
                "observaciones": "Paciente recibe curacion y queda estable.",
                "estado": "finalizada",
            },
        )

        self.assertRedirects(
            response,
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id]),
        )
        historia = HistoriaClinicaEspecialidad.objects.get(paciente=paciente)
        self.assertEqual(historia.tipo, "enfermeria")
        self.assertEqual(historia.observaciones, "Paciente recibe curacion y queda estable.")

        editar_url = reverse(
            "clinica_editar_historia_especialidad",
            args=[self.empresa.slug, paciente.id, historia.id],
        )
        response = self.client.post(
            editar_url,
            {
                "fecha_atencion": "2026-06-17T12:00",
                "observaciones": "Intento de modificación.",
                "estado": "finalizada",
            },
        )
        self.assertRedirects(
            response,
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id]),
        )
        historia.refresh_from_db()
        self.assertEqual(historia.observaciones, "Paciente recibe curacion y queda estable.")

        response = self.client.get(editar_url)
        self.assertContains(response, "bloqueada permanentemente")
        self.assertNotContains(response, "Guardar historia")

    def test_cada_especialidad_tiene_preconsulta_e_historial_independiente(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0303",
            nombre="Paciente Preconsultas",
            identidad="0801199000103",
        )
        for tipo in dict(HistoriaClinicaEspecialidad.TIPO_CHOICES):
            response = self.client.post(
                reverse(
                    "clinica_generar_enlace_preconsulta_tipo",
                    args=[self.empresa.slug, paciente.id, tipo],
                )
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["preconsulta"].tipo, tipo)

        self.assertEqual(paciente.preconsultas.count(), 6)
        selector = self.client.get(
            reverse("clinica_historias_especialidad", args=[self.empresa.slug, paciente.id])
        )
        for nombre in [
            "Capilar",
            "Cirugia plastica y reconstructiva",
            "Tratamiento Estetico / Piel",
            "Enfermeria",
            "Terapias",
            "Camara hiperbarica",
        ]:
            self.assertContains(selector, nombre)
        self.assertContains(selector, "Escribir en esta área", count=12)

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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tratamiento Estetico / Piel")

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
                "nombres": "Carlos",
                "apellidos": "Diaz",
                "primer_nombre": "Carlos",
                "segundo_nombre": "",
                "primer_apellido": "Diaz",
                "segundo_apellido": "",
                "identidad": "0801199200002",
                "fecha_nacimiento": "1992-05-10",
                "sexo": "masculino",
                "estado_civil": "soltero",
                "correo": "carlos@example.com",
                "telefono_codigo_area": "504",
                "telefono": "99990003",
                "lugar_nacimiento": "Tegucigalpa",
                "ocupacion": "Ingeniero",
                "informante": "yo_mismo",
                "contacto_emergencia": "Ana Diaz",
                "telefono_emergencia": "99990004",
                "referido_por": "facebook",
                "motivo_categoria": ["cirugia_mamaria"],
                "motivo_consulta": "Valoracion",
                "procedimientos_interes": ["aumento_mamario", "braquioplastia"],
                "procedimientos_interes_otros": "No aplica",
                "funciones_organicas": "normal",
                "funciones_detalle": "No aplica",
                "antecedentes_hospitalarios": ["no"],
                "antecedentes_personales": ["no_aplica"],
                "antecedentes_personales_detalle": "No aplica",
                "alergias_seleccion": ["ninguna"],
                "alergias_otras": "No aplica",
                "alergias": "No aplica",
                "medicamentos_habituales": ["no_aplica"],
                "medicamentos_habituales_detalle": "No aplica",
                "medicamentos_actuales_seleccion": ["ninguno"],
                "medicamentos_actuales_otros": "No aplica",
                "antecedentes_infecciosos": "No aplica",
                "antecedentes_hospitalarios_detalle": "No aplica",
                "quirurgicos_operado": ["no"],
                "quirurgicos_detalle": "No aplica",
                "consumo_riesgo": ["ninguno"],
                "consumo_riesgo_detalle": "No aplica",
                "dieta": ["balanceada"],
                "ejercicio": ["ocasional"],
                "antecedentes_familiares": ["no_aplica"],
                "antecedentes_familiares_detalle": "No aplica",
                "riesgo_tromboembolico": ["ninguno"],
                "riesgo_tromboembolico_otros": "No aplica",
                "decision_cirugia": ["usted"],
                "evaluacion_psicologica": ["ninguna"],
                "evaluacion_psicologica_detalle": "No aplica",
                "expectativas_realistas": ["si"],
                "busca_perfeccion": ["no"],
                "multiples_cirugias_insatisfaccion": ["no"],
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
        self.assertContains(response, "Enviar directo por WhatsApp")
        self.assertContains(response, "Abrir WhatsApp manual")

        self.client.logout()
        publica_url = reverse("clinica_preconsulta_publica", args=[token_raw])
        response = self.client.get(publica_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preparemos su consulta")
        self.assertContains(response, "Lea esto antes de empezar")
        self.assertContains(response, "contacte al admin DV Solutions")
        self.assertContains(response, "Laura")
        self.assertContains(response, "Paso 8 de 8")
        self.assertContains(response, "No aplica / no estoy seguro todavia")
        self.assertContains(response, "Braquioplastia (brazos: retirar flacidez o exceso de piel)")

        self.assertContains(response, "Musloplastia (piernas/muslos: retirar flacidez o exceso de piel)")
        self.assertContains(response, "Gluteoplastia (gluteos: mejorar forma o volumen)")
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
                "nombres": "Laura Maria",
                "apellidos": "Perez Lopez",
                "primer_nombre": "Laura Maria",
                "segundo_nombre": "",
                "primer_apellido": "Perez",
                "segundo_apellido": "Lopez",
                "identidad": "0801199600001",
                "fecha_nacimiento": "1996-04-10",
                "sexo": "femenino",
                "estado_civil": "soltero",
                "correo": "laura@example.com",
                "telefono_codigo_area": "504",
                "telefono": "99990001",
                "lugar_nacimiento": "Tegucigalpa",
                "ocupacion": "Administradora",
                "lugar_trabajo": "Empresa privada",
                "redes_sociales": "@laura",
                "informante": "yo_mismo",
                "contacto_emergencia": "Maria Perez",
                "telefono_emergencia": "99990002",
                "referido_por": "instagram",
                "motivo_categoria": ["cirugia_facial"],
                "motivo_consulta": "Valoracion de cirugia facial",
                "procedimientos_interes": ["rinoplastia"],
                "procedimientos_interes_otros": "Revision de cicatriz previa",
                "historia_mejorar": "Perfil facial y densidad capilar",
                "historia_tiempo_preocupacion": "2 anos",
                "historia_tratamientos_previos": "Mesoterapia capilar",
                "historia_expectativas": "Resultado natural",
                "funciones_organicas": "normal",
                "funciones_detalle": "No aplica",
                "revision_sistemas": "normal",
                "revision_sistemas_detalle": "",
                "antecedentes_hospitalarios": ["si"],
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
                "quirurgicos_operado": ["si"],
                "quirurgicos_detalle": "Apendicectomia en 2018",
                "tabaco_frecuencia": ["nunca"],
                "alcohol_frecuencia": ["ocasional"],
                "drogas_recreativas": ["si"],
                "drogas_recreativas_tipos": ["marihuana"],
                "drogas_recreativas_detalle": "Uso ocasional historico",
                "consumo_riesgo": ["no_aplica"],
                "consumo_riesgo_detalle": "No aplica",
                "riesgo_tromboembolico": ["ninguno"],
                "riesgo_tromboembolico_otros": "No aplica",
                "gine_menarca": "12",
                "gine_gestas": "0",
                "gine_partos": "0",
                "gine_cesareas": "0",
                "gine_abortos": "0",
                "gine_ultima_menstruacion": "2026-06-20",
                "gine_embarazada": ["no"],
                "gine_lactancia": ["no"],
                "gine_mamografia": ["no"],
                "decision_cirugia": ["usted"],
                "expectativas_realistas": ["si"],
                "busca_perfeccion": ["no"],
                "multiples_cirugias_insatisfaccion": ["no"],
                "evaluacion_psicologica": ["ninguna"],
                "evaluacion_psicologica_detalle": "No aplica",
                "examen_peso": "64",
                "examen_talla": "165",
                "examen_imc": "23.5",
                "examen_pa": "120/80",
                "examen_fc": "72",
                "examen_sato2": "98",
                "dieta": ["balanceada"],
                "ejercicio": ["3_4_semana"],
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
        self.assertEqual(formulario_general["motivo_categoria"], ["cirugia_facial"])
        self.assertEqual(formulario_general["procedimientos_interes"], ["rinoplastia"])
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

    @patch("clinica.views._actualizar_paciente_desde_preconsulta")
    def test_preconsulta_publica_muestra_soporte_si_ocurre_error_tecnico(self, actualizar_mock):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-ERR",
            primer_nombre="Paciente",
            primer_apellido="Error",
            nombre="Paciente Error",
            identidad="0801199600100",
            whatsapp="99990010",
        )
        token_raw = "token-error-tecnico-preconsulta"
        preconsulta = PreconsultaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="general",
            token_hash=hash_token_preconsulta(token_raw),
            token_preview="token...",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
            creada_por=self.user,
        )
        actualizar_mock.side_effect = RuntimeError("fallo controlado")

        self.client.logout()
        response = self.client.post(
            reverse("clinica_preconsulta_publica", args=[token_raw]),
            self._datos_formulario_general(
                nombres="Paciente",
                apellidos="Error",
                identidad="0801199600100",
                telefono="99990010",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pudimos enviar todavía")
        self.assertContains(response, "contacte al admin DV Solutions")
        preconsulta.refresh_from_db()
        self.assertEqual(preconsulta.estado, "pendiente")

    def test_solo_admin_empresa_puede_eliminar_paciente(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Prueba",
            rtn="0801199900001",
        )
        paciente = Paciente.objects.get(cliente=cliente)
        paciente.expediente_codigo = "HM-DEL"
        paciente.primer_nombre = "Paciente"
        paciente.primer_apellido = "Prueba"
        paciente.save(update_fields=["expediente_codigo", "primer_nombre", "primer_apellido", "fecha_actualizacion"])
        url = reverse("clinica_eliminar_paciente", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url)

        self.assertRedirects(response, reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id]))
        self.assertTrue(Paciente.objects.filter(id=paciente.id).exists())

        self.user.es_administrador_empresa = True
        self.user.save(update_fields=["es_administrador_empresa"])
        response = self.client.post(url)

        self.assertRedirects(response, reverse("clinica_pacientes", args=[self.empresa.slug]))
        paciente.refresh_from_db()
        cliente.refresh_from_db()
        self.assertFalse(paciente.activo)
        self.assertFalse(cliente.activo)

    def test_cliente_inactivo_no_recrea_paciente_compartido_eliminado(self):
        from .services_pacientes import asegurar_paciente_desde_cliente

        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Inactivo",
            rtn="0801199900010",
            activo=False,
        )

        paciente, creado = asegurar_paciente_desde_cliente(cliente)

        self.assertIsNone(paciente)
        self.assertFalse(creado)
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, identidad="0801199900010").exists())

    @patch("clinica.views.enviar_plantilla_preconsulta_whatsapp")
    def test_preconsulta_se_envia_directo_por_whatsapp_api(self, enviar_mock):
        ConfiguracionCRM.objects.create(
            empresa=self.empresa,
            whatsapp_activo=True,
            whatsapp_phone_number_id="123",
            whatsapp_token="token-test",
            whatsapp_plantilla_preconsulta="preconsulta_paciente",
            whatsapp_idioma_preconsulta="es",
        )
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-0401",
            primer_nombre="Laura",
            primer_apellido="Perez",
            nombre="Laura Perez",
            identidad="0801199600002",
            whatsapp="99990002",
        )
        token_raw = "token-preconsulta-directa"
        preconsulta = PreconsultaClinica.objects.create(
            empresa=self.empresa,
            paciente=paciente,
            tipo="general",
            token_hash=hash_token_preconsulta(token_raw),
            token_preview="token...",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
            creada_por=self.user,
        )
        enlace_publico = f"https://dvsolutionshn.com/preconsulta/{token_raw}/"

        response = self.client.post(
            reverse("clinica_enviar_preconsulta_whatsapp", args=[self.empresa.slug, paciente.id, preconsulta.id]),
            {"enlace_publico": enlace_publico},
        )

        self.assertEqual(response.status_code, 200)
        enviar_mock.assert_called_once()
        _, numero = enviar_mock.call_args.args
        self.assertEqual(numero, "99990002")
        self.assertEqual(enviar_mock.call_args.kwargs["paciente"], "Laura Perez")
        self.assertEqual(enviar_mock.call_args.kwargs["tipo_preconsulta"], "General")
        self.assertEqual(enviar_mock.call_args.kwargs["enlace"], enlace_publico)
        self.assertContains(response, "Enviar directo por WhatsApp")
        self.assertContains(response, "Abrir WhatsApp manual")

    def test_control_enlaces_registro_es_privado_para_daniel_varela(self):
        url = reverse("clinica_control_enlaces_registro_paciente", args=[self.empresa.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        dueno = get_user_model().objects.create_user(
            username="dannyvarela25@gmail.com",
            email="dannyvarela25@gmail.com",
            password="pass",
            empresa=self.empresa,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(dueno)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control de enlaces de pacientes")
        self.assertContains(response, "Generar nuevo enlace")

    def test_enlace_paciente_nuevo_registra_apertura_y_avance_sin_guardar_respuestas(self):
        token_raw = "token-seguimiento-registro"
        invitacion = InvitacionRegistroPaciente.objects.create(
            empresa=self.empresa,
            token_hash=hash_token_preconsulta(token_raw),
            token_preview="token-seg...",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
            creada_por=self.user,
        )
        self.client.logout()
        publica_url = reverse("clinica_registro_paciente_publico", args=[token_raw])
        response = self.client.get(publica_url)
        self.assertEqual(response.status_code, 200)
        invitacion.refresh_from_db()
        self.assertIsNotNone(invitacion.fecha_primera_apertura)
        self.assertIsNotNone(invitacion.fecha_ultima_actividad)
        self.assertEqual(invitacion.cantidad_aperturas, 1)
        self.assertEqual(invitacion.paso_maximo, 1)

        actividad_url = reverse("clinica_registro_paciente_actividad", args=[token_raw])
        response = self.client.post(actividad_url, {"paso": 3})
        self.assertEqual(response.status_code, 200)
        invitacion.refresh_from_db()
        self.assertEqual(invitacion.paso_maximo, 3)
        self.assertEqual(invitacion.intentos_envio, 0)
        self.assertIsNone(invitacion.paciente)

        self.client.post(actividad_url, {"paso": 1})
        invitacion.refresh_from_db()
        self.assertEqual(invitacion.paso_maximo, 3)

    def test_enlace_paciente_nuevo_distingue_intento_detenido_por_validacion(self):
        token_raw = "token-validacion-registro"
        invitacion = InvitacionRegistroPaciente.objects.create(
            empresa=self.empresa,
            token_hash=hash_token_preconsulta(token_raw),
            token_preview="token-val...",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
            creada_por=self.user,
        )
        self.client.logout()
        response = self.client.post(
            reverse("clinica_registro_paciente_publico", args=[token_raw]),
            {},
        )
        self.assertEqual(response.status_code, 200)
        invitacion.refresh_from_db()
        self.assertEqual(invitacion.intentos_envio, 1)
        self.assertEqual(invitacion.ultimo_resultado, "validacion")
        self.assertIsNotNone(invitacion.fecha_ultimo_intento)
        self.assertIsNone(invitacion.paciente)

    def test_enlace_paciente_nuevo_crea_expediente_cliente_preconsulta_y_foto(self):
        response = self.client.get(reverse("clinica_pacientes", args=[self.empresa.slug]))
        self.assertContains(response, "Enlace para paciente nuevo")

        response = self.client.post(
            reverse("clinica_generar_enlace_registro_paciente", args=[self.empresa.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compartir por WhatsApp")
        enlace = response.context["enlace_publico"]
        token_raw = enlace.rstrip("/").rsplit("/", 1)[-1]
        invitacion = InvitacionRegistroPaciente.objects.get()
        self.assertEqual(invitacion.token_hash, hash_token_preconsulta(token_raw))
        self.assertNotEqual(invitacion.token_hash, token_raw)

        self.client.logout()
        publica_url = reverse("clinica_registro_paciente_publico", args=[token_raw])
        response = self.client.get(publica_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lea esto antes de empezar")
        self.assertContains(response, "contacte al admin DV Solutions")
        self.assertContains(response, "Abrir cámara")
        self.assertContains(response, "Subir archivo")
        self.assertContains(response, 'enctype="multipart/form-data"', html=False)

        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), color=(24, 130, 160)).save(image_buffer, format="JPEG")
        foto = SimpleUploadedFile("paciente.jpg", image_buffer.getvalue(), content_type="image/jpeg")
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                publica_url,
                {
                    "nombres": "Ana María",
                    "apellidos": "López Rivera",
                    "identidad": "0801199900012",
                    "fecha_nacimiento": "1999-08-12",
                    "sexo": "femenino",
                    "estado_civil": "soltero",
                    "correo": "ana@example.com",
                    "telefono_codigo_area": "504",
                    "telefono": "99998888",
                    "informante": "yo_mismo",
                    "referido_por": "facebook",
                    "motivo_categoria": ["medicina_estetica"],
                    "procedimientos_interes": ["rejuvenecimiento_facial"],
                    "procedimientos_interes_otros": "No aplica",
                    "funciones_organicas": "normal",
                    "funciones_detalle": "No aplica",
                    "antecedentes_personales": ["no_aplica"],
                    "antecedentes_personales_detalle": "No aplica",
                    "alergias_seleccion": ["ninguna"],
                    "alergias_otras": "No aplica",
                    "alergias": "No aplica",
                    "medicamentos_habituales": ["no_aplica"],
                    "medicamentos_habituales_detalle": "No aplica",
                    "medicamentos_actuales_seleccion": ["ninguno"],
                    "medicamentos_actuales_otros": "No aplica",
                    "antecedentes_infecciosos": "No aplica",
                    "antecedentes_hospitalarios": ["no"],
                    "antecedentes_hospitalarios_detalle": "No aplica",
                    "quirurgicos_operado": ["no"],
                    "quirurgicos_detalle": "No aplica",
                    "consumo_riesgo": ["ninguno"],
                    "consumo_riesgo_detalle": "No aplica",
                    "dieta": ["balanceada"],
                    "ejercicio": ["ocasional"],
                    "antecedentes_familiares": ["no_aplica"],
                    "antecedentes_familiares_detalle": "No aplica",
                    "riesgo_tromboembolico": ["ninguno"],
                    "riesgo_tromboembolico_otros": "No aplica",
                    "evaluacion_psicologica": ["ninguna"],
                    "evaluacion_psicologica_detalle": "No aplica",
                    "expectativas_realistas": ["si"],
                    "busca_perfeccion": ["no"],
                    "multiples_cirugias_insatisfaccion": ["no"],
                    "motivo_consulta": "Valoración estética",
                    "consentimiento_datos": "on",
                    "foto_perfil": foto,
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Expediente creado")
            paciente = Paciente.objects.get(identidad="0801199900012")
            self.assertEqual(paciente.nombre, "Ana María López Rivera")
            self.assertTrue(bool(paciente.foto_perfil))
            self.assertIsNotNone(paciente.cliente)
            self.assertEqual(paciente.cliente.rtn, paciente.identidad)
            self.assertTrue(PacienteFotoEvolucion.objects.filter(paciente=paciente, tipo="ingreso").exists())
            preconsulta = PreconsultaClinica.objects.get(paciente=paciente)
            self.assertEqual(preconsulta.estado, "completada")
            invitacion.refresh_from_db()
            self.assertEqual(invitacion.estado, "pendiente")
            self.assertEqual(invitacion.paciente, paciente)
            self.assertIsNone(invitacion.preconsulta)
            self.assertEqual(invitacion.ultimo_resultado, "completado")
            self.assertEqual(invitacion.paso_maximo, 3)

        response = self.client.get(publica_url)
        self.assertContains(response, "Formulario de historia clinica")

        response = self.client.post(
            publica_url,
            {
                "nombres": "Elvin Francisco",
                "apellidos": "Romero",
                "identidad": "0801199900099",
                "fecha_nacimiento": "1990-01-15",
                "sexo": "masculino",
                "estado_civil": "soltero",
                "telefono_codigo_area": "504",
                "telefono": "99997777",
                "informante": "yo_mismo",
                "referido_por": "facebook",
                "motivo_categoria": ["capilar"],
                "procedimientos_interes": ["evaluacion_alopecia"],
                "procedimientos_interes_otros": "No aplica",
                "funciones_organicas": "normal",
                "funciones_detalle": "No aplica",
                "antecedentes_personales": ["no_aplica"],
                "antecedentes_personales_detalle": "No aplica",
                "alergias_seleccion": ["ninguna"],
                "alergias_otras": "No aplica",
                "alergias": "No aplica",
                "medicamentos_habituales": ["no_aplica"],
                "medicamentos_habituales_detalle": "No aplica",
                "medicamentos_actuales_seleccion": ["ninguno"],
                "medicamentos_actuales_otros": "No aplica",
                "antecedentes_infecciosos": "No aplica",
                "antecedentes_hospitalarios": ["no"],
                "antecedentes_hospitalarios_detalle": "No aplica",
                "quirurgicos_operado": ["no"],
                "quirurgicos_detalle": "No aplica",
                "consumo_riesgo": ["ninguno"],
                "consumo_riesgo_detalle": "No aplica",
                "dieta": ["balanceada"],
                "ejercicio": ["ocasional"],
                "antecedentes_familiares": ["no_aplica"],
                "antecedentes_familiares_detalle": "No aplica",
                "riesgo_tromboembolico": ["ninguno"],
                "riesgo_tromboembolico_otros": "No aplica",
                "evaluacion_psicologica": ["ninguna"],
                "evaluacion_psicologica_detalle": "No aplica",
                "expectativas_realistas": ["si"],
                "busca_perfeccion": ["no"],
                "multiples_cirugias_insatisfaccion": ["no"],
                "motivo_consulta": "Registro nuevo desde el mismo enlace",
                "consentimiento_datos": "on",
                "foto_perfil": self._foto_prueba("segundo.jpg"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expediente creado")
        segundo_paciente = Paciente.objects.get(identidad="0801199900099")
        self.assertEqual(segundo_paciente.nombre, "Elvin Francisco Romero")
        self.assertIsNotNone(segundo_paciente.cliente)
        self.assertEqual(PreconsultaClinica.objects.filter(paciente=segundo_paciente, estado="completada").count(), 1)

    def test_enlace_paciente_nuevo_crea_expediente_sin_foto_y_con_secciones_clinicas_omitidas(self):
        response = self.client.post(
            reverse("clinica_generar_enlace_registro_paciente", args=[self.empresa.slug])
        )
        enlace = response.context["enlace_publico"]
        token_raw = enlace.rstrip("/").rsplit("/", 1)[-1]

        self.client.logout()
        response = self.client.post(
            reverse("clinica_registro_paciente_publico", args=[token_raw]),
            {
                "nombres": "Paciente",
                "apellidos": "Sin Foto",
                "identidad": "0801199900199",
                "fecha_nacimiento": "1995-05-20",
                "sexo": "masculino",
                "estado_civil": "soltero",
                "telefono_codigo_area": "504",
                "telefono": "99996666",
                "informante": "yo_mismo",
                "referido_por": "no_aplica",
                "motivo_categoria": ["no_aplica"],
                "consentimiento_datos": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expediente creado")
        paciente = Paciente.objects.get(identidad="0801199900199")
        self.assertFalse(bool(paciente.foto_perfil))
        preconsulta = PreconsultaClinica.objects.get(paciente=paciente)
        self.assertTrue(preconsulta.datos_generales["formulario_general_pendiente_doctor"])
        self.assertEqual(preconsulta.datos_generales["formulario_general"]["pendiente_doctor_desde_paso"], 4)
        self.assertEqual(preconsulta.datos_generales["formulario_general"]["motivo_categoria"], ["no_aplica"])
        self.assertIn("No aplica", paciente.antecedentes_medicos)
        self.assertEqual(paciente.alergias, "No aplica")
