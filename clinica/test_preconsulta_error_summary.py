from types import SimpleNamespace

from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class PreconsultaErrorSummaryTests(SimpleTestCase):
    def test_resumen_muestra_nombre_del_campo_y_error(self):
        class FormularioPrueba(forms.Form):
            nombres = forms.CharField(label="Primer y segundo nombre")
            apellidos = forms.CharField(label="Primer y segundo apellido")

        form = FormularioPrueba({"nombres": "Camila Lunamia", "apellidos": ""})
        self.assertFalse(form.is_valid())
        form.procedimientos_interes_grupos = []
        empresa = SimpleNamespace(nombre="Clínica", logo=None)

        html = render_to_string(
            "clinica/preconsulta_publica.html",
            {
                "form": form,
                "preconsulta": SimpleNamespace(empresa=empresa),
                "empresa": empresa,
                "registro_interno": True,
                "flujo_paciente_nuevo_corto": True,
                "modo_edicion_paciente": True,
            },
        )

        self.assertIn("Primer y segundo apellido:", html)
        self.assertIn("Este campo es obligatorio.", html)

