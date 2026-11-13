from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from crm.models import CitaCirugiaFoto, CitaCliente

from .models import ConsentimientoClinico, DocumentoClinicoPaciente, ExamenPaciente, Paciente, PacienteFotoEvolucion


class MediaClinicaPrivadaTests(TestCase):
    CONTENIDO = b"archivo clinico sintetico 0123456789"

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica médica"})
        cls.empresa = Empresa.objects.create(nombre="Hospital de prueba", slug="hospital_mia", rtn="QA-MEDIA-1", tipo_solucion="clinica")
        cls.otra = Empresa.objects.create(nombre="Otra clínica de prueba", slug="medical_spa", rtn="QA-MEDIA-2", tipo_solucion="clinica")
        for empresa in (cls.empresa, cls.otra):
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})
        cls.paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Paciente de prueba", expediente_codigo="QA-1")
        cls.otro_paciente = Paciente.objects.create(empresa=cls.otra, nombre="Otro paciente", expediente_codigo="QA-2")
        cls.rol = RolSistema.objects.create(
            nombre="Permisos de archivos clínicos", codigo="qa-media-privada",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, puede_ver_pacientes=True,
            puede_ver_anexos_clinicos=True, puede_descargar_anexos_clinicos=True,
        )
        cls.usuario = Usuario.objects.create_user(username="archivos-protegidos", empresa=cls.empresa, rol_sistema=cls.rol)

    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.archivos = self.crear_archivos()

    def archivo(self, nombre):
        return SimpleUploadedFile(nombre, self.CONTENIDO)

    def crear_archivos(self):
        datos = {"empresa": self.empresa, "paciente": self.paciente}
        evolucion = PacienteFotoEvolucion.objects.create(
            **datos, imagen=self.archivo("evolucion.jpg"), video=self.archivo("evolucion.mp4"),
        )
        examen = ExamenPaciente.objects.create(**datos, titulo="Examen de prueba", archivo=self.archivo("examen.jpg"))
        consentimiento = ConsentimientoClinico.objects.create(
            **datos, titulo="Consentimiento de prueba", contenido="Contenido de prueba", archivo=self.archivo("consentimiento.pdf"),
        )
        documento = DocumentoClinicoPaciente.objects.create(
            **datos, categoria="documento", titulo="Documento de prueba", archivo=self.archivo("documento.jpg"),
        )
        cita = CitaCliente.objects.create(
            **datos, titulo="Cita de prueba", fecha_hora=timezone.now(),
        )
        self.cirugia = CitaCirugiaFoto.objects.create(empresa=self.empresa, cita=cita, imagen=self.archivo("cirugia.jpg"))
        PacienteFotoEvolucion.objects.create(**datos, imagen=self.cirugia.imagen.name)
        return (evolucion.imagen, evolucion.video, examen.archivo, consentimiento.archivo, documento.archivo, self.cirugia.imagen)

    def test_url_antigua_exige_autorizacion_y_conserva_la_respuesta_http(self):
        for archivo in self.archivos:
            self.assertEqual(self.client.get(archivo.url).status_code, 404)

        self.client.force_login(self.usuario)
        for archivo in self.archivos:
            with self.subTest(archivo=archivo.name):
                respuesta = self.client.get(archivo.url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(b"".join(respuesta.streaming_content), self.CONTENIDO)
                self.assertIn("no-store", respuesta.headers["Cache-Control"])
                self.assertEqual(self.client.head(archivo.url).headers["Content-Length"], str(len(self.CONTENIDO)))
                rango = self.client.get(archivo.url, HTTP_RANGE="bytes=0-6")
                self.assertEqual(rango.status_code, 206)
                self.assertEqual(b"".join(rango.streaming_content), self.CONTENIDO[:7])

        for lectura, descarga in ((True, False), (False, True)):
            RolSistema.objects.filter(pk=self.rol.pk).update(
                puede_ver_anexos_clinicos=lectura, puede_descargar_anexos_clinicos=descarga,
            )
            for archivo in self.archivos:
                self.assertEqual(self.client.get(archivo.url).status_code, 404)

    def test_empresa_ajena_y_referencias_inconsistentes_quedan_bloqueadas(self):
        ajeno = ExamenPaciente.objects.create(
            empresa=self.otra, paciente=self.otro_paciente, titulo="Archivo ajeno", archivo=self.archivo("examen-ajeno.jpg"),
        )
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(ajeno.archivo.url).status_code, 404)

        duplicado = PacienteFotoEvolucion.objects.create(
            empresa=self.otra, paciente=self.otro_paciente, imagen=self.cirugia.imagen.name,
        )
        self.assertEqual(self.client.get(self.cirugia.imagen.url).status_code, 404)

        duplicado.delete()
        CitaCliente.objects.filter(pk=self.cirugia.cita_id).update(paciente=self.otro_paciente)
        self.assertEqual(self.client.get(self.cirugia.imagen.url).status_code, 404)

    def test_conserva_autorizacion_legada_y_administracion_dentro_de_su_empresa(self):
        rol_legado = RolSistema.objects.create(
            nombre="Archivos clínicos legados", codigo="qa-media-privada-legada",
            puede_pacientes=True, puede_expediente_clinico=True,
        )
        for numero, perfil in enumerate(({"rol_sistema": rol_legado}, {"es_administrador_empresa": True}), 1):
            with self.subTest(perfil=perfil):
                usuario = Usuario.objects.create_user(
                    username=f"archivo-legado-{numero}", empresa=self.empresa, **perfil,
                )
                self.client.force_login(usuario)
                for archivo in self.archivos:
                    respuesta = self.client.get(archivo.url)
                    self.assertEqual(respuesta.status_code, 200)
                    respuesta.close()
