"""Synthetic callback fixtures only; no real Meta requests or patient data."""
from datetime import timedelta
import hashlib
import hmac
import json

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import Empresa
from .models import (
    CitaCliente, ConfiguracionCRM, MensajeWhatsAppWebhook, NotificacionCitaWhatsApp,
)
from .whatsapp_webhooks import whatsapp_webhook


@override_settings(META_WHATSAPP_APP_SECRET="synthetic-secret", META_WHATSAPP_VERIFY_TOKEN="synthetic-verify")
class WhatsAppWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Webhook QA", slug="webhook-qa", rtn="QA-WH-1")
        cls.other = Empresa.objects.create(nombre="Webhook other QA", slug="webhook-other-qa", rtn="QA-WH-2")
        cls.config = ConfiguracionCRM.objects.create(
            empresa=cls.empresa, whatsapp_phone_number_id="phone-qa", whatsapp_business_account_id="account-qa")
        cls.other_config = ConfiguracionCRM.objects.create(
            empresa=cls.other, whatsapp_phone_number_id="phone-other", whatsapp_business_account_id="account-other")

    def setUp(self):
        self.factory = RequestFactory()

    def envelope(self, *, message=None, status=None, account="account-qa", phone="phone-qa"):
        value = {"metadata": {"phone_number_id": phone}}
        if message is not None:
            value["messages"] = [message]
        if status is not None:
            value["statuses"] = [status]
        return {"object": "whatsapp_business_account", "entry": [{"id": account,
            "changes": [{"field": "messages", "value": value}]}]}

    def post(self, payload, *, signature=None):
        body = json.dumps(payload).encode()
        digest = "sha256=" + hmac.new(b"synthetic-secret", body, hashlib.sha256).hexdigest()
        request = self.factory.post("/webhook/", data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature if signature is not None else digest)
        return whatsapp_webhook(request)

    def incoming(self, message_id="wamid.incoming.qa"):
        return {"id": message_id, "from": "50400000000", "timestamp": "1793448000",
            "type": "text", "text": {"body": "Confirmo mi cita QA"}}

    def test_challenge_is_exact_plain_text_and_requires_matching_token(self):
        request = self.factory.get("/webhook/", {"hub.mode": "subscribe", "hub.verify_token": "synthetic-verify",
            "hub.challenge": "challenge-123"})
        response = whatsapp_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"challenge-123")
        self.assertEqual(response["Content-Type"], "text/plain")
        request.GET = request.GET.copy()
        request.GET["hub.verify_token"] = "incorrect"
        self.assertEqual(whatsapp_webhook(request).status_code, 403)

    @override_settings(META_WHATSAPP_VERIFY_TOKEN="")
    def test_challenge_fails_closed_with_empty_configuration(self):
        request = self.factory.get("/webhook/", {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
        self.assertEqual(whatsapp_webhook(request).status_code, 503)

    def test_missing_or_invalid_signature_cannot_persist_messages(self):
        payload = self.envelope(message=self.incoming())
        for signature in ("", "sha256=" + "0" * 64, "sha256=not-valid", "invalid-ñ"):
            with self.subTest(signature=signature):
                self.assertEqual(self.post(payload, signature=signature).status_code, 403)
        self.assertFalse(MensajeWhatsAppWebhook.objects.exists())

    @override_settings(META_WHATSAPP_APP_SECRET="")
    def test_post_requires_server_secret(self):
        self.assertEqual(self.post(self.envelope(message=self.incoming())).status_code, 503)
        self.assertFalse(MensajeWhatsAppWebhook.objects.exists())

    @override_settings(META_WHATSAPP_WEBHOOK_MAX_BYTES=32)
    def test_body_limit_rejects_oversized_payload(self):
        self.assertEqual(self.post(self.envelope(message=self.incoming())).status_code, 413)
        self.assertFalse(MensajeWhatsAppWebhook.objects.exists())

    def test_signed_message_persists_in_its_company_and_is_idempotent(self):
        payload = self.envelope(message=self.incoming())
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(MensajeWhatsAppWebhook.objects.count(), 1)
        message = MensajeWhatsAppWebhook.objects.get()
        self.assertEqual(message.empresa_id, self.empresa.id)
        self.assertEqual(message.direccion, "entrante")
        self.assertEqual(message.texto, "Confirmo mi cita QA")
        self.assertEqual(message.estado, "received")

    def test_account_phone_mismatch_cannot_route_by_customer_phone(self):
        for account, phone in (("account-qa", "phone-other"), ("account-other", "phone-qa"), ("unknown", "phone-qa")):
            self.assertEqual(self.post(self.envelope(message=self.incoming(), account=account, phone=phone)).status_code, 200)
        self.assertFalse(MensajeWhatsAppWebhook.objects.exists())

    def test_duplicate_asset_configuration_is_not_assigned_to_either_company(self):
        self.other_config.whatsapp_business_account_id = "account-qa"
        self.other_config.whatsapp_phone_number_id = "phone-qa"
        self.other_config.save()
        self.assertEqual(self.post(self.envelope(message=self.incoming())).status_code, 200)
        self.assertFalse(MensajeWhatsAppWebhook.objects.exists())

    def test_arbitrary_text_does_not_confirm_an_appointment(self):
        cita = CitaCliente.objects.create(empresa=self.empresa, fecha_hora=timezone.now() + timedelta(days=1),
            titulo="Consulta QA", estado="pendiente")
        self.post(self.envelope(message=self.incoming()))
        cita.refresh_from_db()
        self.assertEqual(cita.estado, "pendiente")

    def test_signed_malformed_nested_content_is_ignored_without_crash(self):
        message = self.incoming()
        message["text"] = "malformed"
        self.assertEqual(self.post(self.envelope(message=message)).status_code, 200)
        self.assertEqual(MensajeWhatsAppWebhook.objects.get().texto, "")
        self.assertEqual(self.post({"object": "whatsapp_business_account", "entry": [None, {"id": "account-qa", "changes": "invalid"}]}).status_code, 200)

    def test_status_receipts_persist_without_prior_message_and_never_regress(self):
        def status(state, timestamp="1793448000"):
            return self.envelope(status={"id": "wamid.sent.qa", "recipient_id": "50400000000",
                "status": state, "timestamp": timestamp})
        self.post(status("read"))
        self.post(status("sent", "1793448001"))
        self.post(status("failed", "1793448002"))
        self.post(status("delivered", "1793448003"))
        message = MensajeWhatsAppWebhook.objects.get()
        self.assertEqual(message.direccion, "saliente")
        self.assertEqual(message.estado, "read")
        self.assertEqual(MensajeWhatsAppWebhook.objects.count(), 1)

    def test_failed_receipt_cannot_override_delivery_evidence(self):
        for state in ("sent", "failed", "delivered", "failed"):
            self.post(self.envelope(status={"id": "wamid.delivery.qa", "status": state, "timestamp": "1793448000"}))
        self.assertEqual(MensajeWhatsAppWebhook.objects.get().estado, "delivered")

    def test_notification_retains_api_accepted_state_and_gets_delivery_metadata(self):
        cita = CitaCliente.objects.create(empresa=self.empresa, fecha_hora=timezone.now() + timedelta(days=1),
            titulo="Consulta QA")
        notification = NotificacionCitaWhatsApp.objects.create(cita=cita, tipo="confirmacion", programada_para=timezone.now(),
            estado="enviado", respuesta={"messages": [{"id": "wamid.notification.qa"}]})
        self.post(self.envelope(status={"id": "wamid.notification.qa", "status": "delivered", "timestamp": "1793448000"}))
        notification.refresh_from_db()
        self.assertEqual(notification.estado, "enviado")
        self.assertEqual(notification.respuesta["webhook"]["status"], "delivered")

    def test_untrusted_status_does_not_update_another_company_notification(self):
        cita = CitaCliente.objects.create(empresa=self.other, fecha_hora=timezone.now() + timedelta(days=1),
            titulo="Otra consulta QA")
        notification = NotificacionCitaWhatsApp.objects.create(cita=cita, tipo="confirmacion", programada_para=timezone.now(),
            estado="enviado", respuesta={"messages": [{"id": "wamid.other.qa"}]})
        self.post(self.envelope(status={"id": "wamid.other.qa", "status": "read", "timestamp": "1793448000"}))
        notification.refresh_from_db()
        self.assertNotIn("webhook", notification.respuesta)

    def test_reused_message_id_from_previous_connection_cannot_change_receipt(self):
        existing = MensajeWhatsAppWebhook.objects.create(
            empresa=self.empresa, message_id="wamid.reused.qa", direccion="saliente", estado="sent",
            cuenta_id="previous-account", phone_number_id="previous-phone")
        self.post(self.envelope(status={"id": existing.message_id, "status": "delivered", "timestamp": "1793448000"}))
        existing.refresh_from_db()
        self.assertEqual(existing.estado, "sent")
        self.assertEqual(existing.cuenta_id, "previous-account")
        self.assertEqual(existing.phone_number_id, "previous-phone")

    def test_inbound_id_collision_cannot_overwrite_outbound_or_its_assets(self):
        existing = MensajeWhatsAppWebhook.objects.create(
            empresa=self.empresa, message_id="wamid.collision.qa", direccion="saliente", estado="read",
            cuenta_id="account-qa", phone_number_id="phone-qa", texto="Respuesta saliente QA")
        self.post(self.envelope(message=self.incoming(existing.message_id)))
        existing.refresh_from_db()
        self.assertEqual(existing.direccion, "saliente")
        self.assertEqual(existing.estado, "read")
        self.assertEqual(existing.texto, "Respuesta saliente QA")

    def test_activation_proof_requires_receipt_for_current_company_and_assets(self):
        from .whatsapp_setup import prueba_entregada

        self.config.whatsapp_prueba_message_id = "wamid.activation.qa"
        self.config.save(update_fields=["whatsapp_prueba_message_id"])
        self.assertFalse(prueba_entregada(self.config))
        self.post(self.envelope(status={"id": "wamid.activation.qa", "status": "sent", "timestamp": "1793448000"}))
        self.assertFalse(prueba_entregada(self.config))
        self.post(self.envelope(status={"id": "wamid.activation.qa", "status": "read", "timestamp": "1793448001"}))
        self.assertTrue(prueba_entregada(self.config))
        self.assertFalse(prueba_entregada(self.other_config))
        self.config.whatsapp_phone_number_id = "different-phone"
        self.assertFalse(prueba_entregada(self.config))
        self.config.whatsapp_phone_number_id = "phone-qa"
        self.config.whatsapp_business_account_id = "different-account"
        self.assertFalse(prueba_entregada(self.config))
