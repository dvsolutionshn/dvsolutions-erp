from datetime import timedelta
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import (
    ProgramaCamaraHiperbarica,
    ProgramaTerapiaPostQuirurgica,
    SesionCamaraHiperbarica,
    SesionTerapiaPostQuirurgica,
)

from .models import (
    DocumentoClinicoPaciente, ExamenPaciente, HistoriaClinicaEspecialidad, Paciente,
    PlanTratamientoPaciente, PreconsultaClinica,
)


class ExpedienteLimitadoTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    PLANES = "puede_ver_planes_tratamiento"
    ANEXOS = "puede_ver_anexos_clinicos"
    PERMISOS_SECCION = {
        "terapias": "puede_ver_terapias",
        "camara_hiperbarica": "puede_ver_camara_hiperbarica",
        "terapias_postquirurgicas": "puede_ver_postquirurgicas",
    }
    AREAS = (PLANES, ANEXOS, *PERMISOS_SECCION.values())
    EXPEDIENTE = "clinica_paciente_expediente"
    SECCION = "clinica_paciente_expediente_seccion"

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas, cls.pacientes = {}, {}
        for numero, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Clínica de prueba {numero}", slug=slug, rtn=f"EXP-QA-{numero}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            cls.empresas[slug] = empresa
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente sintético {numero}", foto_perfil=f"clinica/pacientes/perfil/prueba-{numero}.jpg",
                identidad=f"080119995510{numero}", telefono=f"5049999510{numero}", whatsapp=f"5047777510{numero}",
                expediente_codigo=f"EXP-PRIVADO-{numero}", correo=f"expediente-{numero}@example.test",
                direccion=f"Dirección privada {numero}", fecha_nacimiento=timezone.localdate().replace(year=1990, day=15),
                es_alergico=True, no_agendar=True, motivo_no_agendar=f"Motivo privado {numero}",
            )
        cls.empresa = cls.empresas[cls.SLUGS[0]]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.roles = {
            version: RolSistema.objects.create(
                nombre=f"Enfermería limitada v{version}", codigo=f"expediente-limitado-v{version}",
                es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
                usa_permisos_acciones_v3=version == 3, puede_ver_pacientes=True,
            ) for version in (2, 3)
        }
        cls.usuario = Usuario.objects.create_user(username="enfermeria-expediente-limitado", empresa=cls.empresa, rol_sistema=cls.roles[2])
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        for slug, empresa in cls.empresas.items():
            version = 2 if slug in cls.SLUGS[:2] else 3
            UsuarioEmpresaPermiso.objects.create(usuario=cls.usuario, empresa=empresa, rol_sistema=cls.roles[version])
        cls.preconsulta = PreconsultaClinica.objects.create(
            empresa=cls.empresa, paciente=cls.paciente, token_hash="expediente-limitado-prueba",
            fecha_expiracion=timezone.now() + timedelta(days=1), estado="completada",
            motivo_consulta="Motivo reservado de la preconsulta",
        )
        PlanTratamientoPaciente.objects.create(empresa=cls.empresa, paciente=cls.paciente, texto="Plan de prueba ya guardado")
        ExamenPaciente.objects.create(empresa=cls.empresa, paciente=cls.paciente, titulo="Examen de prueba ya guardado", archivo="clinica/examenes/prueba.pdf")

    def setUp(self):
        self.client.force_login(self.usuario)
        media = TemporaryDirectory(prefix="expediente-limitado-")
        self.addCleanup(media.cleanup)
        configuracion = override_settings(MEDIA_ROOT=media.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)

    def conceder_areas(self, *permisos):
        RolSistema.objects.filter(pk__in=[rol.pk for rol in self.roles.values()]).update(
            **{permiso: permiso in permisos for permiso in self.AREAS},
        )

    def url(self, ruta, *args, app=False, empresa=None, paciente=None):
        empresa = empresa or self.empresa
        argumentos = [empresa.slug]
        if paciente is not None:
            argumentos.append(paciente.pk)
        return reverse(ruta, args=[*argumentos, *args]) + ("?app=1" if app else "")

    def url_paciente(self, ruta, *args, app=False):
        return self.url(ruta, *args, app=app, paciente=self.paciente)

    def assert_datos_privados_ausentes(self, response, paciente):
        for dato in (
            paciente.identidad, paciente.telefono, paciente.whatsapp, paciente.expediente_codigo,
            paciente.correo, paciente.direccion, paciente.motivo_no_agendar,
        ):
            self.assertNotContains(response, dato)

    def assert_denegado(self, response, *, metodo=False):
        self.assertIn(response.status_code, (302, 403, 405) if metodo else (302, 403))

    def test_directorio_y_sugerencias_limitan_datos_y_busqueda_por_nombre(self):
        self.conceder_areas(self.PLANES)
        for slug, empresa in self.empresas.items():
            paciente = self.pacientes[slug]
            for app in (False, True):
                with self.subTest(clinica=slug, app=app):
                    listado = self.url("clinica_pacientes", empresa=empresa)
                    sugerencias = self.url("clinica_pacientes_sugerencias", empresa=empresa)
                    expediente = self.url(self.EXPEDIENTE, app=app, empresa=empresa, paciente=paciente)
                    detalle = self.url("clinica_paciente_detalle", app=app, empresa=empresa, paciente=paciente)
                    parametros = {"app": "1"} if app else {}
                    respuesta = self.client.get(listado, {**parametros, "q": paciente.nombre})
                    self.assertContains(respuesta, paciente.nombre)
                    self.assertContains(respuesta, paciente.foto_perfil.url)
                    self.assertContains(respuesta, f'href="{expediente}"')
                    self.assertNotContains(respuesta, f'href="{detalle}"')
                    self.assert_datos_privados_ausentes(respuesta, paciente)
                    self.assertNotContains(respuesta, f"{paciente.edad} años")
                    self.assertNotContains(respuesta, 'data-birthday="true"')
                    self.assertNotContains(respuesta, 'data-restricted="true"')

                    respuesta = self.client.get(sugerencias, {**parametros, "q": paciente.nombre})
                    self.assert_datos_privados_ausentes(respuesta, paciente)
                    resultado, = respuesta.json()["results"]
                    self.assertEqual(resultado["nombre"], paciente.nombre)
                    self.assertFalse(resultado.get("alergico"))
                    if resultado.get("url"):
                        url = urlsplit(resultado["url"])
                        destino = urlsplit(expediente)
                        self.assertEqual((url.path, url.query), (destino.path, destino.query))

                    for consulta in (paciente.identidad, paciente.telefono, paciente.whatsapp, paciente.expediente_codigo, paciente.correo):
                        with self.subTest(consulta=consulta):
                            busqueda = {**parametros, "q": consulta}
                            self.assertNotContains(self.client.get(listado, busqueda), paciente.nombre)
                            self.assertEqual(self.client.get(sugerencias, busqueda).json()["results"], [])

    def test_expediente_muestra_solo_el_area_concedida_y_admite_head(self):
        enlaces = {
            self.PLANES: ("clinica_planes_tratamiento_paciente",),
            self.ANEXOS: ("clinica_examenes_paciente",),
            **{permiso: (self.SECCION, tipo) for tipo, permiso in self.PERMISOS_SECCION.items()},
        }
        for app in (False, True):
            expediente = self.url_paciente(self.EXPEDIENTE, app=app)
            for permiso in self.AREAS:
                with self.subTest(app=app, permiso=permiso):
                    self.conceder_areas(permiso)
                    respuesta = self.client.get(expediente)
                    self.assertContains(respuesta, self.paciente.nombre)
                    self.assertContains(respuesta, self.paciente.foto_perfil.url)
                    self.assert_datos_privados_ausentes(respuesta, self.paciente)
                    for area, destino in enlaces.items():
                        enlace = f'href="{self.url_paciente(*destino, app=app)}"'
                        if area == permiso:
                            self.assertContains(respuesta, enlace)
                        else:
                            self.assertNotContains(respuesta, enlace)
                    self.assertEqual(self.client.head(expediente).status_code, 200)
                    self.assert_denegado(self.client.post(expediente), metodo=True)

        self.conceder_areas()
        for app in (False, True):
            expediente = self.url_paciente(self.EXPEDIENTE, app=app)
            self.assertNotContains(self.client.get(self.url("clinica_pacientes", app=app)), f'href="{expediente}"')
            self.assert_denegado(self.client.get(expediente))

    def test_secciones_de_terapia_aislan_registros_permisos_y_empresa(self):
        datos = {"empresa": self.empresa, "paciente": self.paciente}
        notas = {
            "terapias": "Nota de prueba de fisioterapia",
            "camara_hiperbarica": "Nota de prueba de oxigenoterapia",
            "terapias_postquirurgicas": "Nota de prueba de rehabilitación postoperatoria",
        }
        HistoriaClinicaEspecialidad.objects.create(
            **datos, tipo="terapias", observaciones=notas["terapias"], estado="finalizada",
            notas_privadas_doctor="Nota reservada del doctor",
        )
        HistoriaClinicaEspecialidad.objects.create(**datos, tipo="enfermeria", observaciones="Nota privada de otra especialidad")
        programa_camara = ProgramaCamaraHiperbarica.objects.create(**datos)
        SesionCamaraHiperbarica.objects.create(**datos, programa=programa_camara, numero_sesion=1, nota_enfermeria=notas["camara_hiperbarica"], estado="finalizada")
        programa_post = ProgramaTerapiaPostQuirurgica.objects.create(**datos)
        SesionTerapiaPostQuirurgica.objects.create(**datos, programa=programa_post, numero_sesion=1, nota_enfermeria=notas["terapias_postquirurgicas"], estado="finalizada")

        for tipo, permiso in self.PERMISOS_SECCION.items():
            self.conceder_areas(permiso)
            for app in (False, True):
                with self.subTest(tipo=tipo, app=app):
                    url = self.url_paciente(self.SECCION, tipo, app=app)
                    respuesta = self.client.get(url)
                    self.assertContains(respuesta, notas[tipo])
                    self.assertNotContains(respuesta, "Nota privada de otra especialidad")
                    self.assertNotContains(respuesta, "Nota reservada del doctor")
                    self.assert_datos_privados_ausentes(respuesta, self.paciente)
                    self.assert_denegado(self.client.post(url), metodo=True)
                    paciente_ajeno = self.pacientes[self.SLUGS[1]]
                    self.assertEqual(self.client.get(self.url(self.SECCION, tipo, app=app, paciente=paciente_ajeno)).status_code, 404)
                    for otro_tipo in self.PERMISOS_SECCION.keys() - {tipo}:
                        self.assertNotContains(respuesta, notas[otro_tipo])
                        self.assert_denegado(self.client.get(self.url_paciente(self.SECCION, otro_tipo, app=app)))

    def test_permiso_de_escritura_no_autoriza_lectura_de_terapias(self):
        RolSistema.objects.filter(pk=self.roles[2].pk).update(puede_escribir_terapias=True)
        for app in (False, True):
            with self.subTest(app=app):
                expediente = self.url_paciente(self.EXPEDIENTE, app=app)
                lectura = self.url_paciente(self.SECCION, "terapias", app=app)
                registro = self.url_paciente("clinica_crear_historia_especialidad", "terapias", app=app)
                respuesta = self.client.get(expediente)
                self.assertContains(respuesta, f'href="{registro}"')
                self.assertNotContains(respuesta, f'href="{lectura}"')
                self.assert_denegado(self.client.get(lectura))

    def test_permisos_de_area_no_abren_datos_generales_ni_historia_general(self):
        self.conceder_areas(*self.AREAS)
        for app in (False, True):
            for ruta, args in (
                ("clinica_paciente_detalle", ()),
                ("clinica_historial_clinico_consolidado", ()),
                ("clinica_preconsulta_detalle", (self.preconsulta.pk,)),
            ):
                with self.subTest(app=app, ruta=ruta):
                    self.assert_denegado(self.client.get(self.url_paciente(ruta, *args, app=app)))

    def test_plan_y_anexos_se_consultan_sin_permiso_para_agregarlos(self):
        self.conceder_areas(self.PLANES, self.ANEXOS)
        for app in (False, True):
            with self.subTest(app=app):
                planes = self.url_paciente("clinica_planes_tratamiento_paciente", app=app)
                examenes = self.url_paciente("clinica_examenes_paciente", app=app)
                subir_examen = self.url_paciente("clinica_subir_examen_paciente", app=app)
                self.assertContains(self.client.get(planes), "Plan de prueba ya guardado")
                self.assertContains(self.client.get(examenes), "Examen de prueba ya guardado")
                self.assert_denegado(self.client.post(planes, {"texto": "Plan sin autorización"}))
                self.assert_denegado(self.client.get(subir_examen))
                self.assert_denegado(self.client.post(subir_examen, {
                    "titulo": "Examen sin autorización", "tipo": "laboratorio",
                    "fecha_examen": timezone.localdate().isoformat(),
                    "archivo": SimpleUploadedFile("examen.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"),
                }))
        self.assertEqual(PlanTratamientoPaciente.objects.filter(paciente=self.paciente).count(), 1)
        self.assertEqual(ExamenPaciente.objects.filter(paciente=self.paciente).count(), 1)

    def test_anexos_limitados_protegen_archivos_y_certificado(self):
        self.conceder_areas(self.ANEXOS)
        examen = ExamenPaciente.objects.get(empresa=self.empresa, paciente=self.paciente)
        contenido = b"%PDF-1.4\n%%EOF"
        examen.archivo.save("anexo-protegido.pdf", SimpleUploadedFile("anexo-protegido.pdf", contenido))
        incapacidad = DocumentoClinicoPaciente.objects.create(
            empresa=self.empresa, paciente=self.paciente, categoria="incapacidad", titulo="Incapacidad de prueba",
        )
        for app in (False, True):
            with self.subTest(app=app, descargar=False):
                lista = self.url_paciente("clinica_examenes_paciente", app=app)
                archivo = self.url_paciente("clinica_archivo_anexo_app", "examen", examen.pk, "archivo", app=app)
                certificado = self.url_paciente("clinica_incapacidad_imprimir", incapacidad.pk, app=app)
                respuesta = self.client.get(lista)
                self.assertNotContains(respuesta, examen.archivo.url)
                self.assertNotContains(respuesta, archivo)
                self.assert_denegado(self.client.get(archivo))
                self.assert_denegado(self.client.get(certificado))

        RolSistema.objects.filter(pk=self.roles[2].pk).update(puede_descargar_anexos_clinicos=True)
        for app in (False, True):
            with self.subTest(app=app, descargar=True):
                lista = self.url_paciente("clinica_examenes_paciente", app=app)
                archivo = self.url_paciente("clinica_archivo_anexo_app", "examen", examen.pk, "archivo", app=app)
                respuesta = self.client.get(lista)
                self.assertContains(respuesta, archivo)
                self.assertNotContains(respuesta, examen.archivo.url)
                self.assert_datos_privados_ausentes(respuesta, self.paciente)
                descarga = self.client.get(archivo)
                self.assertEqual(descarga.status_code, 200)
                self.assertIn("no-store", descarga["Cache-Control"])
                self.assertEqual(b"".join(descarga.streaming_content), contenido)
