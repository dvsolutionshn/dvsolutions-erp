from datetime import date
from html.parser import HTMLParser

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.access import permiso_contabilidad_accion
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso

from .forms import AsientoContableForm, LineaAsientoFormSet, PeriodoContableForm
from .models import AsientoContable, PeriodoContable


class MapaPermisosContablesTests(SimpleTestCase):
    def test_cada_accion_exige_el_permiso_correspondiente(self):
        rutas = {
            "asientos/crear": "puede_crear_asientos",
            "asientos/12/editar": "puede_crear_asientos",
            "asientos/12/duplicar": "puede_crear_asientos",
            "asientos/12/contabilizar": "puede_contabilizar_asientos",
            "asientos/12/reversar": "puede_contabilizar_asientos",
            "asientos/12/regresar-borrador": "puede_contabilizar_asientos",
            "periodos/12/cerrar": "puede_contabilizar_asientos",
            "periodos/12/abrir": "puede_contabilizar_asientos",
        }
        for ruta, permiso in rutas.items():
            for sufijo in (ruta, f"{ruta}/"):
                with self.subTest(ruta=sufijo):
                    self.assertEqual(permiso_contabilidad_accion(sufijo), permiso)

    def test_consultar_asiento_no_exige_permiso_de_mutacion(self):
        self.assertIsNone(permiso_contabilidad_accion("asientos/12/"))


class FormulariosPermisosContablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Empresa de prueba", slug="permisos-contables", rtn="08011999110001")
        cls.otra_empresa = Empresa.objects.create(nombre="Otra empresa", slug="otra-empresa-contable", rtn="08011999110002")
        cls.rol_contador = RolSistema.objects.create(
            nombre="Contador de prueba", codigo="contador-formulario", puede_contabilizar_asientos=True,
        )
        cls.rol_captura = RolSistema.objects.create(
            nombre="Captura de prueba", codigo="captura-formulario", puede_crear_asientos=True,
        )
        cls.usuario = Usuario.objects.create_user(
            username="contador-formulario", empresa=cls.empresa, rol_sistema=cls.rol_contador,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=cls.usuario, empresa=cls.otra_empresa, rol_sistema=cls.rol_captura,
        )

    def datos_asiento(self, **cambios):
        datos = {"fecha": "2026-10-01", "descripcion": "Operación de prueba", "estado": "borrador"}
        datos.update(cambios)
        return datos

    def test_sin_autorizacion_se_guarda_solamente_borrador(self):
        form = AsientoContableForm(self.datos_asiento(), empresa=self.empresa)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.puede_contabilizar)
        self.assertTrue(form.fields["contabilizar_ahora"].disabled)
        self.assertEqual(form.cleaned_data["estado"], "borrador")
        self.assertFalse(form.cleaned_data["contabilizar_ahora"])

    def test_sin_permiso_rechaza_contabilizacion_falsificada_en_estado_o_casilla(self):
        for cambios in ({"estado": "contabilizado"}, {"contabilizar_ahora": "1"}):
            with self.subTest(cambios=cambios):
                form = AsientoContableForm(self.datos_asiento(**cambios), empresa=self.empresa)
                self.assertFalse(form.is_valid())
                self.assertIn("Tu rol no tiene permiso para contabilizar asientos.", form.non_field_errors())

    def test_permiso_se_resuelve_dentro_de_la_empresa_actual(self):
        autorizado = AsientoContableForm(
            self.datos_asiento(contabilizar_ahora="1"), usuario=self.usuario, empresa=self.empresa,
        )
        denegado = AsientoContableForm(
            self.datos_asiento(contabilizar_ahora="1"), usuario=self.usuario, empresa=self.otra_empresa,
        )
        self.assertTrue(autorizado.is_valid(), autorizado.errors)
        self.assertTrue(autorizado.cleaned_data["contabilizar_ahora"])
        self.assertFalse(denegado.is_valid())

    def test_usuario_autorizado_admite_estado_contabilizado_existente(self):
        form = AsientoContableForm(
            self.datos_asiento(estado="contabilizado"), usuario=self.usuario, empresa=self.empresa,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["estado"], "contabilizado")

    def test_captura_no_permite_anular_desde_estado(self):
        form = AsientoContableForm(
            self.datos_asiento(estado="anulado"), usuario=self.usuario, empresa=self.empresa,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("estado", form.errors)

    def test_periodo_nuevo_no_se_puede_crear_cerrado_desde_formulario(self):
        form = PeriodoContableForm(
            {"anio": "2026", "mes": "10", "estado": "cerrado"}, empresa=self.empresa,
        )
        self.assertNotIn("estado", form.fields)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.instance.estado, "abierto")

    def test_edicion_no_puede_desplazar_periodo_ni_reabrirlo(self):
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=10, estado="cerrado")
        for cambios in ({"anio": "2027"}, {"mes": "11"}, {"estado": "abierto"}):
            with self.subTest(cambios=cambios):
                periodo.refresh_from_db()
                datos = {"anio": "2026", "mes": "10", **cambios}
                form = PeriodoContableForm(datos, instance=periodo, empresa=self.empresa)
                self.assertTrue(form.fields["anio"].disabled)
                self.assertTrue(form.fields["mes"].disabled)
                self.assertFalse(form.is_valid())
                periodo.refresh_from_db()
                self.assertEqual((periodo.anio, periodo.mes, periodo.estado), (2026, 10, "cerrado"))

    def test_editar_observacion_conserva_el_cierre(self):
        periodo = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=10, estado="cerrado")
        form = PeriodoContableForm(
            {"anio": "2026", "mes": "10", "observacion": "Respaldo del cierre revisado"},
            instance=periodo, empresa=self.empresa,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")
        self.assertEqual(periodo.observacion, "Respaldo del cierre revisado")


class _FormulariosHTML(HTMLParser):
    def __init__(self, texto):
        super().__init__()
        self.formularios = []
        self.actual = None
        self.feed(texto)

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "form":
            self.actual = {"atributos": atributos, "campos": []}
            self.formularios.append(self.actual)
        elif self.actual and tag in {"input", "textarea", "button"}:
            self.actual["campos"].append(atributos)

    def handle_endtag(self, tag):
        if tag == "form":
            self.actual = None


class InterfazPermisosContablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Empresa interfaz", slug="interfaz-contable", rtn="08011999110003")
        cls.asiento = AsientoContable.objects.create(
            empresa=cls.empresa, fecha=date(2026, 10, 1), descripcion="Asiento interfaz",
        )
        cls.periodo = PeriodoContable.objects.create(empresa=cls.empresa, anio=2026, mes=10)

    def renderizar(self, plantilla, **contexto):
        return render_to_string(f"contabilidad/{plantilla}", {
            "empresa": self.empresa,
            "asiento": self.asiento,
            "periodo": self.periodo,
            "erp_access": {"asientos_contables": False, "contabilizar_asientos": False},
            "csrf_token": "token-de-prueba",
            **contexto,
        })

    def test_consulta_oculta_crear_editar_y_duplicar_sin_permiso(self):
        detalle = self.renderizar("ver_asiento.html")
        lista = self.renderizar("asientos_contables.html", asientos=[self.asiento])
        for nombre in ("editar_asiento_contable", "duplicar_asiento_contable"):
            self.assertNotIn(reverse(nombre, args=[self.empresa.slug, self.asiento.id]), detalle)
        self.assertNotIn(reverse("editar_asiento_contable", args=[self.empresa.slug, self.asiento.id]), lista)
        self.assertNotIn(reverse("crear_asiento_contable", args=[self.empresa.slug]), lista)

    def test_oculta_reversar_y_regresar_sin_permiso_de_contabilizar(self):
        self.asiento.estado = "contabilizado"
        contenido = self.renderizar("ver_asiento.html")
        for nombre in ("reversar_asiento_contable", "regresar_asiento_borrador"):
            self.assertNotIn(reverse(nombre, args=[self.empresa.slug, self.asiento.id]), contenido)

    def test_captura_muestra_acciones_segun_autorizacion(self):
        for autorizado in (False, True):
            with self.subTest(autorizado=autorizado):
                form = AsientoContableForm(instance=self.asiento, puede_contabilizar=autorizado)
                contenido = self.renderizar(
                    "asiento_form.html", form=form, formset=LineaAsientoFormSet(instance=self.asiento),
                )
                self.assertIn("Guardar borrador", contenido)
                self.assertEqual("Guardar y contabilizar" in contenido, autorizado)
                self.assertIn('name="origen_modulo"', contenido)

    def test_cierre_desde_lista_y_revision_usa_post_y_proteccion_csrf(self):
        self.periodo.control_cierre = {"total": 0}
        destino = reverse("cerrar_periodo_contable", args=[self.empresa.slug, self.periodo.id])
        acceso = {"contabilizar_asientos": True}
        for plantilla, contexto in (
            ("periodos_contables.html", {"periodos": [self.periodo]}),
            ("checklist_cierre_periodo.html", {"control": {"total": 0}}),
        ):
            with self.subTest(plantilla=plantilla):
                contenido = self.renderizar(plantilla, erp_access=acceso, **contexto)
                formulario = next(
                    form for form in _FormulariosHTML(contenido).formularios
                    if form["atributos"].get("action") == destino
                )
                self.assertEqual(formulario["atributos"].get("method"), "post")
                self.assertTrue(any(campo.get("name") == "csrfmiddlewaretoken" for campo in formulario["campos"]))
                self.assertNotIn(f'href="{destino}"', contenido)

    def test_cierre_y_reapertura_ocultos_sin_permiso(self):
        for estado, accion in (("abierto", "cerrar_periodo_contable"), ("cerrado", "abrir_periodo_contable")):
            with self.subTest(estado=estado):
                self.periodo.estado = estado
                self.periodo.control_cierre = {"total": 0}
                contenido = self.renderizar("periodos_contables.html", periodos=[self.periodo])
                self.assertNotIn(reverse(accion, args=[self.empresa.slug, self.periodo.id]), contenido)

    def test_reapertura_requiere_motivo_en_formulario_post(self):
        self.periodo.estado = "cerrado"
        destino = reverse("abrir_periodo_contable", args=[self.empresa.slug, self.periodo.id])
        contenido = self.renderizar(
            "periodos_contables.html", periodos=[self.periodo], erp_access={"contabilizar_asientos": True},
        )
        formulario = next(
            form for form in _FormulariosHTML(contenido).formularios
            if form["atributos"].get("action") == destino
        )
        motivo = next(campo for campo in formulario["campos"] if campo.get("name") == "motivo_reapertura")
        self.assertEqual(formulario["atributos"].get("method"), "post")
        self.assertIn("required", motivo)
        self.assertEqual(motivo["maxlength"], "400")
