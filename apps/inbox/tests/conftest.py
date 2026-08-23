import pytest
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.inbox.models import WhatsAppChannel
from apps.inbox.services import n8n, realtime, ycloud
from apps.users.models import User


@pytest.fixture
def company(db):
    return Company.objects.create(name="Inmobiliaria Demo", slug="demo", status=Company.Status.ACTIVE)


@pytest.fixture
def other_company(db):
    return Company.objects.create(name="Otra Inmobiliaria", slug="otra", status=Company.Status.ACTIVE)


@pytest.fixture
def user(company):
    return User.objects.create_user(email="asesor@demo.co", password="x", company=company, role=User.Role.ADMIN)


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def channel(company):
    channel = WhatsAppChannel(
        company=company,
        ycloud_api_key="key-demo",
        ycloud_from="+573001111111",
        n8n_webhook_url="https://n8n.example/webhook/demo",
        verify_token="token-demo",
    )
    channel.raw_key = channel.rotate_webhook_key(save=False)
    channel.save()
    return channel


@pytest.fixture
def other_channel(other_company):
    channel = WhatsAppChannel(company=other_company, ycloud_api_key="key-otra", ycloud_from="+573002222222")
    channel.raw_key = channel.rotate_webhook_key(save=False)
    channel.save()
    return channel


@pytest.fixture(autouse=True)
def fake_ycloud(monkeypatch):
    """Ningún test toca la red: YCloud responde OK con un wamid incremental."""
    sent = []

    def _send(*, channel, to, body, from_number=None):
        sent.append({"to": to, "body": body})
        return {"ok": True, "error": None, "wamid": f"wamid.out{len(sent)}", "raw": {}, "status_code": 200}

    monkeypatch.setattr(ycloud, "send_whatsapp_text", _send)
    return sent


@pytest.fixture(autouse=True)
def fake_n8n(monkeypatch):
    forwarded = []

    def _forward(channel, payload):
        forwarded.append(payload)
        return {"ok": True, "error": None, "status_code": 200}

    monkeypatch.setattr(n8n, "forward_ycloud_event", _forward)
    return forwarded


@pytest.fixture(autouse=True)
def realtime_events():
    """Captura la difusión en vivo y restaura el no-op al terminar."""
    events = []
    realtime.set_backend(lambda company_id, event, payload: events.append((company_id, event, payload)))
    yield events
    realtime.set_backend(None)


def ycloud_payload(*, phone="573001234567", text="Hola", wamid="wamid.1", name="Doc Tester", send_time="2026-08-20T16:00:00.000Z"):
    return {
        "id": "evt_1",
        "type": "whatsapp.inbound_message.received",
        "createTime": send_time,
        "whatsappInboundMessage": {
            "id": "msg_abc",
            "wamid": wamid,
            "from": phone,
            "to": "+573001111111",
            "type": "text",
            "text": {"body": text},
            "customerProfile": {"name": name},
            "sendTime": send_time,
        },
    }


@pytest.fixture
def webhook(channel):
    """Cliente sin sesión que se autentica con la credencial del canal."""
    client = APIClient()

    def _post(payload, *, url="/api/v1/inbox/webhook/whatsapp/", key=None):
        return client.post(url, payload, format="json", HTTP_X_API_KEY=key or channel.raw_key)

    _post.client = client
    return _post
