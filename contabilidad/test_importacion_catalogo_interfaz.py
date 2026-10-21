from io import BytesIO
import time
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from openpyxl import Workbook

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario

from .models import CuentaContable
from .views import importar_catalogo_cuentas


class ImportacionCatalogoInterfazTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(nombre="Plan catálogo", codigo="plan-catalogo", precio_mensual="100.00")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="RUMI", slug="rumi", rtn="08011999102001",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Otra empresa catálogo", slug="otra-empresa-catalogo", rtn="08011999102002",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.usuario = Usuario.objects.create_user(
            username="administrador-catalogo", empresa=cls.empresa, is_superuser=True, is_staff=True,
        )
        cls.otro_usuario = Usuario.objects.create_user(
            username="otro-administrador-catalogo", empresa=cls.empresa, is_superuser=True, is_staff=True,
        )
        rol = RolSistema.objects.create(
            nombre="Consulta contable", codigo="consulta-catalogo", puede_contabilidad=True,
            puede_catalogo_cuentas=False,
        )
        cls.usuario_consulta = Usuario.objects.create_user(
            username="consulta-catalogo", empresa=cls.empresa, rol_sistema=rol,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("importar_catalogo_cuentas", args=[self.empresa.slug])

    def _archivo(self, filas=None, nombre="catalogo.xlsx"):
        libro = Workbook()
        hoja = libro.active
        hoja.title = "Catálogo"
        hoja.append(["codigo", "nombre", "tipo", "codigo_padre", "acepta_movimientos", "activa"])
        for fila in filas or [
            ["100", "Activos", "activo", "", "no", "si"],
            ["101", "Caja", "activo", "100", "si", "si"],
        ]:
            hoja.append(fila)
        contenido = BytesIO()
        libro.save(contenido)
        return SimpleUploadedFile(nombre, contenido.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def _revisar(self, **opciones):
        datos = {"accion": "revisar", "archivo": self._archivo(), "incluir_controles": "on"}
        datos.update(opciones)
        respuesta = self.client.post(self.url, datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["revision_token"], respuesta.context["form"].errors)
        return respuesta

    def _confirmar(self, token, **opciones):
        datos = {"accion": "importar", "revision_token": token, "confirmar": "on"}
        datos.update(opciones)
        return self.client.post(self.url, datos)

    def test_get_preserva_cuentas_existentes_por_defecto(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["form"]["actualizar_existentes"].value())
        self.assertTrue(respuesta.context["form"]["incluir_controles"].value())
        self.assertContains(respuesta, "Revisar archivo")
        self.assertFalse(CuentaContable.objects.exists())

    def test_revisar_muestra_empresa_y_jerarquia_sin_guardar(self):
        respuesta = self._revisar()
        self.assertEqual(respuesta.context["vista_previa"]["creadas"], 2)
        self.assertContains(respuesta, "RUMI")
        self.assertContains(respuesta, "Confirmar importación")
        self.assertContains(respuesta, "Admite asientos")
        self.assertEqual(respuesta.context["vista_previa"]["filas"][1]["codigo_padre"], "100")
        self.assertFalse(CuentaContable.objects.exists())

    def test_confirmar_sin_volver_a_subir_archivo_importa_solo_empresa_revisada(self):
        revision = self._revisar()
        respuesta = self._confirmar(revision.context["revision_token"])
        self.assertRedirects(respuesta, reverse("catalogo_cuentas", args=[self.empresa.slug]))
        caja = CuentaContable.objects.get(empresa=self.empresa, codigo="101")
        self.assertEqual(caja.cuenta_padre.codigo, "100")
        self.assertFalse(CuentaContable.objects.filter(empresa=self.otra_empresa).exists())

    def test_confirmacion_ausente_conserva_vista_previa_sin_guardar(self):
        revision = self._revisar()
        respuesta = self._confirmar(revision.context["revision_token"], confirmar="")
        self.assertContains(respuesta, "Marca la confirmación")
        self.assertEqual(respuesta.context["revision_token"], revision.context["revision_token"])
        self.assertFalse(CuentaContable.objects.exists())

    def test_importar_sin_revision_previa_no_guarda(self):
        respuesta = self._confirmar("")
        self.assertContains(respuesta, "La revisión falta o fue modificada")
        self.assertFalse(CuentaContable.objects.exists())

    def test_firma_modificada_no_importa(self):
        revision = self._revisar()
        respuesta = self._confirmar(revision.context["revision_token"] + "x")
        self.assertContains(respuesta, "La revisión falta o fue modificada")
        self.assertFalse(CuentaContable.objects.exists())

    def test_firma_vencida_pide_revisar_archivo(self):
        revision = self._revisar()
        datos = signing.loads(revision.context["revision_token"], salt="contabilidad.importacion-catalogo.v1")
        with patch("django.core.signing.TimestampSigner.timestamp", return_value=signing.b62_encode(int(time.time()) - 1801)):
            vencida = signing.dumps(datos, salt="contabilidad.importacion-catalogo.v1", compress=True)
        respuesta = self._confirmar(vencida)
        self.assertContains(respuesta, "La revisión venció")
        self.assertFalse(CuentaContable.objects.exists())

    def test_revision_no_se_reutiliza_en_otra_empresa(self):
        revision = self._revisar()
        respuesta = self.client.post(
            reverse("importar_catalogo_cuentas", args=[self.otra_empresa.slug]),
            {"accion": "importar", "revision_token": revision.context["revision_token"], "confirmar": "on"},
        )
        self.assertTemplateUsed(respuesta, "contabilidad/importar_catalogo_cuentas.html")
        self.assertIn("archivo", respuesta.context["form"].errors)
        self.assertNotIn("vista_previa", respuesta.context)
        self.assertFalse(CuentaContable.objects.exists())

    def test_revision_no_se_reutiliza_por_otro_usuario(self):
        revision = self._revisar()
        self.client.force_login(self.otro_usuario)
        respuesta = self._confirmar(revision.context["revision_token"])
        self.assertContains(respuesta, "Esta revisión pertenece a otra empresa o usuario")
        self.assertFalse(CuentaContable.objects.exists())

    def test_opcion_agregada_al_confirmar_no_modifica_opciones_firmadas(self):
        revision = self._revisar()
        with patch("contabilidad.views.aplicar_catalogo_cuentas", return_value={"creadas": 2, "actualizadas": 0, "sin_cambios": 0}) as aplicar:
            respuesta = self._confirmar(revision.context["revision_token"], actualizar_existentes="on", prefijo_codigos="ALTERADO-")
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(aplicar.call_args.kwargs["actualizar_existentes"])
        self.assertEqual(aplicar.call_args.args[1][0]["codigo"], "100")

    def test_cambio_de_catalogo_despues_de_revisar_impide_importar(self):
        CuentaContable.objects.create(empresa=self.empresa, codigo="100", nombre="Activos", tipo="activo", acepta_movimientos=False)
        revision = self._revisar(actualizar_existentes="on")
        CuentaContable.objects.filter(empresa=self.empresa, codigo="100").update(nombre="Activos revisados después")
        respuesta = self._confirmar(revision.context["revision_token"])
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Vuelve a subir y revisar el archivo")
        self.assertFalse(CuentaContable.objects.filter(empresa=self.empresa, codigo="101").exists())
        self.assertEqual(CuentaContable.objects.get(empresa=self.empresa, codigo="100").nombre, "Activos revisados después")

    def test_prefijo_muestra_e_importa_jerarquia_final(self):
        revision = self._revisar(prefijo_codigos="RUMI-")
        self.assertContains(revision, "RUMI-101")
        self.assertContains(revision, "Original: 101")
        self.assertEqual(revision.context["vista_previa"]["filas"][1]["codigo_padre"], "RUMI-100")
        respuesta = self._confirmar(revision.context["revision_token"])
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(CuentaContable.objects.get(empresa=self.empresa, codigo="RUMI-101").cuenta_padre.codigo, "RUMI-100")

    def test_extension_invalida_se_muestra_en_espanol(self):
        respuesta = self.client.post(self.url, {"accion": "revisar", "archivo": self._archivo(nombre="catalogo.xls")})
        self.assertContains(respuesta, "Sube un archivo Excel válido")
        self.assertFalse(respuesta.context["revision_token"])
        self.assertFalse(CuentaContable.objects.exists())

    def test_vista_exige_permiso_de_catalogo_incluso_sin_middleware(self):
        fabrica = RequestFactory()
        for peticion in (fabrica.get(self.url), fabrica.post(self.url, {"accion": "importar"})):
            peticion.user = self.usuario_consulta
            with self.assertRaises(PermissionDenied):
                importar_catalogo_cuentas(peticion, self.empresa.slug)

    def test_importacion_completa_no_se_limita_a_cincuenta_filas_visibles(self):
        filas = [[f"A{numero:03}", f"Cuenta {numero}", "activo", "", "si", "si"] for numero in range(53)]
        revision = self._revisar(archivo=self._archivo(filas))
        self.assertEqual(len(revision.context["vista_previa"]["filas"]), 50)
        self.assertEqual(revision.context["vista_previa"]["no_mostradas"], 3)
        self.assertEqual(len(revision.context["vista_previa"]["filas_adicionales"]), 3)
        self.assertContains(revision, "Ver las 3 cuentas restantes")
        self.assertContains(revision, "Cuenta 52")
        self.assertContains(revision, "A052")
        respuesta = self._confirmar(revision.context["revision_token"])
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.empresa).count(), 53)

    def test_solo_get_y_post_estan_permitidos(self):
        respuesta = self.client.put(self.url)
        self.assertEqual(respuesta.status_code, 405)


class ImportacionCatalogoAnteriorInterfazTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ImportacionCatalogoInterfazTests.setUpTestData.__func__(cls)
        cls.empresa_nombre_rumi = Empresa.objects.create(
            nombre="RUMI", slug="rumi-otra", rtn="08011999102003",
            estado_licencia="activa", plan_comercial=cls.empresa.plan_comercial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("importar_catalogo_cuentas", args=[self.otra_empresa.slug])

    def _archivo(self, *args, **kwargs):
        return ImportacionCatalogoInterfazTests._archivo(self, *args, **kwargs)

    def test_otras_empresas_tienen_formulario_anterior_y_actualizacion_predeterminada(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "contabilidad/importar_catalogo_cuentas.html")
        self.assertEqual(set(respuesta.context["form"].fields), {"archivo", "actualizar_existentes"})
        self.assertTrue(respuesta.context["form"]["actualizar_existentes"].value())
        self.assertContains(respuesta, "Importar Catalogo")
        self.assertNotContains(respuesta, "Revisar archivo")
        self.assertNotContains(respuesta, "Conservar controles extracontables")
        self.assertNotIn("vista_previa", respuesta.context)

    def test_otras_empresas_importan_formato_estandar_en_un_solo_paso(self):
        respuesta = self.client.post(self.url, {"archivo": self._archivo(), "actualizar_existentes": "on"})
        self.assertRedirects(respuesta, reverse("catalogo_cuentas", args=[self.otra_empresa.slug]))
        caja = CuentaContable.objects.get(empresa=self.otra_empresa, codigo="101")
        self.assertEqual(caja.cuenta_padre.codigo, "100")
        self.assertFalse(CuentaContable.objects.filter(empresa=self.empresa).exists())

    def test_otras_empresas_rechazan_encabezados_adelantados_como_antes(self):
        libro = Workbook()
        hoja = libro.active
        hoja.append(["RUMI: propuesta de catálogo"])
        hoja.append(["codigo", "nombre", "tipo"])
        hoja.append(["100", "Activos", "activo"])
        contenido = BytesIO()
        libro.save(contenido)
        respuesta = self.client.post(self.url, {"archivo": SimpleUploadedFile("RUMI.xlsx", contenido.getvalue())})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "contabilidad/importar_catalogo_cuentas.html")
        self.assertFalse(CuentaContable.objects.exists())
        self.assertNotIn("vista_previa", respuesta.context)

    def test_nombre_rumi_y_slug_similar_no_habilitan_importador_especial(self):
        respuesta = self.client.get(reverse("importar_catalogo_cuentas", args=[self.empresa_nombre_rumi.slug]))
        self.assertTemplateUsed(respuesta, "contabilidad/importar_catalogo_cuentas.html")
        self.assertEqual(set(respuesta.context["form"].fields), {"archivo", "actualizar_existentes"})

    def test_otras_empresas_actualizan_cuentas_en_el_post_original(self):
        cuenta = CuentaContable.objects.create(
            empresa=self.otra_empresa, codigo="100", nombre="Anterior", tipo="activo", acepta_movimientos=False,
        )
        respuesta = self.client.post(self.url, {"archivo": self._archivo(), "actualizar_existentes": "on"})
        self.assertEqual(respuesta.status_code, 302)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.nombre, "Activos")
        self.assertFalse(CuentaContable.objects.filter(empresa=self.empresa).exists())
