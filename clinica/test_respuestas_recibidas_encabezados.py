"""Identificación visible de registros iniciales y respuestas clínicas recibidas."""

from html.parser import HTMLParser
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class _ReceivedHeaders(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.in_summary = False
        self.reading = None
        self.headers = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "summary":
            self.in_summary = True
        if tag == "h4" or (tag == "strong" and self.in_summary):
            self.reading = tag
            self.headers.append("")

    def handle_endtag(self, tag):
        if tag == self.reading:
            self.reading = None
        if tag == "summary":
            self.in_summary = False

    def handle_data(self, text):
        if self.reading:
            self.headers[-1] += text


class ReceivedHistoryHeaderTests(SimpleTestCase):
    def test_el_encabezado_distingue_registro_inicial_de_historia_respondida_web_y_app(self):
        preconsulta = SimpleNamespace(
            id=7, get_tipo_display=lambda: "Medicina Estética", fecha_completada=None, cita_id=None,
        )
        secciones = [{"titulo": "Respuesta recibida", "campos": [
            {"label": "Detalle", "type": "texto", "value": "Contenido recibido conservado QA"},
        ]}]
        for inicial, interno, etiqueta in (
            (True, False, "Registro inicial del paciente"),
            (False, False, "Historia clínica respondida · Medicina Estética"),
            (False, True, "Registro interno del expediente"),
        ):
            contexto = {
                "empresa": SimpleNamespace(slug="hospital_mia"), "paciente": SimpleNamespace(id=3),
                "bloques": [{"preconsulta": preconsulta, "secciones": secciones,
                             "registro_inicial_pendiente": inicial, "registro_interno": interno}],
                "abrir_reciente": True,
            }
            for app in (False, True):
                with self.subTest(inicial=inicial, interno=interno, app=app):
                    parcial = "respuestas_preconsulta_mobile.html" if app else "respuestas_preconsulta.html"
                    html = render_to_string("clinica/includes/" + parcial, contexto)
                    encabezados = _ReceivedHeaders(html).headers
                    self.assertEqual(encabezados, [etiqueta if app else etiqueta + " · Sin registro"])
                    self.assertIn("Contenido recibido conservado QA", html)
                    self.assertIn('data-preconsulta-id="7"', html)
                    self.assertNotIn("Historia clínica completa respondida", html)
                    if app:
                        self.assertIn('data-preconsulta-id="7" open', html)
                    else:
                        self.assertIn("Respuestas guardadas en este formulario", html)
                        self.assertNotIn("Formulario completado por el paciente", html)
