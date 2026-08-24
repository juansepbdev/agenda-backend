"""Cobertura de `POST /api/v1/inbox/messages/`."""

import pytest

from apps.inbox.models import Contact, Conversation, Message

URL = "/api/v1/inbox/messages/"


def test_stores_inbound_and_creates_contact_and_conversation(api, company):
    response = api.post(URL, {"phone": "+57 300 123 4567", "content": "Hola"}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["message"]["direction"] == "inbound"
    assert body["message"]["sender_type"] == "contact"
    assert body["message"]["status"] == "received"

    contact = Contact.objects.get(company=company)
    assert contact.phone_number == "+573001234567"
    assert Conversation.objects.get(company=company).unread_count == 1


def test_stores_outbound_without_calling_ycloud(api, company, fake_ycloud):
    """Este endpoint solo escribe: nunca despacha por WhatsApp."""
    response = api.post(
        URL, {"phone": "+573001234567", "content": "Ya te confirmo", "sender_type": "agent"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["message"]["direction"] == "outbound"
    assert fake_ycloud == []
    assert Conversation.objects.get(company=company).unread_count == 0


def test_outbound_from_agent_reassigns_conversation(api, company):
    api.post(URL, {"phone": "+573001234567", "content": "Hola"}, format="json")
    api.post(URL, {"phone": "+573001234567", "content": "Voy", "sender_type": "agent"}, format="json")

    assert Conversation.objects.get(company=company).assignment == Conversation.Assignment.ME


def test_repeated_wa_message_id_is_idempotent(api, company):
    payload = {"phone": "+573001234567", "content": "Hola", "wa_message_id": "wamid.dup"}

    first = api.post(URL, payload, format="json")
    second = api.post(URL, payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert Message.objects.filter(company=company).count() == 1


def test_rejects_direction_inconsistent_with_sender(api):
    response = api.post(
        URL,
        {"phone": "+573001234567", "content": "Hola", "sender_type": "contact", "direction": "outbound"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INBOX_VALIDATION_ERROR"


def test_rejects_empty_content(api):
    response = api.post(URL, {"phone": "+573001234567", "content": "   "}, format="json")

    assert response.status_code == 400


def test_requires_phone_or_conversation(api):
    response = api.post(URL, {"content": "Hola"}, format="json")

    assert response.status_code == 400


def test_accepts_conversation_id_instead_of_phone(api, company):
    created = api.post(URL, {"phone": "+573001234567", "content": "Hola"}, format="json").json()

    response = api.post(
        URL, {"conversation_id": created["conversation_id"], "content": "Segundo"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["conversation_id"] == created["conversation_id"]


def test_explicit_timestamp_is_persisted(api, company):
    response = api.post(
        URL,
        {"phone": "+573001234567", "content": "Antiguo", "timestamp": "2026-01-15T10:00:00Z"},
        format="json",
    )

    assert response.status_code == 201
    assert Message.objects.get(company=company).created_at.isoformat().startswith("2026-01-15T10:00")


def test_batch_reports_successes_and_failures(api, company):
    response = api.post(
        URL,
        {"messages": [
            {"phone": "+573001234567", "content": "Uno"},
            {"phone": "+573001234567", "content": "", "sender_type": "bot"},
            {"phone": "+573009999999", "content": "Tres", "sender_type": "bot"},
        ]},
        format="json",
    )

    assert response.status_code == 207
    body = response.json()
    assert body["counts"] == {"received": 3, "created": 2, "duplicated": 0, "failed": 1}
    assert body["errors"][0]["index"] == 1
    assert Message.objects.filter(company=company).count() == 2


def test_batch_without_errors_returns_201(api):
    response = api.post(
        URL, {"messages": [{"phone": "+573001234567", "content": "Uno"}]}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["counts"]["failed"] == 0


def test_api_key_resolves_the_tenant(webhook, company, channel):
    response = webhook.client.post(
        URL,
        {"phone": "+573001234567", "content": "Desde n8n", "sender_type": "bot"},
        format="json",
        HTTP_X_API_KEY=channel.raw_key,
    )

    assert response.status_code == 201
    assert Message.objects.get(company=company).sender_type == Message.Sender.BOT


def test_invalid_api_key_is_rejected(webhook):
    response = webhook.client.post(
        URL, {"phone": "+573001234567", "content": "x"}, format="json", HTTP_X_API_KEY="wak_falsa"
    )

    assert response.status_code == 401


def test_anonymous_without_credential_is_rejected(webhook):
    """403 y no 401: es lo que devuelve DRF con `SessionAuthentication` al frente."""
    response = webhook.client.post(URL, {"phone": "+573001234567", "content": "x"}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_credential_of_another_company_writes_in_that_company(webhook, other_channel, other_company, company):
    """La credencial manda sobre cualquier otra pista de tenant del cuerpo."""
    response = webhook.client.post(
        URL, {"phone": "+573001234567", "content": "x"}, format="json", HTTP_X_API_KEY=other_channel.raw_key
    )

    assert response.status_code == 201
    assert Message.objects.filter(company=other_company).count() == 1
    assert Message.objects.filter(company=company).count() == 0
