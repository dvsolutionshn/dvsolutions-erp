from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from facturacion.models import Cliente

from .models import CitaTaller, DiagnosticoVehicular, HistorialEstadoOrden, InspeccionRecepcion, OrdenServicio, Vehiculo


class TecnicentroTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Taller Futuro", slug="taller-futuro", rtn="08011999007777",
            estado_licencia="activa", tipo_solucion="tecnicentro",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="tecnicentro", defaults={"nombre": "Tecnicentro Vehicular"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        self.rol = RolSistema.objects.create(
            nombre="Jefe de Taller", codigo="jefe-taller",
            puede_tecnicentro=True, puede_recepcion_taller=True,
            puede_diagnostico_taller=True, puede_operacion_taller=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="jefe-taller", password="pass12345", empresa=self.empresa, rol_sistema=self.rol
        )
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre="Cliente Vehicular", telefono="99999999", activo=True
        )
        self.client.login(username="jefe-taller", password="pass12345")

    def _crear_orden(self):
        response = self.client.post(reverse("tecnicentro_recepcion", args=[self.empresa.slug]), {
            "cliente": self.cliente.id,
            "placa": "haa 1234",
            "marca": "Toyota",
            "modelo": "Hilux",
            "anio": "2022",
            "color": "Gris",
            "tipo_vehiculo": "pickup",
            "combustible": "diesel",
            "kilometraje_entrada": "45000",
            "nivel_combustible": "medio",
            "motivo_ingreso": "Ruido en suspension delantera",
            "observaciones_recepcion": "Rayon leve en puerta derecha",
            "prioridad": "normal",
            "tiempo_espera_estimado_min": "25",
            "tiempo_reparacion_estimado_min": "90",
            "deja_vehiculo": "on",
            "autoriza_whatsapp": "on",
        })
        self.assertEqual(response.status_code, 302)
        return OrdenServicio.objects.filter(empresa=self.empresa).latest("id")

    def test_dashboard_tecnicentro_responde_con_interfaz_especializada(self):
        response = self.client.get(reverse("tecnicentro_dashboard", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Garage Operations System")
        self.assertContains(response, "Taller en tiempo real")

    def test_recepcion_crea_vehiculo_orden_y_trazabilidad(self):
        orden = self._crear_orden()
        vehiculo = Vehiculo.objects.get(empresa=self.empresa, placa="HAA 1234")
        self.assertEqual(orden.vehiculo, vehiculo)
        self.assertEqual(orden.estado, "espera")
        self.assertEqual(orden.kilometraje_entrada, 45000)
        self.assertTrue(orden.numero.startswith("OT-"))
        self.assertTrue(HistorialEstadoOrden.objects.filter(orden=orden, estado="espera").exists())

    def test_diagnostico_y_cambio_de_estado_actualizan_orden(self):
        orden = self._crear_orden()
        response = self.client.post(reverse("tecnicentro_diagnostico", args=[self.empresa.slug, orden.id]), {
            "sintomas_reportados": "Ruido al girar",
            "hallazgos": "Terminal de direccion con juego",
            "causa_probable": "Desgaste por kilometraje",
            "recomendaciones": "Cambiar terminal y alinear",
            "requiere_prueba_ruta": "on",
            "estado": "completado",
        })
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, "diagnostico")
        self.assertTrue(DiagnosticoVehicular.objects.filter(orden=orden, estado="completado").exists())

        response = self.client.post(
            reverse("tecnicentro_cambiar_estado", args=[self.empresa.slug, orden.id]),
            {"estado": "reparacion", "nota": "Cotizacion aprobada por cliente"},
        )
        self.assertEqual(response.status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, "reparacion")

    def test_rol_sin_permiso_no_puede_entrar_al_tecnicentro(self):
        rol = RolSistema.objects.create(nombre="Sin Taller", codigo="sin-taller")
        usuario = Usuario.objects.create_user(
            username="sin-taller", password="pass12345", empresa=self.empresa, rol_sistema=rol
        )
        self.client.logout()
        self.client.login(username="sin-taller", password="pass12345")
        response = self.client.get(reverse("tecnicentro_dashboard", args=[self.empresa.slug]))
        self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]))

    def test_login_exclusivo_tecnicentro_usa_mismas_credenciales(self):
        self.client.logout()
        login_url = reverse("tecnicentro_login", args=[self.empresa.slug])
        response = self.client.get(login_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GARAGE")
        self.assertContains(response, "Acceso exclusivo al tecnicentro")

        response = self.client.post(login_url, {
            "username": "jefe-taller",
            "password": "pass12345",
        })
        self.assertRedirects(response, reverse("tecnicentro_dashboard", args=[self.empresa.slug]))

    def test_acceso_directo_sin_sesion_redirige_al_login_del_taller(self):
        self.client.logout()
        response = self.client.get(reverse("tecnicentro_dashboard", args=[self.empresa.slug]))
        self.assertRedirects(response, reverse("tecnicentro_login", args=[self.empresa.slug]))

    def test_enlace_principal_empresa_tecnicentro_abre_garage_os(self):
        self.client.logout()
        response = self.client.get(reverse("empresa_login", args=[self.empresa.slug]))
        self.assertRedirects(response, reverse("tecnicentro_login", args=[self.empresa.slug]))

    def test_agenda_convierte_cita_en_recepcion(self):
        from django.utils import timezone

        response = self.client.post(reverse("tecnicentro_agenda", args=[self.empresa.slug]), {
            "cliente": self.cliente.id,
            "fecha_hora": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "servicio_solicitado": "Mantenimiento preventivo",
            "duracion_estimada_min": "90",
            "estado": "confirmada",
            "observaciones": "Cliente espera en sala",
        })
        self.assertEqual(response.status_code, 302)
        cita = CitaTaller.objects.get(empresa=self.empresa)
        agenda_response = self.client.get(reverse("tecnicentro_agenda", args=[self.empresa.slug]))
        self.assertContains(agenda_response, "Mantenimiento preventivo")
        response = self.client.post(reverse("tecnicentro_recepcion", args=[self.empresa.slug]) + f"?cita={cita.id}", {
            "cliente": self.cliente.id, "placa": "PAA 9001", "marca": "Honda", "modelo": "CR-V",
            "anio": "2021", "color": "Negro", "tipo_vehiculo": "suv", "combustible": "gasolina",
            "kilometraje_entrada": "30000", "nivel_combustible": "medio", "motivo_ingreso": "Mantenimiento preventivo",
            "observaciones_recepcion": "Sin daños", "prioridad": "normal", "tiempo_reparacion_estimado_min": "90",
            "deja_vehiculo": "on", "autoriza_whatsapp": "on",
        })
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, "atendida")
        self.assertIsNotNone(cita.orden_id)

    def test_inspeccion_recepcion_queda_en_trazabilidad(self):
        orden = self._crear_orden()
        response = self.client.post(reverse("tecnicentro_inspeccion", args=[self.empresa.slug, orden.id]), {
            "carroceria": "observaciones", "llantas": "desgaste", "parabrisas": "bueno",
            "porta_documentos": "on", "llanta_repuesto": "on", "herramientas": "on",
            "danos_existentes": "Rayón en puerta derecha", "aceptacion_cliente": "on",
            "nombre_aceptante": "Cliente Vehicular",
        })
        self.assertEqual(response.status_code, 302)
        inspeccion = InspeccionRecepcion.objects.get(orden=orden)
        self.assertTrue(inspeccion.aceptacion_cliente)
        self.assertTrue(HistorialEstadoOrden.objects.filter(orden=orden, nota__icontains="Inspección").exists())
        self.assertContains(self.client.get(reverse("tecnicentro_inspeccion", args=[self.empresa.slug, orden.id])), "Cliente Vehicular")

    def test_espera_de_recepcion_se_calcula_sin_captura_manual(self):
        primera = self._crear_orden()
        self.assertGreaterEqual(primera.tiempo_espera_estimado_min, 0)
        primera.tiempo_reparacion_estimado_min = 120
        primera.save(update_fields=["tiempo_reparacion_estimado_min"])
        self._crear_orden()
        segunda = OrdenServicio.objects.filter(empresa=self.empresa).order_by("-id").first()
        self.assertGreaterEqual(segunda.tiempo_espera_estimado_min, primera.tiempo_espera_estimado_min)

    def test_recepcion_permite_crear_propietario_sin_salir_del_garage(self):
        response = self.client.post(reverse("tecnicentro_recepcion", args=[self.empresa.slug]), {
            "nuevo_cliente_nombre": "Nuevo Propietario", "nuevo_cliente_telefono": "99887766", "nuevo_cliente_rtn": "",
            "placa": "HAB 2020", "marca": "Mazda", "modelo": "CX-5", "anio": "2020", "color": "Rojo",
            "tipo_vehiculo": "suv", "combustible": "gasolina", "kilometraje_entrada": "51000",
            "nivel_combustible": "cuarto", "motivo_ingreso": "Revisión general", "observaciones_recepcion": "",
            "prioridad": "normal", "tiempo_reparacion_estimado_min": "60", "deja_vehiculo": "on", "autoriza_whatsapp": "on",
        })
        self.assertEqual(response.status_code, 302)
        cliente = Cliente.objects.get(empresa=self.empresa, nombre="Nuevo Propietario")
        self.assertEqual(cliente.telefono_whatsapp, "99887766")
        self.assertTrue(OrdenServicio.objects.filter(empresa=self.empresa, cliente=cliente).exists())
