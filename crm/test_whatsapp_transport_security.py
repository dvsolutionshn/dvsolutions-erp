import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase

from .services import WhatsAppAPIError, _post_whatsapp


class WhatsAppTransportSecurityTests(SimpleTestCase):
    config = SimpleNamespace(whatsapp_token="private-token", whatsapp_api_version="v23.0", whatsapp_phone_number_id="123")

    @patch("crm.services.urlopen")
    def test_remote_error_body_is_not_exposed_to_users_or_saved_as_last_error(self, transport):
        transport.side_effect = HTTPError("https://example.invalid/?token=private-token", 400, "private-token", {},
            io.BytesIO(json.dumps({"error": {"code": 190, "message": "private-token and private patient text"}}).encode()))
        with self.assertRaises(WhatsAppAPIError) as raised:
            _post_whatsapp(self.config, {"type": "text"})
        self.assertIn("190", str(raised.exception))
        self.assertNotIn("private-token", str(raised.exception))
        self.assertNotIn("private patient", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    @patch("crm.services.urlopen", side_effect=URLError("private-token"))
    def test_network_error_does_not_repeat_private_url(self, transport):
        with self.assertRaises(WhatsAppAPIError) as raised:
            _post_whatsapp(self.config, {})
        self.assertNotIn("private-token", str(raised.exception))
