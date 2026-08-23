"""Las siete reglas de negocio transversales del inbox."""

import pytest

from apps.inbox.models import Contact, Conversation, Message
from apps.inbox.services import n8n

from .conftest import ycloud_payload

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/v1/inbox/webhook/whatsapp/"
BOT_REPLY_URL = "/api/v1/inbox/n8n/bot-reply/"


def _contact(company, phone="+573001234567"):
    return Contact.objects.get(company=company, phone_number=phone)


def _conversation(company):
    return Conversation.objects.get(company=company)


# -- R1: el interruptor chatbot_enabled es excluyente ---------------------------

def test_r1_agente_bloqueado_con_chatbot_encendido(api, company, channel, webhook):
    webhook(ycloud_payload())
    conversation = _conversation(company)

    response = api.post(
        f"/api/v1/inbox/conversations/{conversation.id}/messages/", {"content": "Hola"}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CHATBOT_ENABLED"
    assert not Message.objects.filter(message_type=Message.Type.OUTBOUND).exists()


def test_r1_agente_puede_enviar_con_chatbot_apagado(api, company, channel, webhook):
    webhook(ycloud_payload())
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    response = api.post(
        f"/api/v1/inbox/conversations/{_conversation(company).id}/messages/", {"content": "Hola"}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["message"]["sender_type"] == "agent"
    assert body["message"]["status"] == "sent"
    assert body["ycloud_ok"] is True


def test_r1_callback_de_n8n_se_omite_con_chatbot_apagado(api, company, channel, webhook):
    webhook(ycloud_payload())
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    response = webhook.client.post(
        BOT_REPLY_URL,
        {"phone": "573001234567", "text": "respuesta bot"},
        format="json",
        HTTP_X_API_KEY=channel.raw_key,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped"
    assert body["reason"] == "chatbot_disabled"
    assert not Message.objects.filter(sender_type=Message.Sender.BOT).exists()


def test_r1_callback_de_n8n_persiste_con_chatbot_encendido(company, channel, webhook):
    webhook(ycloud_payload())

    response = webhook.client.post(
        BOT_REPLY_URL,
        {"phone": "573001234567", "text": "respuesta bot", "message_id": "m1", "update_id": "u1"},
        format="json",
        HTTP_X_API_KEY=channel.raw_key,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["message"]["sender_type"] == "bot"
    assert body["message"]["status"] == "sent"
    assert body["message"]["wa_message_id"] == "wamid.out1"


# -- R2: idempotencia por wa_message_id ----------------------------------------

def test_r2_webhook_repetido_no_duplica_el_mensaje(company, channel, webhook, fake_n8n, realtime_events):
    first = webhook(ycloud_payload(wamid="wamid.dup"))
    events_after_first = len(realtime_events)

    second = webhook(ycloud_payload(wamid="wamid.dup"))

    assert first.json()["processed"][0]["duplicate"] is False
    assert second.json()["processed"][0]["duplicate"] is True
    assert Message.objects.count() == 1
    # Ni difusión ni reenvío al bot en el reintento.
    assert len(realtime_events) == events_after_first
    assert len(fake_n8n) == 1


def test_r2_los_mensajes_sin_wamid_no_se_deduplican(api, company):
    for _ in range(2):
        api.post(
            "/api/v1/inbox/messages/incoming/",
            {"phone": "573001234567", "content": "Hola"},
            format="json",
        )
    assert Message.objects.count() == 2


# -- R3: el assignment se mueve solo -------------------------------------------

def test_r3_entrante_con_chatbot_encendido_asigna_al_bot(company, channel, webhook):
    webhook(ycloud_payload())
    assert _conversation(company).assignment == Conversation.Assignment.BOT


def test_r3_entrante_con_chatbot_apagado_no_cambia_el_assignment(api, company, channel, webhook):
    webhook(ycloud_payload(wamid="wamid.1"))
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")
    Conversation.objects.filter(company=company).update(assignment=Conversation.Assignment.UNASSIGNED)

    webhook(ycloud_payload(wamid="wamid.2"))

    assert _conversation(company).assignment == Conversation.Assignment.UNASSIGNED


def test_r3_envio_del_asesor_asigna_a_me(api, company, channel, webhook):
    webhook(ycloud_payload())
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    api.post(
        f"/api/v1/inbox/conversations/{_conversation(company).id}/messages/", {"content": "Hola"}, format="json"
    )

    assert _conversation(company).assignment == Conversation.Assignment.ME


def test_r3_respuesta_del_bot_asigna_a_bot(api, company, channel, webhook):
    webhook(ycloud_payload())
    Conversation.objects.filter(company=company).update(assignment=Conversation.Assignment.UNASSIGNED)

    webhook.client.post(
        BOT_REPLY_URL, {"phone": "573001234567", "text": "hola"}, format="json", HTTP_X_API_KEY=channel.raw_key
    )

    assert _conversation(company).assignment == Conversation.Assignment.BOT


# -- R4: unread_count ----------------------------------------------------------

def test_r4_cada_entrante_suma_uno(company, channel, webhook):
    webhook(ycloud_payload(wamid="wamid.1"))
    webhook(ycloud_payload(wamid="wamid.2", text="segundo"))

    assert _conversation(company).unread_count == 2


def test_r4_abrir_el_chat_limpia_sin_reordenar(api, company, channel, webhook):
    webhook(ycloud_payload())
    conversation = _conversation(company)
    activity_before = conversation.last_activity_at

    response = api.get(f"/api/v1/inbox/conversations/{conversation.id}/")

    conversation.refresh_from_db()
    assert response.json()["conversation"]["unread_count"] == 0
    assert conversation.unread_count == 0
    assert conversation.last_activity_at == activity_before


def test_r4_los_salientes_no_tocan_el_no_leidos(api, company, channel, webhook):
    webhook(ycloud_payload())
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    api.post(
        f"/api/v1/inbox/conversations/{_conversation(company).id}/messages/", {"content": "Hola"}, format="json"
    )

    assert _conversation(company).unread_count == 1


# -- R5: el evento en vivo no transporta contenido -----------------------------

def test_r5_cada_persistencia_difunde_punteros_sin_contenido(company, channel, webhook, realtime_events):
    webhook(ycloud_payload(text="texto secreto"))

    names = [event for _, event, _ in realtime_events]
    assert names == ["messages.updated", "conversations.changed"]

    payload = realtime_events[0][2]
    assert set(payload) == {"conversation_id", "after_id", "display_id"}
    assert "texto secreto" not in str(payload)


def test_r5_after_id_es_el_maximo_previo_no_id_menos_uno(api, company, channel, webhook, realtime_events):
    webhook(ycloud_payload(wamid="wamid.1"))
    first_id = Message.objects.get().id
    realtime_events.clear()

    webhook(ycloud_payload(wamid="wamid.2", text="segundo"))

    assert realtime_events[0][2]["after_id"] == first_id


def test_r5_el_interruptor_difunde_el_contacto_completo(api, company, channel, webhook, realtime_events):
    webhook(ycloud_payload())
    realtime_events.clear()

    api.post(f"/api/v1/inbox/contacts/{_contact(company).id}/chatbot/", {"enabled": False}, format="json")

    _, event, payload = realtime_events[-1]
    assert event == "contact.updated"
    assert payload["contact"]["chatbot_enabled"] is False


# -- R6: el envío saliente persiste su resultado --------------------------------

def test_r6_fallo_de_ycloud_deja_el_mensaje_failed_y_responde_2xx(api, company, channel, webhook, monkeypatch):
    webhook(ycloud_payload())
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    from apps.inbox.services import ycloud

    monkeypatch.setattr(
        ycloud,
        "send_whatsapp_text",
        lambda **kwargs: {"ok": False, "error": "YCloud respondió 400", "wamid": None, "raw": {}, "status_code": 400},
    )

    response = api.post(
        f"/api/v1/inbox/conversations/{_conversation(company).id}/messages/", {"content": "Hola"}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["ycloud_ok"] is False
    assert body["ycloud_error"] == "YCloud respondió 400"
    assert Message.objects.get(sender_type=Message.Sender.AGENT).status == Message.Status.FAILED


def test_r6_un_wamid_ya_usado_no_se_guarda_dos_veces(api, company, channel, webhook, monkeypatch):
    webhook(ycloud_payload(wamid="wamid.colision"))
    contact = _contact(company)
    api.post(f"/api/v1/inbox/contacts/{contact.id}/chatbot/", {"enabled": False}, format="json")

    from apps.inbox.services import ycloud

    monkeypatch.setattr(
        ycloud,
        "send_whatsapp_text",
        lambda **kwargs: {"ok": True, "error": None, "wamid": "wamid.colision", "raw": {}, "status_code": 200},
    )

    response = api.post(
        f"/api/v1/inbox/conversations/{_conversation(company).id}/messages/", {"content": "Hola"}, format="json"
    )

    assert response.status_code == 201
    message = Message.objects.get(sender_type=Message.Sender.AGENT)
    assert message.status == Message.Status.SENT
    assert message.wa_message_id is None


# -- R7: un reenvío a n8n por payload ------------------------------------------

def test_r7_un_solo_reenvio_aunque_el_payload_traiga_varios_mensajes(company, channel, webhook, fake_n8n):
    meta_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "573001234567", "profile": {"name": "Doc"}}],
                            "messages": [
                                {"id": "wamid.a", "from": "573001234567", "type": "text", "text": {"body": "uno"}},
                                {"id": "wamid.b", "from": "573001234567", "type": "text", "text": {"body": "dos"}},
                            ],
                        }
                    }
                ]
            }
        ]
    }

    response = webhook(meta_payload)

    assert len(response.json()["processed"]) == 2
    assert Message.objects.count() == 2
    assert len(fake_n8n) == 1


def test_r7_no_se_reenvia_con_el_chatbot_apagado(api, company, channel, webhook, fake_n8n):
    webhook(ycloud_payload(wamid="wamid.1"))
    fake_n8n.clear()
    api.post(f"/api/v1/inbox/contacts/{_contact(company).id}/chatbot/", {"enabled": False}, format="json")

    response = webhook(ycloud_payload(wamid="wamid.2"))

    assert fake_n8n == []
    assert "n8n_forward" not in response.json()


def test_r7_no_se_reenvia_si_n8n_no_esta_configurado(company, channel, webhook, fake_n8n):
    channel.n8n_webhook_url = ""
    channel.save(update_fields=["n8n_webhook_url"])

    response = webhook(ycloud_payload())

    assert fake_n8n == []
    assert "n8n_forward" not in response.json()
    assert Message.objects.count() == 1


def test_r7_se_reenvia_el_json_crudo_sin_transformar(company, channel, webhook, fake_n8n):
    payload = ycloud_payload()

    webhook(payload)

    assert fake_n8n == [payload]


def test_r7_el_reenvio_fallido_no_rompe_el_webhook(company, channel, webhook, monkeypatch):
    monkeypatch.setattr(
        n8n, "forward_ycloud_event", lambda channel, payload: {"ok": False, "error": "caído", "status_code": None}
    )

    response = webhook(ycloud_payload())

    assert response.status_code == 200
    assert response.json()["n8n_forward"]["ok"] is False
    assert Message.objects.count() == 1
