"""Las respuestas recibidas se leen dentro de su sección del expediente."""

from copy import deepcopy
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from .models import CitaClinica, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica
from . import test_preconsulta_paciente_existente as datos_publicos


class _PanelesRecibidos(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.pila = []
        self.paneles = {}
        self.enlaces = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.enlaces.append(attrs["href"])
        clave = attrs.get("data-respuestas-seccion")
        if clave:
            self.paneles.setdefault(clave, []).append({"texto": [], "registros": []})
        panel = self._panel(clave)
        if panel is not None and attrs.get("data-preconsulta-id"):
            plegable = attrs if tag == "details" else next((datos for nombre, datos in reversed(self.pila) if nombre == "details"), None)
            panel["registros"].append({
                "id": int(attrs["data-preconsulta-id"]),
                "abierto": "open" in plegable if plegable is not None else None,
            })
        if tag not in self.VOID:
            self.pila.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for indice in range(len(self.pila) - 1, -1, -1):
            if self.pila[indice][0] == tag:
                del self.pila[indice:]
                break

    def _panel(self, nueva=None):
        clave = nueva or next((attrs["data-respuestas-seccion"] for _tag, attrs in reversed(self.pila) if "data-respuestas-seccion" in attrs), None)
        return self.paneles[clave][-1] if clave else None

    def handle_data(self, data):
        panel = self._panel()
        if panel is not None:
            panel["texto"].append(data)

    def texto(self, clave):
        return " ".join(" ".join(self.paneles[clave][0]["texto"]).split())


class HistorialRespuestasSeccionesTests(TestCase):
    SECCIONES = ("motivo_consulta", "funciones_organicas", "examen_fisico", "antecedentes", "datos_generales")
    respuestas_base = datos_publicos.FormularioHistoriaPacienteExistenteTests.respuestas

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.doctora = Usuario.objects.create_user(username="historial-por-seccion-doctora", es_administrador_empresa=True)
        cls.casos = []
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"SECCION-{indice}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo)
            cls.doctora.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente maestro {indice}", expediente_codigo=f"SEC-{indice}",
                sexo="femenino", identidad=f"08011999550{indice:02d}", telefono=f"998855{indice:02d}",
                alergias=f"Alergia maestra conservada {indice}", ocupacion="Ocupación maestra conservada",
            )
            cls.casos.append((empresa, paciente))

    def setUp(self):
        self.client.force_login(self.doctora)

    def publicar(self, empresa, paciente, version):
        marca = f"{empresa.slug} {version}"
        datos = self.respuestas_base()
        campos = {
            "motivo_consulta": f"MOTIVO RECIBIDO {marca}",
            "funciones_detalle": f"FUNCIONES RECIBIDAS {marca}",
            "diagnostico_medico_detalle": f"CONDICIÓN RECIBIDA {marca}",
            "alergias_detalle": f"ALERGIA RECIBIDA {marca}",
            "medicamentos_detalle": f"MEDICACIÓN RECIBIDA {marca}",
            "antecedentes_hospitalarios_detalle": f"HOSPITALIZACIÓN RECIBIDA {marca}",
            "quirurgicos_detalle": f"CIRUGÍA RECIBIDA {marca}",
            "antecedentes_familiares_detalle": f"FAMILIARES RECIBIDOS {marca}",
            "consumo_riesgo_detalle": f"HÁBITOS RECIBIDOS {marca}",
            "gine_menarca": f"MENARCA RECIBIDA {marca}",
            "evaluacion_psicologica_detalle": f"PSICOLOGÍA RECIBIDA {marca}",
            "decision_cirugia_otros": f"DECISIÓN RECIBIDA {marca}",
            "examen_peso": f"MEDICIÓN RECIBIDA {marca}",
            "ocupacion": f"OFICIO DECLARADO {marca}",
        }
        datos.update(campos)
        fecha = timezone.now() + timedelta(days=-3 if version == "anterior" else 2)
        cita = CitaClinica.objects.create(empresa=empresa, paciente=paciente, fecha_hora=fecha, motivo=f"Agenda {version}")
        generada = self.client.post(reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]), {"cita_id": cita.pk})
        self.assertEqual(generada.status_code, 200)
        preconsulta = generada.context["preconsulta"]
        enlace = urlsplit(generada.context["enlace_publico"]).path
        publico = Client()
        self.assertContains(publico.get(enlace), "Motivo de Consulta")
        enviada = publico.post(enlace, datos)
        self.assertEqual(enviada.status_code, 200)
        self.assertContains(enviada, "Información recibida")
        if version == "anterior":
            PreconsultaClinica.objects.filter(pk=preconsulta.pk).update(fecha_completada=timezone.now() - timedelta(days=3))
        preconsulta.refresh_from_db()
        self.assertEqual(preconsulta.cita_id, cita.pk)
        marcadores = {
            "motivo_consulta": [campos["motivo_consulta"]],
            "funciones_organicas": [campos["funciones_detalle"]],
            "examen_fisico": [campos["examen_peso"]],
            "antecedentes": [campos[campo] for campo in (
                "diagnostico_medico_detalle", "alergias_detalle", "medicamentos_detalle",
                "antecedentes_hospitalarios_detalle", "quirurgicos_detalle", "antecedentes_familiares_detalle",
                "consumo_riesgo_detalle", "gine_menarca", "evaluacion_psicologica_detalle", "decision_cirugia_otros",
            )],
            "datos_generales": [campos["ocupacion"]],
        }
        return preconsulta, marcadores

    def comprobar_paneles(self, respuesta, esperadas, recibidos, app):
        self.assertEqual(respuesta.status_code, 200)
        dom = _PanelesRecibidos(respuesta.content.decode())
        self.assertEqual(set(dom.paneles), set(esperadas))
        for clave in esperadas:
            self.assertEqual(len(dom.paneles[clave]), 1, clave)
            panel = dom.paneles[clave][0]
            self.assertEqual([registro["id"] for registro in panel["registros"]], [registro.pk for registro, _marcadores in recibidos])
            if app:
                self.assertEqual([registro["abierto"] for registro in panel["registros"]], [True, False])
            texto = dom.texto(clave)
            for registro, marcadores in recibidos:
                self.assertIn(f"Cita {registro.cita_id}", texto)
                self.assertIn(timezone.localtime(registro.fecha_completada).strftime("%d/%m/%Y %H:%M"), texto)
                for marcador in marcadores[clave]:
                    self.assertIn(marcador, texto)
                for otra in set(self.SECCIONES) - {clave}:
                    for marcador in marcadores[otra]:
                        self.assertNotIn(marcador, texto)
            actual = recibidos[0][1][clave][0]
            anterior = recibidos[1][1][clave][0]
            self.assertLess(texto.index(actual), texto.index(anterior))
        return dom

    def test_enlace_a_expediente_muestra_respuestas_una_vez_en_su_seccion_web_y_app(self):
        for empresa, paciente in self.casos:
            with self.subTest(empresa=empresa.slug):
                maestro = Paciente.objects.values().get(pk=paciente.pk)
                anterior = self.publicar(empresa, paciente, "anterior")
                actual = self.publicar(empresa, paciente, "actual")
                recibidos = [actual, anterior]
                snapshots = deepcopy(list(PreconsultaClinica.objects.filter(paciente=paciente).order_by("pk").values()))
                conteos = (Paciente.objects.count(), CitaClinica.objects.count(), PreconsultaClinica.objects.count(), HistoriaClinicaEspecialidad.objects.count())
                historial = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
                detalle = reverse("clinica_paciente_detalle", args=[empresa.slug, paciente.pk])
                for app in (False, True):
                    entrada = self.client.get(detalle, {"app": "1"} if app else {})
                    self.assertEqual(entrada.status_code, 200)
                    enlaces = _PanelesRecibidos(entrada.content.decode()).enlaces
                    destinos = [url for url in enlaces if urlsplit(url).path == historial
                                and parse_qs(urlsplit(url).query).get("seccion", ["completa"]) == ["completa"]
                                and (parse_qs(urlsplit(url).query).get("app") == ["1"]) == app]
                    self.assertTrue(destinos, "El expediente debe ofrecer entrada directa al historial")
                    completa = self.client.get(destinos[0].split("#", 1)[0])
                    self.comprobar_paneles(completa, self.SECCIONES, recibidos, app)
                    for seccion in self.SECCIONES:
                        enfoque = self.client.get(historial, {"seccion": seccion, **({"app": "1"} if app else {})})
                        self.comprobar_paneles(enfoque, (seccion,), recibidos, app)
                self.assertEqual(Paciente.objects.values().get(pk=paciente.pk), maestro)
                self.assertEqual(list(PreconsultaClinica.objects.filter(paciente=paciente).order_by("pk").values()), snapshots)
                self.assertEqual((Paciente.objects.count(), CitaClinica.objects.count(), PreconsultaClinica.objects.count(), HistoriaClinicaEspecialidad.objects.count()), conteos)

    def test_las_secciones_recibidas_no_conceden_lectura_fuera_del_permiso(self):
        empresa, paciente = self.casos[0]
        preconsulta, marcadores = self.publicar(empresa, paciente, "parcial")
        rol = RolSistema.objects.create(
            nombre="Solo lectura de antecedentes", codigo="historial-secciones-antecedentes",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True, puede_ver_antecedentes_clinicos=True,
        )
        lector = Usuario.objects.create_user(username="historial-seccion-limitado", empresa=empresa, rol_sistema=rol)
        self.client.force_login(lector)
        historial = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
        for app in (False, True):
            completa = self.client.get(historial, {"app": "1"} if app else {})
            self.assertEqual(completa.status_code, 200)
            dom = _PanelesRecibidos(completa.content.decode())
            self.assertEqual(set(dom.paneles), {"antecedentes"})
            for marcador in marcadores["antecedentes"]:
                self.assertIn(marcador, dom.texto("antecedentes"))
            for clave in set(self.SECCIONES) - {"antecedentes"}:
                for marcador in marcadores[clave]:
                    self.assertNotContains(completa, marcador)
            self.assertNotContains(completa, paciente.identidad)
            self.assertNotContains(completa, paciente.telefono)
            datos = self.client.get(historial, {"seccion": "datos_generales", **({"app": "1"} if app else {})})
            self.assertIn(datos.status_code, (302, 403))
        self.assertEqual(preconsulta.estado, "completada")
        self.assertFalse(HistoriaClinicaEspecialidad.objects.exists())

    def test_envio_importado_sin_fecha_no_desplaza_la_ultima_respuesta_fechada(self):
        empresa, paciente = self.casos[0]
        fechada, _campos = self.publicar(empresa, paciente, "actual")
        importada, _campos_importados = self.publicar(empresa, paciente, "anterior")
        PreconsultaClinica.objects.filter(pk=importada.pk).update(fecha_completada=None)
        historial = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
        for app in (False, True):
            respuesta = self.client.get(historial, {"seccion": "motivo_consulta", **({"app": "1"} if app else {})})
            self.assertEqual(respuesta.status_code, 200)
            dom = _PanelesRecibidos(respuesta.content.decode())
            registros = dom.paneles["motivo_consulta"][0]["registros"]
            self.assertEqual([registro["id"] for registro in registros], [fechada.pk, importada.pk])
            if app:
                self.assertEqual([registro["abierto"] for registro in registros], [True, False])
            self.assertIn("Sin registro", dom.texto("motivo_consulta"))
        importada.refresh_from_db()
        self.assertIsNone(importada.fecha_completada)
