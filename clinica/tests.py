from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema
from crm.models import CitaCliente, ConfiguracionCRM
from facturacion.models import Cliente, Producto
from .forms import PreconsultaClinicaPublicaForm
from .models import CitaClinica, ConsentimientoClinico, DocumentoClinicoPaciente, ExamenPaciente, HistoriaClinicaEspecialidad, InvitacionRegistroPaciente, Paciente, PacienteFotoEvolucion, PreconsultaClinica, ProfesionalSalud, RecetaMedica, ServicioClinico
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
        self.assertContains(response, "Plan de consentimiento")
        self.assertContains(response, "patient-evolution-carousel")
        self.assertContains(response, "patientPhotoModal")

    @patch("clinica.views._sincronizar_cliente_facturacion_paciente")
    def test_crear_paciente_muestra_error_si_falla_sincronizacion_cliente(self, sincronizar):
        sincronizar.side_effect = ValidationError({"rtn": "Ya existe un cliente con este RTN en la empresa."})

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            {
                "expediente_codigo": "HM-ERROR-SYNC",
                "tipo_id": "cc",
                "identidad": "0801199912350",
                "primer_nombre": "Paciente",
                "primer_apellido": "ConError",
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
                "activo": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se guardo el paciente")
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, expediente_codigo="HM-ERROR-SYNC").exists())

    def test_crear_paciente_rechaza_foto_inicial_mayor_a_5_mb(self):
        image_buffer = BytesIO()
        Image.effect_noise((3200, 3200), 100).convert("RGB").save(image_buffer, format="JPEG", quality=95)
        self.assertGreater(len(image_buffer.getvalue()), 5 * 1024 * 1024)
        foto_grande = SimpleUploadedFile("grande.jpg", image_buffer.getvalue(), content_type="image/jpeg")

        response = self.client.post(
            reverse("clinica_crear_paciente", args=[self.empresa.slug]),
            {
                "expediente_codigo": "HM-FOTO-GRANDE",
                "tipo_id": "cc",
                "identidad": "0801199912351",
                "primer_nombre": "Foto",
                "primer_apellido": "Grande",
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
                "activo": "on",
                "foto_perfil": foto_grande,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La foto inicial no puede superar 5 MB")
        self.assertFalse(Paciente.objects.filter(empresa=self.empresa, expediente_codigo="HM-FOTO-GRANDE").exists())

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
            {
                "expediente_codigo": "MIA-00118",
                "tipo_id": "cc",
                "identidad": "0801199912352",
                "primer_nombre": "Codigo",
                "primer_apellido": "Nuevo",
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
                "activo": "on",
            },
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
        self.assertContains(selector, "Nueva preconsulta", count=6)

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

    def test_solo_admin_empresa_puede_eliminar_paciente(self):
        paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="HM-DEL",
            primer_nombre="Paciente",
            primer_apellido="Prueba",
            nombre="Paciente Prueba",
            identidad="0801199900001",
        )
        url = reverse("clinica_eliminar_paciente", args=[self.empresa.slug, paciente.id])

        response = self.client.post(url)

        self.assertRedirects(response, reverse("clinica_paciente_detalle", args=[self.empresa.slug, paciente.id]))
        self.assertTrue(Paciente.objects.filter(id=paciente.id).exists())

        self.user.es_administrador_empresa = True
        self.user.save(update_fields=["es_administrador_empresa"])
        response = self.client.post(url)

        self.assertRedirects(response, reverse("clinica_pacientes", args=[self.empresa.slug]))
        self.assertFalse(Paciente.objects.filter(id=paciente.id).exists())

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
                    "motivo_categoria": "medicina_estetica",
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

        response = self.client.get(publica_url)
        self.assertContains(response, "Formulario general del paciente")

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
                "motivo_categoria": "capilar",
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
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expediente creado")
        segundo_paciente = Paciente.objects.get(identidad="0801199900099")
        self.assertEqual(segundo_paciente.nombre, "Elvin Francisco Romero")
        self.assertIsNotNone(segundo_paciente.cliente)
        self.assertEqual(PreconsultaClinica.objects.filter(paciente=segundo_paciente, estado="completada").count(), 1)
