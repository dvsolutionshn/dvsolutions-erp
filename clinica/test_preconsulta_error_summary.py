from types import SimpleNamespace

from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import Empresa

from .forms import PreconsultaClinicaPublicaForm
from .models import Paciente, PreconsultaClinica


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


class PreconsultaFlujoCortoTests(TestCase):
    def test_no_valida_campos_clinicos_historicos_del_segundo_formulario(self):
        empresa = Empresa.objects.create(
            nombre="Hospital MIA",
            slug="hospital-mia-flujo-corto",
            tipo_solucion="clinica",
        )
        paciente = Paciente.objects.create(
            empresa=empresa,
            expediente_codigo="MIA-CORTO-001",
            primer_nombre="Camisa",
            segundo_nombre="Lunamia",
            primer_apellido="Duron",
            segundo_apellido="Martinez",
            identidad="0801199512345",
            fecha_nacimiento="1995-08-12",
            sexo="femenino",
            estado_civil="soltero",
            telefono="99990001",
            whatsapp="99990001",
        )
        preconsulta = PreconsultaClinica.objects.create(
            empresa=empresa,
            paciente=paciente,
            tipo="general",
            token_hash="flujo-corto-camila",
            token_preview="flujo...",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=30),
            dieta="Alimentacion balanceada indicada anteriormente",
            ejercicio="Actividad fisica tres veces por semana",
            datos_generales={
                "referido_por": "facebook",
                "informante": "yo_mismo",
                "formulario_general": {"motivo_categoria": ["no_aplica"]},
            },
        )
        form = PreconsultaClinicaPublicaForm(
            {
                "nombres": "Camila Lunamia",
                "apellidos": "Duron Martinez",
                "identidad": paciente.identidad,
                "fecha_nacimiento": "1995-08-12",
                "sexo": "femenino",
                "estado_civil": "soltero",
                "correo": "",
                "telefono_codigo_area": "504",
                "telefono": "99990001",
                "direccion": "",
                "lugar_nacimiento": "",
                "ocupacion": "",
                "lugar_trabajo": "",
                "informante": "yo_mismo",
                "contacto_emergencia_completo": "",
                "referido_por": "facebook",
                "motivo_categoria": ["no_aplica"],
                "procedimientos_interes": [],
                "procedimientos_interes_otros": "",
            },
            instance=preconsulta,
            paciente=paciente,
            empresa=empresa,
            modo_basico_paciente_nuevo=True,
        )

        self.assertNotIn("dieta", form.fields)
        self.assertNotIn("ejercicio", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        instancia = form.save(commit=False)
        self.assertEqual(instancia.dieta, "Alimentacion balanceada indicada anteriormente")
        self.assertEqual(instancia.ejercicio, "Actividad fisica tres veces por semana")
