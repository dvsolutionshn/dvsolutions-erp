"""Concurrent connection/test completion must not validate another snapshot."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from .models import ConfiguracionCRM
from .views import conectar_whatsapp_meta


@override_settings(META_WHATSAPP_APP_SECRET="synthetic-secret", META_WHATSAPP_VERIFY_TOKEN="synthetic-verify")
class WhatsAppTestCompletionRaceTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="QA conexion", slug="qa-conexion", rtn="QA-RACE-1")
        modulo, _ = Modulo.objects.get_or_create(codigo="crm_marketing", defaults={"nombre": "CRM QA"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        self.user = Usuario.objects.create_superuser(username="qa-wa-race", email="qa@example.invalid", password="synthetic")
        self.client.force_login(self.user)
        self.config = ConfiguracionCRM.objects.create(
            empresa=self.empresa, whatsapp_activo=False, whatsapp_pendiente_validacion=True,
            whatsapp_business_account_id="account-initial", whatsapp_phone_number_id="phone-initial",
            whatsapp_token="token-initial", whatsapp_numero_prueba="50499999999",
            whatsapp_plantilla_prueba="prueba_sin_variables", whatsapp_prueba_message_id="wamid.previous",
        )
        self.url = reverse("crm_enviar_prueba_whatsapp", args=[self.empresa.slug])

    def complete_send_after_change(self, changes):
        def sender(*args, **kwargs):
            ConfiguracionCRM.objects.filter(pk=self.config.pk).update(**changes)
            return {"messages": [{"id": "wamid.finished-old-request"}]}
        return sender

    @patch("crm.meta_onboarding.validate_template")
    def test_reconnection_while_sending_does_not_store_old_test_on_new_connection(self, validate):
        replacement = {"whatsapp_phone_number_id": "phone-new", "whatsapp_business_account_id": "account-new",
                       "whatsapp_token": "token-new", "whatsapp_prueba_message_id": ""}
        with patch("crm.views.enviar_plantilla_whatsapp", side_effect=self.complete_send_after_change(replacement)):
            response = self.client.post(self.url, {"autorizar_prueba": "si"}, follow=True)
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_phone_number_id, "phone-new")
        self.assertEqual(self.config.whatsapp_prueba_message_id, "")
        self.assertFalse(self.config.whatsapp_activo)
        self.assertTrue(self.config.whatsapp_pendiente_validacion)
        self.assertContains(response, "La configuracion cambio mientras Meta enviaba la prueba")

    @patch("crm.meta_onboarding.validate_template")
    def test_another_test_finishing_first_keeps_its_receipt(self, validate):
        with patch("crm.views.enviar_plantilla_whatsapp", side_effect=self.complete_send_after_change(
            {"whatsapp_prueba_message_id": "wamid.newer-test"}
        )):
            self.client.post(self.url, {"autorizar_prueba": "si"})
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_prueba_message_id, "wamid.newer-test")

    @patch("crm.meta_onboarding.validate_template")
    def test_recipient_change_while_sending_does_not_validate_new_recipient(self, validate):
        with patch("crm.views.enviar_plantilla_whatsapp", side_effect=self.complete_send_after_change(
            {"whatsapp_numero_prueba": "50498888888", "whatsapp_prueba_message_id": ""}
        )):
            self.client.post(self.url, {"autorizar_prueba": "si"})
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_prueba_message_id, "")


@skipUnless(connection.vendor == "postgresql", "Requiere locks de filas PostgreSQL; SQLite no los implementa.")
@override_settings(META_WHATSAPP_APP_SECRET="synthetic-secret", META_WHATSAPP_VERIFY_TOKEN="synthetic-verify")
class WhatsAppAssignmentPostgresRaceTests(TransactionTestCase):
    """Run only against an isolated PostgreSQL test database, never production."""

    def setUp(self):
        self.first = Empresa.objects.create(nombre="Primera QA", slug="first-race-qa", rtn="QA-RACE-PG-1")
        self.second = Empresa.objects.create(nombre="Segunda QA", slug="second-race-qa", rtn="QA-RACE-PG-2")
        self.user = Usuario.objects.create_superuser(username="qa-race-pg", email="qa@example.invalid", password="synthetic")

    def test_two_companies_cannot_assign_the_same_phone_concurrently(self):
        barrier = threading.Barrier(2, timeout=10)

        def connect(*args, **kwargs):
            barrier.wait()
            return {"phone_number_id": "shared-phone", "business_account_id": "shared-account",
                    "display_phone_number": "+504 9999-9999", "access_token": "synthetic-token"}

        def request_for(slug):
            close_old_connections()
            try:
                request = RequestFactory().post("/", data=json.dumps({"code": "synthetic", "waba_id": "shared-account"}),
                                                content_type="application/json")
                request.user = self.user
                return conectar_whatsapp_meta(request, slug).status_code
            finally:
                close_old_connections()

        with patch("crm.meta_onboarding.complete_connection", side_effect=connect), ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(request_for, [self.first.slug, self.second.slug]))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(ConfiguracionCRM.objects.filter(whatsapp_phone_number_id="shared-phone").count(), 1)
