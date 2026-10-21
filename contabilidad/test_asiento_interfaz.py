"""El nuevo formulario conserva los datos y las protecciones del diario."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .forms import AsientoContableForm, LineaAsientoFormSet
from .models import AsientoContable, CuentaContable, LineaAsientoContable


class AsientoInterfazTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(nombre="Plan diario", codigo="plan-diario-interfaz", precio_mensual="199.00")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa del diario", slug="diario-interfaz", rtn="08011999177051",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra = Empresa.objects.create(
            nombre="Otra empresa del diario", slug="otro-diario-interfaz", rtn="08011999177052",
            estado_licencia="activa", plan_comercial=plan,
        )
        rol = RolSistema.objects.create(
            nombre="Contador del diario", codigo="contador-diario-interfaz", puede_contabilidad=True,
            puede_crear_asientos=True, puede_contabilizar_asientos=True,
        )
        cls.usuario = Usuario.objects.create_user(username="contador-diario-interfaz", empresa=cls.empresa, rol_sistema=rol)
        cls.caja = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="110201", tipo="activo",
            nombre="Banco de operaciones nacionales en lempiras para desembolsos de capital y recuperación de cartera de préstamos comerciales de clientes con actividad económica independiente",
        )
        cls.ingreso = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="510101", nombre="Intereses de préstamos", tipo="ingreso",
        )
        cls.inactiva = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="110202", nombre="Banco inactivo", tipo="activo", activa=False,
        )
        cls.agrupadora = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1", nombre="Activo agrupador", tipo="activo", acepta_movimientos=False,
        )
        cls.ajena = CuentaContable.objects.create(
            empresa=cls.otra, codigo="110201", nombre="Cuenta bancaria privada de otra empresa", tipo="activo",
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def ruta(self, nombre="crear_asiento_contable", asiento=None):
        args = [self.empresa.slug]
        if asiento:
            args.append(asiento.pk)
        return reverse(nombre, args=args)

    def datos(self, *, asiento=None, **cambios):
        datos = {
            "fecha": "2026-10-14", "descripcion": "Cobro de intereses de octubre",
            "referencia": "REC-014", "origen_modulo": "manual", "estado": "borrador",
            "lineas-TOTAL_FORMS": "2", "lineas-INITIAL_FORMS": "2" if asiento else "0",
            "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000",
            "lineas-0-cuenta": str(self.caja.pk), "lineas-0-detalle": "Ingreso en el banco",
            "lineas-0-debe": "1250.35", "lineas-0-haber": "0.00",
            "lineas-1-cuenta": str(self.ingreso.pk), "lineas-1-detalle": "Intereses cobrados",
            "lineas-1-debe": "0.00", "lineas-1-haber": "1250.35",
        }
        if asiento:
            for indice, linea in enumerate(asiento.lineas.order_by("id")):
                datos[f"lineas-{indice}-id"] = str(linea.pk)
        datos.update(cambios)
        return datos

    def crear_borrador(self):
        respuesta = self.client.post(self.ruta(), self.datos())
        self.assertEqual(respuesta.status_code, 302)
        return AsientoContable.objects.get(empresa=self.empresa)

    def test_nuevo_propone_fecha_local_sin_sobrescribir_fecha_editada_o_enviada(self):
        with patch("contabilidad.forms.timezone.localdate", return_value=date(2026, 10, 14)):
            nuevo = AsientoContableForm(instance=AsientoContable(empresa=self.empresa))
            self.assertEqual(nuevo["fecha"].value(), date(2026, 10, 14))
            enviado = AsientoContableForm({"fecha": "2026-09-30"}, empresa=self.empresa)
            self.assertEqual(enviado["fecha"].value(), "2026-09-30")
            vacio = AsientoContableForm({"fecha": ""}, empresa=self.empresa)
            self.assertEqual(vacio["fecha"].value(), "")
            editado = AsientoContableForm(instance=AsientoContable(
                empresa=self.empresa, fecha=date(2026, 9, 28), descripcion="Fecha propia",
            ))
            self.assertEqual(editado["fecha"].value(), date(2026, 9, 28))
            self.assertIsNone(nuevo["origen_modulo"].value())

    def test_formulario_y_partida_nueva_restringen_catalogo_y_conservan_nombre_largo(self):
        respuesta = self.client.get(self.ruta())
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.caja.nombre)
        self.assertNotContains(respuesta, self.ajena.nombre)
        self.assertNotContains(respuesta, self.inactiva.nombre)
        self.assertNotContains(respuesta, self.agrupadora.nombre)
        self.assertEqual({cuenta["id"] for cuenta in respuesta.context["cuentas_busqueda"]}, {self.caja.pk, self.ingreso.pk})
        formset = respuesta.context["formset"]
        for formulario in [*formset.forms, formset.empty_form, formset.empty_form]:
            self.assertEqual(set(formulario.fields["cuenta"].queryset.values_list("pk", flat=True)), {self.caja.pk, self.ingreso.pk})

    def test_empty_form_del_formset_sin_vista_tambien_pertenece_a_la_empresa(self):
        formset = LineaAsientoFormSet(instance=AsientoContable(empresa=self.empresa))
        self.assertEqual(set(formset.empty_form.fields["cuenta"].queryset.values_list("pk", flat=True)), {self.caja.pk, self.ingreso.pk})
        sin_empresa = LineaAsientoFormSet(instance=AsientoContable())
        self.assertFalse(sin_empresa.empty_form.fields["cuenta"].queryset.exists())

    def test_guardar_borrador_conserva_importes_decimales_origen_y_creador(self):
        asiento = self.crear_borrador()
        self.assertEqual(asiento.estado, "borrador")
        self.assertEqual(asiento.creado_por, self.usuario)
        self.assertEqual(asiento.empresa, self.empresa)
        self.assertEqual(asiento.total_debe, Decimal("1250.35"))
        self.assertEqual(asiento.total_haber, Decimal("1250.35"))
        self.assertEqual(asiento.origen_modulo, "manual")
        self.assertEqual(asiento.referencia, "REC-014")
        self.assertEqual(asiento.lineas.count(), 2)

    def test_accion_explicita_contabilizar_guarda_asiento_numerado(self):
        respuesta = self.client.post(self.ruta(), self.datos(contabilizar_ahora="1"))
        self.assertEqual(respuesta.status_code, 302)
        asiento = AsientoContable.objects.get(empresa=self.empresa)
        self.assertEqual(asiento.estado, "contabilizado")
        self.assertTrue(asiento.numero.startswith("ASI-"))
        self.assertTrue(asiento.esta_balanceado)

    def test_descuadre_retorna_formulario_con_datos_y_sin_escrituras(self):
        datos = self.datos(**{"lineas-1-haber": "1200.35"})
        respuesta = self.client.post(self.ruta(), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(AsientoContable.objects.exists())
        self.assertFalse(LineaAsientoContable.objects.exists())
        self.assertEqual(respuesta.context["form"]["descripcion"].value(), datos["descripcion"])
        self.assertEqual(respuesta.context["formset"].forms[0]["cuenta"].value(), str(self.caja.pk))
        self.assertEqual(respuesta.context["formset"].forms[1]["haber"].value(), "1200.35")
        self.assertContains(respuesta, "No se puede guardar el asiento")

    def test_dato_invalido_retorna_error_y_conserva_los_otros_campos(self):
        datos = self.datos(**{"lineas-0-debe": "importe inválido"})
        respuesta = self.client.post(self.ruta(), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("debe", respuesta.context["formset"].forms[0].errors)
        self.assertEqual(respuesta.context["formset"].forms[0]["debe"].value(), "importe inválido")
        self.assertEqual(respuesta.context["form"]["referencia"].value(), "REC-014")
        self.assertEqual(respuesta.context["formset"].forms[1]["haber"].value(), "1250.35")
        self.assertFalse(AsientoContable.objects.exists())

    def test_post_rechaza_cuenta_ajena_inactiva_o_agrupadora(self):
        for cuenta in (self.ajena, self.inactiva, self.agrupadora):
            with self.subTest(cuenta=cuenta.codigo, empresa=cuenta.empresa_id):
                respuesta = self.client.post(self.ruta(), self.datos(**{"lineas-0-cuenta": str(cuenta.pk)}))
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn("cuenta", respuesta.context["formset"].forms[0].errors)
                self.assertFalse(AsientoContable.objects.exists())

    def test_editar_con_eliminacion_de_partidas_ignora_importes_retirados(self):
        asiento = self.crear_borrador()
        ids_originales = list(asiento.lineas.values_list("pk", flat=True))
        datos = self.datos(asiento=asiento, **{
            "descripcion": "Asiento corregido", "lineas-TOTAL_FORMS": "4",
            "lineas-0-DELETE": "on", "lineas-1-DELETE": "on",
            "lineas-2-cuenta": str(self.caja.pk), "lineas-2-detalle": "Banco corregido",
            "lineas-2-debe": "925.48", "lineas-2-haber": "0.00",
            "lineas-3-cuenta": str(self.ingreso.pk), "lineas-3-detalle": "Interés corregido",
            "lineas-3-debe": "0.00", "lineas-3-haber": "925.48",
        })
        respuesta = self.client.post(self.ruta("editar_asiento_contable", asiento), datos)
        self.assertEqual(respuesta.status_code, 302)
        asiento.refresh_from_db()
        self.assertEqual(asiento.descripcion, "Asiento corregido")
        self.assertEqual(asiento.estado, "borrador")
        self.assertEqual(asiento.total_debe, Decimal("925.48"))
        self.assertEqual(asiento.total_haber, Decimal("925.48"))
        self.assertEqual(asiento.lineas.count(), 2)
        self.assertFalse(LineaAsientoContable.objects.filter(pk__in=ids_originales).exists())

    def test_edicion_invalida_no_modifica_el_registro_y_retiene_la_captura(self):
        asiento = self.crear_borrador()
        datos = self.datos(asiento=asiento, **{
            "descripcion": "Cambio pendiente de corregir", "lineas-1-haber": "30.00",
        })
        respuesta = self.client.post(self.ruta("editar_asiento_contable", asiento), datos)
        self.assertEqual(respuesta.status_code, 200)
        asiento.refresh_from_db()
        self.assertEqual(asiento.descripcion, "Cobro de intereses de octubre")
        self.assertEqual(asiento.total_haber, Decimal("1250.35"))
        self.assertEqual(respuesta.context["form"]["descripcion"].value(), "Cambio pendiente de corregir")
        self.assertEqual(respuesta.context["formset"].forms[1]["haber"].value(), "30.00")
