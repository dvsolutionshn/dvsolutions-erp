"""Activation and tenant isolation, synthetic data only; no requests to Meta."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.forms.models import model_to_dict
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from .forms import ConfiguracionCRMForm
from .meta_onboarding import MetaOnboardingError
from .models import ConfiguracionCRM, MensajeWhatsAppWebhook


@override_settings(META_WHATSAPP_APP_SECRET="test-only-secret", META_WHATSAPP_VERIFY_TOKEN="test-only-verify")
class WhatsAppSetupTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="Clinica QA", slug="clinica-qa", rtn="QA-WA-1")
        self.other = Empresa.objects.create(nombre="Otra QA", slug="otra-qa", rtn="QA-WA-2")
        modulo, _ = Modulo.objects.get_or_create(codigo="crm_marketing", defaults={"nombre": "CRM QA"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        self.user = Usuario.objects.create_superuser(username="wa-admin", password="synthetic-password", email="qa@example.invalid")
        self.client.force_login(self.user)
        self.config = ConfiguracionCRM.objects.create(empresa=self.empresa,
            whatsapp_business_account_id="account-qa", whatsapp_phone_number_id="phone-qa",
            whatsapp_token="private-qa-token", whatsapp_pendiente_validacion=True,
            whatsapp_numero_prueba="50499999999", whatsapp_prueba_message_id="wamid.qa",
            recordatorio_cumpleanos_activo=False)
        self.url = reverse("crm_activar_whatsapp", args=[self.empresa.slug])

    def delivered(self, **kwargs):
        fields = dict(empresa=self.empresa, message_id="wamid.qa", cuenta_id="account-qa",
                      phone_number_id="phone-qa", direccion="saliente", estado="delivered")
        fields.update(kwargs)
        return MensajeWhatsAppWebhook.objects.create(**fields)

    @patch("crm.whatsapp_setup.verify_connection")
    def test_accepted_or_other_tenant_receipt_does_not_enable_automations(self, verify):
        self.delivered(empresa=self.other)
        self.delivered(estado="sent")
        self.client.post(self.url, {"confirmar_activacion": "si"})
        verify.assert_not_called()
        self.config.refresh_from_db()
        self.assertFalse(self.config.whatsapp_activo)
        self.assertTrue(self.config.whatsapp_pendiente_validacion)

    @patch("crm.whatsapp_setup.verify_connection")
    def test_receipt_for_old_number_cannot_activate_new_connection(self, verify):
        self.delivered(phone_number_id="old-phone")
        self.client.post(self.url, {"confirmar_activacion": "si"})
        verify.assert_not_called()
        self.config.refresh_from_db()
        self.assertFalse(self.config.whatsapp_activo)

    @patch("crm.whatsapp_setup.validate_template")
    @patch("crm.whatsapp_setup.verify_connection")
    def test_explicit_activation_requires_delivered_test_and_valid_template(self, verify, template):
        self.delivered()
        self.client.post(self.url, {})
        verify.assert_not_called()
        self.client.post(self.url, {"confirmar_activacion": "si"})
        verify.assert_called_once()
        template.assert_called_once()
        self.assertEqual(template.call_args.kwargs["expected_body_parameters"], 6)
        self.config.refresh_from_db()
        self.assertTrue(self.config.whatsapp_activo)
        self.assertFalse(self.config.whatsapp_pendiente_validacion)
        self.assertIsNotNone(self.config.whatsapp_validado_en)

    @patch("crm.whatsapp_setup.validate_template", side_effect=MetaOnboardingError("Plantilla no aprobada"))
    @patch("crm.whatsapp_setup.verify_connection")
    def test_template_failure_leaves_connection_paused(self, verify, template):
        self.delivered()
        self.client.post(self.url, {"confirmar_activacion": "si"})
        self.config.refresh_from_db()
        self.assertFalse(self.config.whatsapp_activo)
        self.assertTrue(self.config.whatsapp_pendiente_validacion)

    @patch("crm.whatsapp_setup.validate_template")
    @patch("crm.whatsapp_setup.verify_connection")
    def test_birthday_enabled_requires_compatible_template_even_without_a_campaign_source(self, verify, template):
        self.config.recordatorio_cumpleanos_activo = True
        self.config.save()
        self.delivered()
        self.client.post(self.url, {"confirmar_activacion": "si"})
        self.assertEqual(template.call_count, 2)
        self.assertEqual(template.call_args.kwargs["expected_body_parameters"], 4)

    @patch("crm.views.enviar_plantilla_whatsapp", return_value={"messages": [{"id": "wamid.new-test"}]})
    @patch("crm.meta_onboarding.validate_template")
    def test_test_send_works_while_paused_only_with_recipient_consent(self, template, send):
        url = reverse("crm_enviar_prueba_whatsapp", args=[self.empresa.slug])
        self.client.post(url, {})
        send.assert_not_called()
        self.client.post(url, {"autorizar_prueba": "si"})
        send.assert_called_once()
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_prueba_message_id, "wamid.new-test")
        self.assertFalse(self.config.whatsapp_activo)

    def test_inbox_excludes_other_company_and_escapes_incoming_text(self):
        self.delivered(texto="<script>untrusted</script>", direccion="entrante", estado="received")
        self.delivered(empresa=self.other, texto="PRIVATE OTHER TENANT")
        response = self.client.get(reverse("crm_mensajes_whatsapp", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "PRIVATE OTHER TENANT")
        self.assertContains(response, "&lt;script&gt;untrusted&lt;/script&gt;")
        self.assertIn("no-store", response["Cache-Control"])

    def test_pending_configuration_cannot_be_activated_by_general_form(self):
        data = {"whatsapp_activo": "on", "whatsapp_api_version": "v23.0", "dias_alerta_producto": 7}
        form = ConfiguracionCRMForm(data, instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn("whatsapp_activo", form.errors)

    def test_password_field_never_renders_existing_token_and_blank_preserves_it(self):
        form = ConfiguracionCRMForm(instance=self.config)
        self.assertNotIn("private-qa-token", str(form["whatsapp_token"]))
        bound = ConfiguracionCRMForm({"whatsapp_token": ""}, instance=self.config)
        bound.is_valid()
        self.assertEqual(bound.cleaned_data["whatsapp_token"], "private-qa-token")

    def test_changing_validated_template_schema_requires_a_new_paused_validation(self):
        self.config.whatsapp_pendiente_validacion = False
        self.config.whatsapp_validado_en = timezone.now()
        self.config.save()
        data = model_to_dict(self.config, fields=ConfiguracionCRMForm.Meta.fields)
        data.update(whatsapp_activo="", whatsapp_plantilla_cita="nueva_plantilla", whatsapp_token="")
        form = ConfiguracionCRMForm(data, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertTrue(saved.whatsapp_pendiente_validacion)
        self.assertFalse(saved.whatsapp_activo)
        self.assertEqual(saved.whatsapp_prueba_message_id, "")
        self.assertIsNone(saved.whatsapp_validado_en)

    def test_general_form_cannot_overwrite_validation_changed_during_the_request(self):
        self.config.whatsapp_pendiente_validacion = False
        self.config.save()
        data = model_to_dict(self.config, fields=ConfiguracionCRMForm.Meta.fields)
        data.update(whatsapp_activo="", whatsapp_token="")
        original = ConfiguracionCRMForm.is_valid
        data = {key: "" if value is None else value for key, value in data.items()}
        def validate_then_reconnect(form):
            result = original(form)
            ConfiguracionCRM.objects.filter(pk=self.config.pk).update(whatsapp_pendiente_validacion=True)
            return result
        with patch.object(ConfiguracionCRMForm, "is_valid", validate_then_reconnect):
            response = self.client.post(reverse("crm_configuracion", args=[self.empresa.slug]), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La conexion cambio mientras editabas")
        self.config.refresh_from_db()
        self.assertTrue(self.config.whatsapp_pendiente_validacion)

    @patch("crm.meta_onboarding.complete_connection")
    def test_bad_json_and_cross_company_user_do_not_start_oauth(self, connect):
        url = reverse("crm_conectar_whatsapp_meta", args=[self.empresa.slug])
        response = self.client.post(url, data="[]", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        actor = Usuario.objects.create_user(username="external", password="synthetic", empresa=self.other)
        self.client.force_login(actor)
        self.client.post(url, data=json.dumps({"code":"synthetic","waba_id":"qa"}), content_type="application/json")
        connect.assert_not_called()

    @patch("crm.meta_onboarding.complete_connection")
    def test_duplicate_number_is_not_reassigned_to_another_company(self, connect):
        ConfiguracionCRM.objects.create(empresa=self.other, whatsapp_phone_number_id="taken-phone")
        connect.return_value = dict(phone_number_id="taken-phone", business_account_id="other-account",
                                    display_phone_number="+504 9999-0000", access_token="new-private-token")
        response = self.client.post(reverse("crm_conectar_whatsapp_meta", args=[self.empresa.slug]),
                                    data=json.dumps({"code":"synthetic", "waba_id":"other-account"}), content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_phone_number_id, "phone-qa")
