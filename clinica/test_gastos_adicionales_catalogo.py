"""Regresiones de los selectores GA y de su excepción de catálogo acotada."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from facturacion.models import Factura, Producto, TipoImpuesto

from .catalogo_gastos_adicionales import productos_gastos_adicionales
from .forms_gastos_adicionales import validar_lineas_gasto
from .models import GastoAdicional, LineaGastoAdicional, Paciente


class GastoAdicionalCatalogoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.spa = Empresa.objects.create(nombre="Medical Spa", slug="medical_spa", rtn="GAC-1")
        cls.hospital = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GAC-2")
        cls.empresa = Empresa.objects.create(nombre="Servicios Médicos", slug="serviciosmedicos", rtn="GAC-3")
        cls.luque = Empresa.objects.create(nombre="Luque Aestetic", slug="luque_aestetic", rtn="GAC-4")
        cls.ajena = Empresa.objects.create(nombre="Otra empresa", slug="otro_catalogo_ga", rtn="GAC-5")
        modulo, _ = Modulo.objects.get_or_create(codigo="gastos_adicionales", defaults={"nombre": "Gastos Adicionales"})
        for empresa in (cls.spa, cls.hospital, cls.empresa, cls.luque):
            EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo)
        cls.usuario = Usuario.objects.create_superuser(username="ga_catalogo", password=None, empresa=cls.empresa)
        cls.paciente = Paciente.objects.create(
            empresa=cls.empresa, nombre="María del Carmen", identidad="0801222212345", expediente_codigo="GAC-001",
            telefono="8888 1234", whatsapp="+504 9999-5678", celular_2="(7777) 4321",
        )
        cls.paciente_otro = Paciente.objects.create(empresa=cls.spa, nombre="María externa", identidad="0801111100000", expediente_codigo="GAC-EXTERNO", telefono="66661234")
        cls.isv = TipoImpuesto.objects.create(nombre="ISV catálogo GA", porcentaje="15.00")
        cls.exento = TipoImpuesto.objects.create(nombre="Exento catálogo GA", porcentaje="0.00")
        cls.productos = {}
        for indice, empresa in enumerate((cls.spa, cls.hospital, cls.empresa, cls.luque, cls.ajena), start=1):
            cls.productos[empresa.slug] = Producto.objects.create(
                empresa=empresa, nombre=f"Bot tratamiento {indice}", codigo=f"GAC-{indice}",
                descripcion=f"Aplicación clínica compartida {indice}", precio="115.00",
                tipo_item="servicio", controla_inventario=False, impuesto_predeterminado=cls.isv,
            )

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, nombre, gasto=None, empresa=None):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if gasto:
            kwargs["gasto_id"] = gasto.pk
        return reverse(nombre, kwargs=kwargs)

    def datos(self, producto=None, **overrides):
        data = {
            "paciente": self.paciente.pk, "fecha": timezone.localdate().isoformat(),
            "profesional": "", "tipo_cirugia": "rinoplastia", "observacion": "Detalle clínico", "accion": "guardar",
            "lineas": json.dumps([{
                "producto_id": (producto or self.productos["medical_spa"]).pk,
                "cantidad": "2.00", "precio_unitario": "115.00",
            }]),
        }
        data.update(overrides)
        return data

    def crear(self, **overrides):
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(**overrides))
        self.assertEqual(response.status_code, 302)
        return GastoAdicional.objects.get(empresa=self.empresa)

    def test_catalogo_consulta_ambos_origenes_y_catalogo_local_sin_crear_registros(self):
        empresas_antes, productos_antes = Empresa.objects.count(), Producto.objects.count()
        origenes = {"medical_spa", "hospital_mia"}
        for empresa in (self.spa, self.hospital, self.empresa, self.luque):
            with self.subTest(empresa=empresa.slug):
                response = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar", empresa=empresa))
                self.assertEqual(response.status_code, 200)
                self.assertEqual({producto["empresa_slug"] for producto in response.json()["results"]}, origenes | {empresa.slug})
        self.assertEqual(Empresa.objects.count(), empresas_antes)
        self.assertEqual(Producto.objects.count(), productos_antes)
        for slug, producto in self.productos.items():
            producto.refresh_from_db()
            self.assertEqual(producto.empresa.slug, slug)

    def test_empresa_sin_excepcion_solo_puede_consultar_su_catalogo(self):
        self.assertEqual(list(productos_gastos_adicionales(self.ajena)), [self.productos[self.ajena.slug]])
        paciente = Paciente.objects.create(empresa=self.ajena, nombre="Paciente ajeno", expediente_codigo="AJENO")
        gasto = GastoAdicional.objects.create(empresa=self.ajena, paciente=paciente)
        with self.assertRaises(ValidationError):
            LineaGastoAdicional.objects.create(gasto=gasto, producto=self.productos["medical_spa"], cantidad=1, precio_unitario=115)

    def test_productos_buscan_un_caracter_nombre_codigo_descripcion_y_origen(self):
        for query, ids in (
            ("b", {producto.pk for slug, producto in self.productos.items() if slug in {"medical_spa", "hospital_mia", self.empresa.slug}}),
            ("Bot tratamiento 1", {self.productos["medical_spa"].pk}),
            ("GAC-2", {self.productos["hospital_mia"].pk}),
            ("compartida 3", {self.productos[self.empresa.slug].pk}),
            ("Hospital MIA", {self.productos["hospital_mia"].pk}),
            ("medical_spa", {self.productos["medical_spa"].pk}),
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar"), {"q": query})
                self.assertEqual({producto["id"] for producto in response.json()["results"]}, ids)

    def test_payload_conserva_precio_origen_e_impuesto_vigente(self):
        producto = self.productos["medical_spa"]
        response = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar"), {"q": producto.codigo})
        dato = response.json()["results"][0]
        self.assertEqual((dato["id"], dato["precio"], dato["empresa_slug"], dato["empresa_nombre"]), (producto.pk, "115.00", "medical_spa", self.spa.nombre))
        self.assertEqual((dato["impuesto_id"], dato["impuesto_porcentaje"], dato["impuesto_activo"]), (self.isv.pk, "15.00", True))
        producto.impuesto_predeterminado = self.exento
        producto.save()
        dato = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar"), {"q": producto.codigo}).json()["results"][0]
        self.assertEqual((dato["impuesto_id"], dato["impuesto_porcentaje"]), (self.exento.pk, "0.00"))
        producto.impuesto_predeterminado = None
        producto.save()
        dato = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar"), {"q": producto.codigo}).json()["results"][0]
        self.assertIsNone(dato["impuesto_id"])
        self.assertIsNone(dato["impuesto_porcentaje"])
        self.assertFalse(dato["impuesto_activo"])

    def test_pacientes_iniciales_y_filtro_vivo_permanecen_locales(self):
        for query in ("", "M", "Mar", "08012222", "88881234", "99995678", "77774321", "GAC-001"):
            with self.subTest(query=query):
                datos = self.client.get(self.url("clinica_gastos_adicionales_pacientes_buscar"), {"q": query}).json()
                self.assertEqual([paciente["id"] for paciente in datos["results"]], [self.paciente.pk])
        self.assertEqual(self.client.get(self.url("clinica_gastos_adicionales_pacientes_buscar"), {"q": "66661234"}).json()["results"], [])
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(paciente=self.paciente_otro.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GastoAdicional.objects.exists())

    def test_paginacion_hace_accesibles_todos_productos_y_pacientes(self):
        for indice in range(43):
            Producto.objects.create(empresa=self.spa, nombre=f"Paginado {indice:02d}", precio=100, impuesto_predeterminado=self.isv)
            Paciente.objects.create(empresa=self.empresa, nombre=f"Paginado {indice:02d}", expediente_codigo=f"PAG-{indice:02d}")
        for route in ("clinica_gastos_adicionales_productos_buscar", "clinica_gastos_adicionales_pacientes_buscar"):
            with self.subTest(route=route):
                primera = self.client.get(self.url(route), {"q": "Paginado"}).json()
                segunda = self.client.get(self.url(route), {"q": "Paginado", "page": primera["next_page"]}).json()
                self.assertEqual((primera["page"], len(primera["results"]), primera["total"], primera["has_more"]), (1, 40, 43, True))
                self.assertEqual((segunda["page"], len(segunda["results"]), segunda["total"], segunda["has_more"], segunda["next_page"]), (2, 3, 43, False, None))
                self.assertEqual(len({dato["id"] for dato in primera["results"] + segunda["results"]}), 43)

    def test_guardar_y_editar_producto_compartido_no_duplica_y_conserva_documento_ga(self):
        productos_antes, empresas_antes = Producto.objects.count(), Empresa.objects.count()
        gasto = self.crear()
        self.assertEqual(gasto.numero, "GA-000001")
        self.assertEqual(gasto.total, Decimal("230.00"))
        self.assertEqual(gasto.lineas.get().producto, self.productos["medical_spa"])
        response = self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(
            producto=self.productos["hospital_mia"], observacion="Observación actualizada",
        ))
        self.assertEqual(response.status_code, 302)
        gasto.refresh_from_db()
        self.assertEqual(gasto.numero, "GA-000001")
        self.assertEqual(gasto.lineas.get().producto, self.productos["hospital_mia"])
        self.assertEqual(gasto.observacion, "Observación actualizada")
        self.assertEqual((Producto.objects.count(), Empresa.objects.count()), (productos_antes, empresas_antes))
        self.assertFalse(Factura.objects.exists())

    def test_producto_de_otro_catalogo_rechazado_en_post_y_modelo(self):
        for slug in (self.luque.slug, self.ajena.slug):
            with self.subTest(slug=slug):
                producto = self.productos[slug]
                response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(producto=producto))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["errores_lineas"])
                self.assertEqual(response.context["lineas_iniciales"], [])
                self.assertFalse(GastoAdicional.objects.exists())
        gasto = self.crear()
        with self.assertRaises(ValidationError):
            LineaGastoAdicional.objects.create(gasto=gasto, producto=self.productos[self.ajena.slug], cantidad=1, precio_unitario=115)

    def test_post_invalido_preserva_catalogo_autorizado_y_metadatos(self):
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(fecha="fecha inválida"))
        self.assertEqual(response.status_code, 200)
        linea = response.context["lineas_iniciales"][0]
        self.assertEqual((linea["producto_id"], linea["cantidad"], linea["precio_unitario"]), (self.productos["medical_spa"].pk, "2.00", "115.00"))
        self.assertEqual((linea["empresa_slug"], linea["impuesto_porcentaje"]), ("medical_spa", "15.00"))
        self.assertFalse(GastoAdicional.objects.exists())

    def test_inactivos_no_ofrecidos_ni_aceptados_excepto_originales_en_edicion(self):
        gasto = self.crear()
        producto = self.productos["medical_spa"]
        producto.activo = False
        producto.save()
        self.assertNotIn(producto.pk, [dato["id"] for dato in self.client.get(self.url("clinica_gastos_adicionales_productos_buscar")).json()["results"]])
        with self.assertRaises(ValidationError):
            validar_lineas_gasto(self.datos()["lineas"], self.empresa)
        self.assertEqual(validar_lineas_gasto(self.datos()["lineas"], self.empresa, gasto=gasto)[0]["producto"], producto)
        response = self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(observacion="Conservar original inactivo"))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(self.url("clinica_gasto_adicional_editar", gasto))
        self.assertEqual(response.context["lineas_iniciales"][0]["producto_id"], producto.pk)

    def test_contexto_resumen_usa_snapshot_del_gasto_y_permiso_actual_ga(self):
        with patch("facturacion.views._precios_incluyen_impuesto", return_value=True):
            gasto = self.crear()
        self.assertTrue(gasto.precio_incluye_impuesto)
        with patch("facturacion.views._precios_incluyen_impuesto", return_value=False):
            response = self.client.get(self.url("clinica_gasto_adicional_editar", gasto))
            self.assertTrue(response.context["precios_incluyen_impuesto"])
            self.assertTrue(response.context["puede_editar_precio"])
            response = self.client.get(self.url("clinica_gasto_adicional_crear"))
            self.assertFalse(response.context["precios_incluyen_impuesto"])
