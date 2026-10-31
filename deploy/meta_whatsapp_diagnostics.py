"""Read-only Meta preflight; outputs no access tokens, secrets or patient data."""
import json
import os
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
from crm.models import ConfiguracionCRM

def read(path, token, query=None):
    url = f"https://graph.facebook.com/{settings.META_WHATSAPP_GRAPH_VERSION}/{path}"
    if query:
        url += "?" + urlencode(query)
    try:
        with urlopen(Request(url, headers={"Authorization": f"Bearer {token}"}), timeout=15) as response:
            return json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        return {"diagnostic_error": error.code}
    except (URLError, OSError, ValueError):
        return {"diagnostic_error": "unavailable"}

app_id = settings.META_WHATSAPP_APP_ID
app_token = f"{app_id}|{settings.META_WHATSAPP_APP_SECRET}"
subscriptions = read(f"{quote(app_id, safe='')}/subscriptions", app_token)
safe_subscriptions = []
for item in subscriptions.get("data", []):
    parsed = urlsplit(item.get("callback_url", ""))
    safe_subscriptions.append({"object": item.get("object"),
        "callback_url": f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}",
        "fields": [f.get("name") for f in item.get("fields", []) if isinstance(f, dict)]})
result = {"subscriptions": safe_subscriptions, "subscription_error": subscriptions.get("diagnostic_error")}
config = ConfiguracionCRM.objects.filter(empresa__slug="hospital_mia").first()
if config and config.whatsapp_token:
    token_status = read("debug_token", app_token, {"input_token": config.whatsapp_token})
    data = token_status.get("data", {})
    result["hospital_token"] = {key: data.get(key) for key in ("is_valid", "app_id", "expires_at", "data_access_expires_at", "scopes")}
    result["token_error"] = token_status.get("diagnostic_error")
    if config.whatsapp_business_account_id:
        phones = read(f"{quote(config.whatsapp_business_account_id, safe='')}/phone_numbers", config.whatsapp_token,
                      {"fields": "id,display_phone_number,verified_name"})
        result["phone_error"] = phones.get("diagnostic_error")
        result["hospital_phones"] = [{"selected": str(p.get("id")) == config.whatsapp_phone_number_id,
            "number_suffix": "".join(c for c in str(p.get("display_phone_number", "")) if c.isdigit())[-4:],
            "verified_name": p.get("verified_name")} for p in phones.get("data", [])]
print(json.dumps(result, ensure_ascii=True))
