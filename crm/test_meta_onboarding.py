import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from .meta_onboarding import (
    MetaOnboardingError, _collection, complete_connection, validate_template, verify_connection,
)


@override_settings(META_WHATSAPP_APP_ID="app", META_WHATSAPP_APP_SECRET="secret-private",
                   META_WHATSAPP_GRAPH_VERSION="v23.0")
class MetaOnboardingTests(SimpleTestCase):
    def setUp(self):
        self.token = {"access_token": "token-private"}
        self.debug = {"data": {"is_valid": True, "app_id": "app", "scopes": [
            "whatsapp_business_messaging", "whatsapp_business_management"]}}
        self.phone = {"id": "phone", "display_phone_number": "+504 9999 9999", "verified_name": "Hospital QA"}
        self.phones = {"data": [self.phone]}
        self.apps = {"data": [{"whatsapp_business_api_data": {"id": "app"}}]}
        self.config = SimpleNamespace(whatsapp_token="token-private", whatsapp_business_account_id="account",
                                      whatsapp_phone_number_id="phone")

    def mock_responses(self, mocked, *payloads):
        mocked.side_effect = [io.BytesIO(json.dumps(p).encode("utf-8")) for p in payloads]

    @patch("crm.meta_onboarding.urlopen")
    def test_connection_validates_token_membership_and_subscribes_only_our_app(self, mocked):
        self.mock_responses(mocked, self.token, self.debug, self.phones,
                            {"data": [{"whatsapp_business_api_data": {"id": "other-app"}}]}, {"success": True})
        result = complete_connection("code-private", "account", "phone")
        self.assertEqual(result["phone_number_id"], "phone")
        self.assertEqual(result["readiness"], "pending_test")
        self.assertTrue(result["webhook_subscribed"])
        requests = [call.args[0] for call in mocked.call_args_list]
        self.assertEqual([req.get_method() for req in requests], ["GET", "GET", "GET", "GET", "POST"])
        self.assertTrue(requests[-1].full_url.endswith("/account/subscribed_apps"))
        self.assertEqual(requests[-1].get_header("Authorization"), "Bearer token-private")
        self.assertFalse(any("/register" in req.full_url for req in requests))
        self.assertTrue(all(call.kwargs["timeout"] == 20 for call in mocked.call_args_list))

    @patch("crm.meta_onboarding.urlopen")
    def test_existing_subscription_is_not_mutated_and_unique_phone_can_be_discovered(self, mocked):
        self.mock_responses(mocked, self.token, self.debug, self.phones, self.apps)
        result = complete_connection("code", "account")
        self.assertEqual(result["phone_number_id"], "phone")
        self.assertEqual(mocked.call_count, 4)
        self.assertTrue(all(c.args[0].get_method() == "GET" for c in mocked.call_args_list))

    @patch("crm.meta_onboarding.urlopen")
    def test_wrong_account_or_ambiguous_number_does_not_subscribe(self, mocked):
        for requested, phones in [("alien-phone", self.phones), (None, {"data": [self.phone, {**self.phone, "id": "second"}]})]:
            with self.subTest(requested=requested):
                mocked.reset_mock()
                self.mock_responses(mocked, self.token, self.debug, phones)
                with self.assertRaises(MetaOnboardingError):
                    complete_connection("code", "account", requested)
                self.assertEqual(mocked.call_count, 3)

    @patch("crm.meta_onboarding.urlopen")
    def test_other_app_expired_or_missing_scope_is_rejected(self, mocked):
        for changes in ({"app_id": "alien"}, {"is_valid": False}, {"scopes": ["whatsapp_business_messaging"]}):
            with self.subTest(changes=changes):
                self.mock_responses(mocked, self.token, {"data": {**self.debug["data"], **changes}})
                with self.assertRaises(MetaOnboardingError):
                    complete_connection("code", "account", "phone")

    @patch("crm.meta_onboarding.urlopen")
    def test_graph_failures_never_echo_secrets_or_meta_bodies(self, mocked):
        for failure in (HTTPError("https://secret-private/code-private", 400, "token-private", {},
                                  io.BytesIO(b'{"error":{"message":"token-private secret-private code-private"}}')),
                        URLError("token-private secret-private code-private"),
                        TimeoutError("token-private secret-private code-private")):
            with self.subTest(error=type(failure).__name__):
                mocked.side_effect = failure
                with self.assertRaises(MetaOnboardingError) as caught:
                    complete_connection("code-private", "account", "phone")
                for secret in ("token-private", "secret-private", "code-private"):
                    self.assertNotIn(secret, str(caught.exception))

    @patch("crm.meta_onboarding.urlopen")
    def test_pagination_uses_cursor_and_never_follows_untrusted_next_url(self, mocked):
        self.mock_responses(mocked,
            {"data": [], "paging": {"next": "https://attacker.invalid/?access_token=private", "cursors": {"after": "cursor"}}},
            self.phones)
        self.assertEqual(_collection("account/phone_numbers", "token-private"), [self.phone])
        self.assertTrue(all(call.args[0].full_url.startswith("https://graph.facebook.com/v23.0/account/phone_numbers?")
                            for call in mocked.call_args_list))
        self.assertIn("after=cursor", mocked.call_args_list[-1].args[0].full_url)

    @patch("crm.meta_onboarding.urlopen")
    def test_missing_pagination_cursor_fails_instead_of_using_partial_assets(self, mocked):
        self.mock_responses(mocked, {"data": [self.phone], "paging": {"next": "https://graph.facebook.com/next"}})
        with self.assertRaises(MetaOnboardingError):
            _collection("account/phone_numbers", "token-private")

    @patch("crm.meta_onboarding.urlopen")
    def test_false_subscription_success_is_rejected(self, mocked):
        self.mock_responses(mocked, self.token, self.debug, self.phones, {"data": []}, {"success": False})
        with self.assertRaises(MetaOnboardingError):
            complete_connection("code", "account", "phone")

    @patch("crm.meta_onboarding.urlopen")
    def test_verify_connection_is_read_only_and_requires_subscription(self, mocked):
        self.mock_responses(mocked, self.debug, self.phones, self.apps)
        self.assertEqual(verify_connection(self.config)["display_phone_number"], "+504 9999 9999")
        self.assertTrue(all(c.args[0].get_method() == "GET" for c in mocked.call_args_list))
        self.mock_responses(mocked, self.debug, self.phones, {"data": []})
        with self.assertRaises(MetaOnboardingError):
            verify_connection(self.config)

    def approved_template(self, **changes):
        return {"data": [{"id": "template-id", "name": "recordatorio_cita", "language": "es", "status": "APPROVED",
                          "components": [{"type": "BODY", "text": "{{1}} {{2}} {{3}} {{4}} {{5}} {{6}}"}], **changes}]}

    @patch("crm.meta_onboarding.urlopen")
    def test_template_exact_language_approval_and_parameter_count(self, mocked):
        self.mock_responses(mocked, self.approved_template())
        self.assertEqual(validate_template(self.config, "recordatorio_cita", "es", 6)["body_parameters"], 6)
        for data in (self.approved_template(language="en_US"), self.approved_template(status="PENDING"),
                     self.approved_template(components=[{"type": "BODY", "text": "{{1}} {{2}}"}]),
                     self.approved_template(components=[{"type": "BODY", "text": "{{nombre}}"}]),
                     self.approved_template(components=[{"type": "BODY", "text": "{{1}} {{3}}"}])):
            with self.subTest(data=data):
                self.mock_responses(mocked, data)
                with self.assertRaises(MetaOnboardingError):
                    validate_template(self.config, "recordatorio_cita", "es", 6)

    @patch("crm.meta_onboarding.urlopen")
    def test_cita_template_rejects_dynamic_header_or_button_missing_in_sender(self, mocked):
        body = self.approved_template()["data"][0]["components"]
        for extra in ({"type": "HEADER", "format": "IMAGE"},
                      {"type": "HEADER", "format": "TEXT", "text": "Hola {{1}}"},
                      {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://example.test/{{1}}"}]}):
            with self.subTest(extra=extra):
                self.mock_responses(mocked, self.approved_template(components=body + [extra]))
                with self.assertRaises(MetaOnboardingError):
                    validate_template(self.config, "recordatorio_cita", "es", 6)

    @patch("crm.meta_onboarding.urlopen")
    def test_empty_or_non_json_responses_fail_safely(self, mocked):
        for value in ([], "token-private", {"error": {"message": "secret-private"}}):
            self.mock_responses(mocked, value)
            with self.assertRaises(MetaOnboardingError) as caught:
                complete_connection("code", "account", "phone")
            self.assertNotIn("private", str(caught.exception))
