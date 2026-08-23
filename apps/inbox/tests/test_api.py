"""Contratos HTTP, aislamiento multiempresa, parser y paginación por cursor."""

import pytest
from rest_framework.test import APIClient

from apps.clients.models import Client
from apps.inbox.models import Contact, Conversation, Message, WhatsAppChannel
from apps.inbox.services import messaging
from apps.users.models import User

from .conftest import ycloud_payload

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/v1/inbox/webhook/whatsapp/"
BOT_REPLY_URL = "/api/v1/inbox/n8n/bot-reply/"


# -- Autenticación y aislamiento entre empresas --------------------------------

def test_los_endpoints_internos_exigen_sesion():
    assert APIClient().get("/api/v1/inbox/conversations/").status_code in (401, 403)


def test_el_webhook_rechaza_credenciales_invalidas(channel):
    client = APIClient()

    sin_clave = client.post(WEBHOOK_URL, ycloud_payload(), format="json")
    con_clave_falsa = client.post(WEBHOOK_URL, ycloud_payload(), format="json", HTTP_X_API_KEY="wak_falsa")

    assert sin_clave.status_code == 401
    assert con_clave_falsa.status_code == 401
    assert con_clave_falsa.json()["error"]["code"] == "INVALID_WEBHOOK_CREDENTIAL"
    assert Message.objects.count() == 0


def test_una_credencial_desactivada_deja_de_servir(channel):
    channel.is_active = False
    channel.save(update_fields=["is_active"])

    response = APIClient().post(WEBHOOK_URL, ycloud_payload(), format="json", HTTP_X_API_KEY=channel.raw_key)

    assert response.status_code == 401


def test_la_credencial_tambien_viaja_por_query_param(channel):
    response = APIClient().post(
        f"{WEBHOOK_URL}?api_key={channel.raw_key}", ycloud_payload(), format="json"
    )
    assert response.status_code == 200


def test_la_credencial_decide_la_empresa_no_el_cuerpo(channel, other_channel, company, other_company):
    APIClient().post(WEBHOOK_URL, ycloud_payload(), format="json", HTTP_X_API_KEY=other_channel.raw_key)

    assert Contact.objects.filter(company=other_company).count() == 1
    assert Contact.objects.filter(company=company).count() == 0


def test_una_conversacion_de_otra_empresa_responde_404(api, other_company, channel, webhook):
    webhook(ycloud_payload())
    conversation = Conversation.objects.get()
    Conversation.objects.filter(pk=conversation.pk).update(company=other_company)

    response = api.get(f"/api/v1/inbox/conversations/{conversation.id}/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_el_listado_solo_muestra_la_empresa_del_usuario(api, company, other_company, channel, other_channel):
    client = APIClient()
    client.post(WEBHOOK_URL, ycloud_payload(), format="json", HTTP_X_API_KEY=channel.raw_key)
    client.post(
        WEBHOOK_URL, ycloud_payload(phone="573009998888", wamid="wamid.otra"), format="json",
        HTTP_X_API_KEY=other_channel.raw_key,
    )

    conversations = api.get("/api/v1/inbox/conversations/").json()["conversations"]

    assert len(conversations) == 1
    assert conversations[0]["contact"]["phone_number"] == "+573001234567"


def test_una_empresa_suspendida_no_procesa_el_webhook(channel, company):
    company.status = company.Status.SUSPENDED
    company.save(update_fields=["status"])

    response = APIClient().post(WEBHOOK_URL, ycloud_payload(), format="json", HTTP_X_API_KEY=channel.raw_key)

    assert response.json() == {"status": "ignored", "reason": "company_inactive"}
    assert Message.objects.count() == 0


# -- El webhook siempre responde 200 -------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {"type": "whatsapp.smb.message.echoes", "whatsappInboundMessage": {"type": "text"}},
        {"type": "whatsapp.template.updated"},
        {"type": "whatsapp.inbound_message.received", "whatsappInboundMessage": {"type": "image", "from": "573001234567"}},
        {},
    ],
)
def test_los_payloads_no_procesables_responden_200_sin_persistir(webhook, payload):
    response = webhook(payload)

    assert response.status_code == 200
    assert response.json()["processed"] == []
    assert Message.objects.count() == 0


def test_un_json_malformado_responde_200(channel):
    response = APIClient().post(
        WEBHOOK_URL, "{roto", content_type="application/json", HTTP_X_API_KEY=channel.raw_key
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "invalid_json"


# -- Parser --------------------------------------------------------------------

def test_el_parser_lee_el_formato_de_ycloud():
    events = messaging.parse_whatsapp_webhook(ycloud_payload())

    assert len(events) == 1
    event = events[0]
    assert event["phone"] == "+573001234567"
    assert event["name"] == "Doc Tester"
    assert event["wa_message_id"] == "wamid.1"
    assert event["timestamp"].isoformat() == "2026-08-20T16:00:00+00:00"
    assert event["business_phone"] == "+573001111111"


def test_el_parser_usa_el_id_cuando_falta_el_wamid():
    payload = ycloud_payload()
    del payload["whatsappInboundMessage"]["wamid"]

    assert messaging.parse_whatsapp_webhook(payload)[0]["wa_message_id"] == "msg_abc"


def test_el_mensaje_queda_fechado_con_la_hora_de_whatsapp(channel, webhook):
    webhook(ycloud_payload(send_time="2026-08-20T16:00:00.000Z"))

    assert Message.objects.get().created_at.isoformat() == "2026-08-20T16:00:00+00:00"


# -- Contacto ------------------------------------------------------------------

def test_un_nombre_editado_a_mano_no_se_pisa_con_el_de_whatsapp(company, channel, webhook):
    webhook(ycloud_payload(wamid="wamid.1", name="Doc Tester"))
    contact = Contact.objects.get()
    contact.name = "Diego Ramirez"
    contact.save(update_fields=["name"])

    webhook(ycloud_payload(wamid="wamid.2", name="Doc Tester"))

    contact.refresh_from_db()
    assert contact.name == "Diego Ramirez"


def test_un_contacto_nuevo_nace_con_el_chatbot_encendido_y_avatar_derivado(company, channel, webhook):
    webhook(ycloud_payload(name="Ana"))

    contact = Contact.objects.get()
    assert contact.chatbot_enabled is True
    assert contact.avatar_initial == "A"
    assert contact.avatar_color.startswith("#") and len(contact.avatar_color) == 7
    assert contact.source_id == "wa_573001234567"


def test_el_contacto_se_enlaza_con_el_cliente_de_agenda_por_telefono(company, channel, webhook):
    client = Client.objects.create(
        company=company, first_name="Diego", phone="573001234567", normalized_phone="+573001234567"
    )

    webhook(ycloud_payload())

    assert Contact.objects.get().client_id == client.id


def test_el_interruptor_exige_el_campo_enabled(api, company, channel, webhook):
    webhook(ycloud_payload())

    response = api.post(f"/api/v1/inbox/contacts/{Contact.objects.get().id}/chatbot/", {}, format="json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INBOX_VALIDATION_ERROR"


def test_un_contacto_de_otra_empresa_responde_404(api, other_company, company):
    foreign = Contact.objects.create(company=other_company, phone_number="+573009998888")

    response = api.post(f"/api/v1/inbox/contacts/{foreign.id}/chatbot/", {"enabled": False}, format="json")

    assert response.status_code == 404


# -- display_id ----------------------------------------------------------------

def test_el_display_id_es_secuencial_por_empresa(company, other_company, channel, other_channel):
    client = APIClient()
    for index, phone in enumerate(["573001111111", "573002222222"], start=1):
        client.post(
            WEBHOOK_URL, ycloud_payload(phone=phone, wamid=f"wamid.{index}"), format="json",
            HTTP_X_API_KEY=channel.raw_key,
        )
    client.post(
        WEBHOOK_URL, ycloud_payload(phone="573003333333", wamid="wamid.otra"), format="json",
        HTTP_X_API_KEY=other_channel.raw_key,
    )

    assert sorted(Conversation.objects.filter(company=company).values_list("display_id", flat=True)) == [1, 2]
    assert list(Conversation.objects.filter(company=other_company).values_list("display_id", flat=True)) == [1]


# -- Listado y filtros ---------------------------------------------------------

def test_el_listado_ordena_por_actividad_descendente(api, company, channel, webhook):
    webhook(ycloud_payload(phone="573001111111", wamid="wamid.1"))
    webhook(ycloud_payload(phone="573002222222", wamid="wamid.2"))

    conversations = api.get("/api/v1/inbox/conversations/").json()["conversations"]

    assert [item["contact"]["phone_number"] for item in conversations] == ["+573002222222", "+573001111111"]


def test_el_filtro_por_assignment(api, company, channel, webhook):
    webhook(ycloud_payload(phone="573001111111", wamid="wamid.1"))
    webhook(ycloud_payload(phone="573002222222", wamid="wamid.2"))
    Conversation.objects.filter(contact__phone_number="+573002222222").update(assignment="unassigned")

    solo_bot = api.get("/api/v1/inbox/conversations/?filter=bot").json()["conversations"]
    sin_asignar = api.get("/api/v1/inbox/conversations/?filter=unassigned").json()["conversations"]
    invalido = api.get("/api/v1/inbox/conversations/?filter=inexistente").json()["conversations"]

    assert len(solo_bot) == 1 and solo_bot[0]["assignment"] == "bot"
    assert len(sin_asignar) == 1
    assert len(invalido) == 2  # un filtro desconocido retrocede a "all"


# -- Paginación por cursor ------------------------------------------------------

@pytest.fixture
def conversation_with_messages(api, company, channel, webhook):
    webhook(ycloud_payload(wamid="wamid.0"))
    conversation = Conversation.objects.get()
    for index in range(1, 10):
        Message.objects.create(
            company=company,
            conversation=conversation,
            contact=conversation.contact,
            content=f"mensaje {index}",
            message_type=Message.Type.INBOUND,
            sender_type=Message.Sender.CONTACT,
            status=Message.Status.RECEIVED,
        )
    return conversation


def test_sin_cursor_devuelve_los_ultimos_en_orden_ascendente(api, conversation_with_messages):
    messages = api.get(
        f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?limit=3"
    ).json()["messages"]

    ids = [item["id"] for item in messages]
    assert ids == sorted(ids)
    assert [item["content"] for item in messages] == ["mensaje 7", "mensaje 8", "mensaje 9"]


def test_after_id_anexa_los_nuevos(api, conversation_with_messages):
    todos = api.get(f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?limit=200").json()["messages"]
    cursor = todos[4]["id"]

    messages = api.get(
        f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?after_id={cursor}"
    ).json()["messages"]

    assert [item["id"] for item in messages] == [item["id"] for item in todos[5:]]


def test_before_id_pagina_hacia_arriba_en_orden_ascendente(api, conversation_with_messages):
    todos = api.get(f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?limit=200").json()["messages"]
    cursor = todos[5]["id"]

    messages = api.get(
        f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?before_id={cursor}&limit=2"
    ).json()["messages"]

    assert [item["id"] for item in messages] == [todos[3]["id"], todos[4]["id"]]


def test_after_id_tiene_prioridad_sobre_before_id(api, conversation_with_messages):
    todos = api.get(f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?limit=200").json()["messages"]
    cursor = todos[7]["id"]

    messages = api.get(
        f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/"
        f"?after_id={cursor}&before_id={cursor}"
    ).json()["messages"]

    assert [item["id"] for item in messages] == [todos[8]["id"], todos[9]["id"]]


@pytest.mark.parametrize(("raw", "expected"), [("0", 1), ("500", 10), ("no-numerico", 10)])
def test_el_limite_se_acota(api, conversation_with_messages, raw, expected):
    messages = api.get(
        f"/api/v1/inbox/conversations/{conversation_with_messages.id}/messages/?limit={raw}"
    ).json()["messages"]

    assert len(messages) == expected


def test_el_detalle_trae_conversacion_mensajes_y_contacto(api, conversation_with_messages):
    body = api.get(f"/api/v1/inbox/conversations/{conversation_with_messages.id}/").json()

    assert set(body) == {"conversation", "messages", "contact"}
    assert len(body["messages"]) == 10
    assert body["contact"]["phone_number"] == "+573001234567"
    assert body["conversation"]["contact"]["id"] == body["contact"]["id"]


# -- Ingesta manual ------------------------------------------------------------

def test_la_ingesta_manual_persiste_y_no_reenvia_a_n8n(api, company, channel, fake_n8n):
    response = api.post(
        "/api/v1/inbox/messages/incoming/",
        {"phone": "57 300-123 45 67", "message": "Hola", "name": "Ana"},
        format="json",
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["duplicate"] is False
    assert body["inbound"]["direction"] == "inbound"
    assert Contact.objects.get().phone_number == "+573001234567"
    assert fake_n8n == []


def test_la_ingesta_manual_exige_telefono(api, company):
    response = api.post("/api/v1/inbox/messages/incoming/", {"content": "Hola"}, format="json")

    assert response.status_code == 400


# -- Callback de n8n -----------------------------------------------------------

@pytest.mark.parametrize("body", [{"text": "hola"}, {"phone": "573001234567"}, {"phone": "573001234567", "text": "  "}])
def test_el_callback_valida_sus_campos(channel, body):
    response = APIClient().post(BOT_REPLY_URL, body, format="json", HTTP_X_API_KEY=channel.raw_key)

    assert response.status_code == 400


def test_el_callback_crea_el_contacto_si_no_existe(channel, company):
    response = APIClient().post(
        BOT_REPLY_URL, {"phone": "573009999999", "text": "hola"}, format="json", HTTP_X_API_KEY=channel.raw_key
    )

    assert response.status_code == 201
    assert Contact.objects.get(company=company).phone_number == "+573009999999"


# -- Verificación estilo Meta ---------------------------------------------------

def test_la_verificacion_devuelve_el_challenge(channel):
    response = APIClient().get(
        f"{WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=token-demo&hub.challenge=1234"
    )

    assert response.status_code == 200
    assert response.content == b"1234"


def test_la_verificacion_falla_con_un_token_incorrecto(channel):
    response = APIClient().get(
        f"{WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=otro&hub.challenge=1234"
    )

    assert response.status_code == 403


# -- Canal ---------------------------------------------------------------------

def test_la_credencial_se_guarda_hasheada(company):
    channel = WhatsAppChannel(company=company)
    raw_key = channel.rotate_webhook_key(save=False)
    channel.save()

    assert raw_key.startswith("wak_")
    assert channel.webhook_key_hash != raw_key
    assert WhatsAppChannel.resolve(raw_key) == channel
    assert WhatsAppChannel.resolve("wak_otra") is None


def test_un_usuario_sin_empresa_no_entra_al_inbox(db):
    superuser = User.objects.create_superuser(email="root@demo.co", password="x")
    client = APIClient()
    client.force_authenticate(user=superuser)

    response = client.get("/api/v1/inbox/conversations/")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_SCOPE"
