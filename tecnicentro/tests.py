from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from facturacion.models import Cliente

from .models import DiagnosticoVehicular, HistorialEstadoOrden, OrdenServicio, Vehiculo


class TecnicentroTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Taller Futuro", slug="taller-futuro", rtn="08011999007777", estado_licencia="activa"
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
        return OrdenServicio.objects.get(empresa=self.empresa)

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
