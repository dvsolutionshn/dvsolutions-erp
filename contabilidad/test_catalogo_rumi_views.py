"""Aislamiento, revisión firmada y enlaces de la instalación exclusiva de RUMI."""

from io import BytesIO
import time
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .catalogo_rumi_roles import CAMPOS_CONFIG, ROLES, resolver_cuenta_rumi
from .catalogo_rumi_views import (
    SALT, EnlacesContablesRumiForm, configuracion_rumi,
    respaldo_catalogo_rumi, sustituir_catalogo_rumi,
)
from .models import (
    ConfiguracionContableEmpresa, CuentaContable,
    RegistroSustitucionCatalogoRumi, VinculoCuentaOperativaRumi,
)
from .sustitucion_catalogo_rumi import DESCRIPCION_BASE
from .test_importacion_catalogo import libro_rumi


class CatalogoRumiVistasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(
            nombre="Plan instalación RUMI", codigo="plan-instalacion-rumi", precio_mensual="100.00",
        )
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="RUMI", slug="rumi", rtn="08011999222001",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="RUMI sucursal", slug="rumi-sucursal", rtn="08011999222002",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.usuario = Usuario.objects.create_user(
            username="administrador-instalacion-rumi", empresa=cls.empresa,
            is_superuser=True, is_staff=True,
        )
        cls.otro_usuario = Usuario.objects.create_user(
            username="segundo-administrador-instalacion-rumi", empresa=cls.empresa,
            is_superuser=True, is_staff=True,
        )
        rol = RolSistema.objects.create(
            nombre="Consulta RUMI", codigo="consulta-instalacion-rumi",
            puede_contabilidad=True, puede_catalogo_cuentas=False,
        )
        cls.usuario_consulta = Usuario.objects.create_user(
            username="consulta-instalacion-rumi", empresa=cls.empresa, rol_sistema=rol,
        )
        cls.raiz_base = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1", nombre="ACTIVOS", tipo="activo",
            acepta_movimientos=False, descripcion=DESCRIPCION_BASE,
        )
        cls.grupo_base = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="11", nombre="ACTIVO CORRIENTE", tipo="activo",
            cuenta_padre=cls.raiz_base, acepta_movimientos=False, descripcion=DESCRIPCION_BASE,
        )
        cls.base = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1101", nombre="Caja General", tipo="activo",
            cuenta_padre=cls.grupo_base, descripcion=DESCRIPCION_BASE,
        )
        cls.configuracion = ConfiguracionContableEmpresa.objects.create(
            empresa=cls.empresa, cuenta_caja=cls.base, isv_compras_recuperable=True,
        )
        cls.cuenta_ajena = CuentaContable.objects.create(
            empresa=cls.otra_empresa, codigo="1101", nombre="Caja General", tipo="activo",
            descripcion=DESCRIPCION_BASE,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("sustituir_catalogo_rumi", args=[self.empresa.slug])
        self.configuracion_url = reverse("configuracion_contable", args=[self.empresa.slug])

    def _archivo(self, nombre="catalogo-rumi.xlsx"):
        archivo = BytesIO()
        libro = libro_rumi()
        libro.save(archivo)
        libro.close()
        return SimpleUploadedFile(
            nombre, archivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _revisar(self):
        respuesta = self.client.post(self.url, {"accion": "revisar", "archivo": self._archivo()})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["error"])
        self.assertTrue(respuesta.context["revision_token"])
        return respuesta

    def _aplicar(self, token, **datos):
        post = {"accion": "aplicar", "revision_token": token, "confirmar": "on"}
        post.update(datos)
        return self.client.post(self.url, post)

    def _estado(self):
        return {
            "cuentas": list(CuentaContable.objects.order_by("pk").values()),
            "configuraciones": list(ConfiguracionContableEmpresa.objects.order_by("pk").values()),
            "vinculos": list(VinculoCuentaOperativaRumi.objects.order_by("pk").values()),
            "registros": list(RegistroSustitucionCatalogoRumi.objects.order_by("pk").values()),
        }

    def _instalar(self):
        respuesta = self._aplicar(self._revisar().context["revision_token"])
        self.assertRedirects(respuesta, self.configuracion_url)
        return RegistroSustitucionCatalogoRumi.objects.get(empresa=self.empresa)

    def test_get_muestra_revision_espanol_sin_escribir(self):
        antes = self._estado()
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "contabilidad/sustituir_catalogo_rumi.html")
        self.assertContains(respuesta, "Revisar sustitución")
        self.assertContains(respuesta, "exclusivamente en RUMI")
        self.assertIsNone(respuesta.context["plan"])
        self.assertEqual(self._estado(), antes)

    def test_otras_empresas_rechazan_get_y_post_sin_modificaciones(self):
        url = reverse("sustituir_catalogo_rumi", args=[self.otra_empresa.slug])
        antes = self._estado()
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                respuesta = getattr(self.client, metodo)(url)
                self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(self._estado(), antes)

    def test_vistas_exigen_permiso_de_catalogo_sin_depender_del_middleware(self):
        fabrica = RequestFactory()
        antes = self._estado()
        for metodo in ("get", "post"):
            peticion = getattr(fabrica, metodo)(self.url, {"accion": "aplicar"})
            peticion.user = self.usuario_consulta
            with self.subTest(metodo=metodo), self.assertRaises(PermissionDenied):
                sustituir_catalogo_rumi(peticion, self.empresa.slug)
        peticion = fabrica.get(self.configuracion_url)
        peticion.user = self.usuario_consulta
        with self.assertRaises(PermissionDenied):
            configuracion_rumi(peticion, self.empresa)
        peticion = fabrica.get("/respaldo/")
        peticion.user = self.usuario_consulta
        with self.assertRaises(PermissionDenied):
            respaldo_catalogo_rumi(peticion, self.empresa.slug, 1)
        self.assertEqual(self._estado(), antes)

    def test_preview_firma_empresa_usuario_datos_sin_guardar_respaldo(self):
        antes = self._estado()
        respuesta = self._revisar()
        plan = respuesta.context["plan"]
        datos = signing.loads(respuesta.context["revision_token"], salt=SALT)
        self.assertEqual(datos["empresa_id"], self.empresa.pk)
        self.assertEqual(datos["usuario_id"], self.usuario.pk)
        self.assertEqual(datos["version"], 1)
        self.assertEqual(datos["firma"], plan["firma"])
        self.assertEqual(len(datos["datos"]), plan["importables"])
        self.assertNotIn("respaldo", plan)
        self.assertNotIn("respaldo", datos)
        self.assertContains(respuesta, "Aplicar catálogo RUMI y guardar respaldo")
        self.assertEqual(self._estado(), antes)

    def test_revision_alterada_o_ausente_no_escribe(self):
        revision = self._revisar()
        antes = self._estado()
        for token in ("", revision.context["revision_token"] + "x"):
            with self.subTest(token_ausente=not token):
                respuesta = self._aplicar(token)
                self.assertContains(respuesta, "La revisión venció o fue modificada")
                self.assertEqual(self._estado(), antes)

    def test_revision_vencida_no_escribe(self):
        revision = self._revisar()
        datos = signing.loads(revision.context["revision_token"], salt=SALT)
        with patch("django.core.signing.TimestampSigner.timestamp", return_value=signing.b62_encode(int(time.time()) - 1801)):
            vencida = signing.dumps(datos, salt=SALT, compress=True)
        antes = self._estado()
        respuesta = self._aplicar(vencida)
        self.assertContains(respuesta, "La revisión venció o fue modificada")
        self.assertEqual(self._estado(), antes)

    def test_revision_firmada_de_otra_empresa_usuario_o_version_no_escribe(self):
        revision = self._revisar()
        original = signing.loads(revision.context["revision_token"], salt=SALT)
        antes = self._estado()
        for campo, valor in (("empresa_id", self.otra_empresa.pk), ("usuario_id", self.otro_usuario.pk), ("version", 2)):
            datos = {**original, campo: valor}
            token = signing.dumps(datos, salt=SALT, compress=True)
            with self.subTest(campo=campo):
                respuesta = self._aplicar(token)
                self.assertContains(respuesta, "Esta revisión pertenece a otra empresa o usuario")
                self.assertEqual(self._estado(), antes)

    def test_segundo_usuario_no_reutiliza_revision_del_primero(self):
        revision = self._revisar()
        antes = self._estado()
        self.client.force_login(self.otro_usuario)
        respuesta = self._aplicar(revision.context["revision_token"])
        self.assertContains(respuesta, "Esta revisión pertenece a otra empresa o usuario")
        self.assertEqual(self._estado(), antes)

    def test_confirmacion_es_obligatoria_y_no_escribe_si_falta(self):
        revision = self._revisar()
        antes = self._estado()
        respuesta = self._aplicar(revision.context["revision_token"], confirmar="")
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["error"])
        self.assertEqual(self._estado(), antes)

    def test_cambio_tras_revisar_obliga_repetir_revision(self):
        revision = self._revisar()
        ConfiguracionContableEmpresa.objects.filter(pk=self.configuracion.pk).update(isv_compras_recuperable=False)
        antes = self._estado()
        respuesta = self._aplicar(revision.context["revision_token"])
        self.assertContains(respuesta, "cambiaron desde la revisión")
        self.assertEqual(self._estado(), antes)

    def test_aplicar_instala_y_guarda_respaldo_solo_de_rumi(self):
        ajena = dict(CuentaContable.objects.filter(pk=self.cuenta_ajena.pk).values().get())
        registro = self._instalar()
        self.configuracion.refresh_from_db()
        self.assertTrue(self.configuracion.catalogo_rumi_instalado)
        self.assertTrue(self.configuracion.isv_compras_recuperable)
        self.assertEqual(registro.usuario_id, self.usuario.pk)
        respaldo_caja = next(cuenta for cuenta in registro.respaldo["cuentas"] if cuenta["codigo"] == "1101")
        self.assertEqual(respaldo_caja["descripcion"], DESCRIPCION_BASE)
        self.assertEqual(respaldo_caja["cuenta_padre_id"], self.grupo_base.pk)
        self.assertEqual(registro.resumen["eliminadas"], 3)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.empresa).count(), registro.resumen["importadas"])
        self.assertEqual(CuentaContable.objects.get(empresa=self.empresa, codigo="5").tipo, "ingreso")
        self.assertEqual(CuentaContable.objects.get(empresa=self.empresa, codigo="4").tipo, "orden")
        self.assertEqual(dict(CuentaContable.objects.filter(pk=self.cuenta_ajena.pk).values().get()), ajena)
        self.assertFalse(ConfiguracionContableEmpresa.objects.filter(empresa=self.otra_empresa, catalogo_rumi_instalado=True).exists())

    def test_error_al_aplicar_revierte_retiro_importacion_configuracion_y_respaldo(self):
        revision = self._revisar()
        antes = self._estado()
        with patch("contabilidad.sustitucion_catalogo_rumi.aplicar_catalogo_cuentas", side_effect=ValidationError("Error de importación simulado")):
            respuesta = self._aplicar(revision.context["revision_token"])
        self.assertContains(respuesta, "Error de importación simulado")
        self.assertEqual(self._estado(), antes)

    def test_respaldo_descargable_no_se_expone_a_otra_empresa(self):
        registro = self._instalar()
        url = reverse("respaldo_catalogo_rumi", args=[self.empresa.slug, registro.pk])
        antes = self._estado()
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["empresa"], "rumi")
        self.assertEqual(respuesta.json()["respaldo"], registro.respaldo)
        self.assertIn("attachment", respuesta["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("respaldo_catalogo_rumi", args=[self.otra_empresa.slug, registro.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("respaldo_catalogo_rumi", args=[self.empresa.slug, registro.pk + 100])).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self._estado(), antes)

    def test_configuracion_muestra_formato_rumi_solo_despues_de_instalar(self):
        respuesta = self.client.get(self.configuracion_url)
        self.assertTemplateUsed(respuesta, "contabilidad/configuracion_contable.html")
        self._instalar()
        respuesta = self.client.get(self.configuracion_url)
        self.assertTemplateUsed(respuesta, "contabilidad/configuracion_rumi.html")
        self.assertEqual(set(respuesta.context["form"].fields), {"isv_compras_recuperable"} | {"rol_" + clave for clave in ROLES})
        respuesta = self.client.get(reverse("configuracion_contable", args=[self.otra_empresa.slug]))
        self.assertTemplateUsed(respuesta, "contabilidad/configuracion_contable.html")
        self.assertNotIn("rol_caja", respuesta.context["form"].fields)

    def test_instalacion_repetida_redirige_a_enlaces_sin_otro_respaldo(self):
        self._instalar()
        antes = self._estado()
        self.assertRedirects(self.client.get(self.url), self.configuracion_url)
        self.assertRedirects(self.client.post(self.url, {"accion": "aplicar"}), self.configuracion_url)
        self.assertEqual(self._estado(), antes)


class EnlacesContablesRumiFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999223001")
        cls.otra = Empresa.objects.create(nombre="Otra empresa enlaces", slug="otra-enlaces-rumi", rtn="08011999223002")
        cls.configuracion = ConfiguracionContableEmpresa.objects.create(
            empresa=cls.empresa, catalogo_rumi_instalado=True, isv_compras_recuperable=True,
        )
        cls.cuentas = {}
        for tipo in ("activo", "pasivo", "patrimonio", "ingreso", "costo", "gasto"):
            cls.cuentas[tipo] = CuentaContable.objects.create(
                empresa=cls.empresa, codigo=f"R-{tipo}", nombre=f"Cuenta propia {tipo}", tipo=tipo,
            )
        cls.ajena = CuentaContable.objects.create(empresa=cls.otra, codigo="A-AJENA", nombre="Cuenta ajena", tipo="activo")
        cls.condicionada = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="A-CONDICIONADA", nombre="Cuenta condicionada", tipo="activo", activa=False,
        )
        cls.agrupadora = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="A-GRUPO", nombre="Cuenta agrupadora", tipo="activo", acepta_movimientos=False,
        )
        cls.control = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="7", nombre="Cuentas de Orden", tipo="orden", activa=False, acepta_movimientos=False,
        )

    def _form(self, datos=None):
        return EnlacesContablesRumiForm(datos, empresa=self.empresa, configuracion=self.configuracion)

    def _datos_completos(self):
        datos = {"isv_compras_recuperable": "on"}
        for clave, definicion in ROLES.items():
            datos["rol_" + clave] = self.cuentas[definicion["tipos"][0]].pk
        return datos

    def test_formulario_cubre_todos_los_roles_y_filtra_por_empresa_tipo_y_actividad(self):
        form = self._form()
        self.assertEqual(len(ROLES), 26)
        self.assertEqual(set(form.fields), {"isv_compras_recuperable"} | {"rol_" + clave for clave in ROLES})
        for clave, definicion in ROLES.items():
            campo = form.fields["rol_" + clave]
            with self.subTest(clave=clave):
                self.assertFalse(campo.required)
                self.assertEqual(campo.empty_label, "Pendiente de configurar")
                esperadas = {self.cuentas[tipo].pk for tipo in definicion["tipos"]}
                self.assertEqual(set(campo.queryset.values_list("pk", flat=True)), esperadas)
        self.assertTrue(form.initial["isv_compras_recuperable"])

    def test_no_se_pueden_enlazar_cuentas_ajenas_condicionadas_agrupadoras_o_controles(self):
        for cuenta in (self.ajena, self.condicionada, self.agrupadora, self.control, self.cuentas["ingreso"]):
            with self.subTest(cuenta=cuenta.codigo):
                form = self._form({"rol_caja": cuenta.pk, "isv_compras_recuperable": "on"})
                self.assertFalse(form.is_valid())
                self.assertIn("rol_caja", form.errors)
        self.configuracion.refresh_from_db()
        self.assertIsNone(self.configuracion.cuenta_caja_id)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.exists())

    def test_guarda_los_26_enlaces_y_conserva_politica_isv_seleccionada(self):
        form = self._form(self._datos_completos())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.configuracion.refresh_from_db()
        self.assertTrue(self.configuracion.isv_compras_recuperable)
        for clave, definicion in ROLES.items():
            cuenta = self.cuentas[definicion["tipos"][0]]
            with self.subTest(clave=clave):
                campo = CAMPOS_CONFIG.get(clave)
                if campo:
                    self.assertEqual(getattr(self.configuracion, campo + "_id"), cuenta.pk)
                else:
                    self.assertEqual(VinculoCuentaOperativaRumi.objects.get(empresa=self.empresa, clave=clave).cuenta_id, cuenta.pk)
        self.assertEqual(VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa).count(), len(ROLES) - len(CAMPOS_CONFIG))
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.otra).exists())
        form_recargado = self._form()
        for clave, definicion in ROLES.items():
            self.assertEqual(form_recargado.initial["rol_" + clave], self.cuentas[definicion["tipos"][0]].pk)

    def test_enlaces_opcionales_pueden_quedar_pendientes_y_bool_isv_se_actualiza(self):
        form = self._form(self._datos_completos())
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form = self._form({})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.configuracion.refresh_from_db()
        self.assertFalse(self.configuracion.isv_compras_recuperable)
        for campo in CAMPOS_CONFIG.values():
            self.assertIsNone(getattr(self.configuracion, campo + "_id"))
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa).exists())

    def test_cuenta_desactivada_tras_validacion_revierte_todos_los_enlaces(self):
        form = self._form(self._datos_completos())
        self.assertTrue(form.is_valid(), form.errors)
        self.cuentas["gasto"].activa = False
        self.cuentas["gasto"].save(update_fields=["activa"])
        with self.assertRaises(ValidationError):
            form.save()
        self.configuracion.refresh_from_db()
        self.assertTrue(self.configuracion.isv_compras_recuperable)
        for campo in CAMPOS_CONFIG.values():
            self.assertIsNone(getattr(self.configuracion, campo + "_id"))
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa).exists())

    def test_no_guarda_enlaces_si_se_desinstalo_catalogo_desde_validacion(self):
        form = self._form(self._datos_completos())
        self.assertTrue(form.is_valid(), form.errors)
        ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).update(catalogo_rumi_instalado=False)
        with self.assertRaisesMessage(ValidationError, "Instale el catálogo RUMI"):
            form.save()
        self.assertFalse(VinculoCuentaOperativaRumi.objects.exists())

    def test_rol_con_campo_config_y_vinculo_alternativo_se_muestra_y_puede_quedar_pendiente(self):
        cuenta = self.cuentas["activo"]
        VinculoCuentaOperativaRumi.objects.create(empresa=self.empresa, clave="caja", cuenta=cuenta)
        self.assertIsNone(self.configuracion.cuenta_caja_id)
        form = self._form()
        self.assertEqual(form.initial["rol_caja"], cuenta.pk)
        self.assertEqual(resolver_cuenta_rumi(self.empresa, "caja").pk, cuenta.pk)
        form = self._form({"isv_compras_recuperable": "on", "rol_caja": ""})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.configuracion.refresh_from_db()
        self.assertIsNone(self.configuracion.cuenta_caja_id)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa, clave="caja").exists())
        with self.assertRaisesMessage(ValidationError, "Configure la cuenta"):
            resolver_cuenta_rumi(self.empresa, "caja")

    def test_rol_con_campo_config_migra_vinculo_alternativo_al_guardar_eleccion(self):
        anterior = self.cuentas["activo"]
        elegida = CuentaContable.objects.create(
            empresa=self.empresa, codigo="CAJA-ELEGIDA", nombre="Caja elegida", tipo="activo",
        )
        VinculoCuentaOperativaRumi.objects.create(empresa=self.empresa, clave="caja", cuenta=anterior)
        form = self._form({"isv_compras_recuperable": "on", "rol_caja": elegida.pk})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.configuracion.refresh_from_db()
        self.assertEqual(self.configuracion.cuenta_caja_id, elegida.pk)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa, clave="caja").exists())
        self.assertEqual(resolver_cuenta_rumi(self.empresa, "caja").pk, elegida.pk)
