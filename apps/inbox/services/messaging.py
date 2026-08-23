"""Núcleo del inbox: aquí viven las siete reglas de negocio.

Las vistas solo hacen parsing de HTTP; toda la lógica está en este módulo.
Las dependencias fluyen en una sola dirección: `ycloud`, `n8n`, `realtime` y
`serializers` no se importan entre sí ni conocen a `messaging`.
"""

import logging

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.clients.models import Client

from ..exceptions import (
    ChatbotEnabledError,
    ContactNotFoundError,
    ConversationNotFoundError,
    InboxValidationError,
)
from ..models import Contact, Conversation, Message
from ..serializers import serialize_contact, serialize_conversation, serialize_message
from ..utils import normalize_phone, parse_wa_timestamp
from . import n8n, realtime, ycloud

logger = logging.getLogger(__name__)

PREVIEW_MAX_LENGTH = 255
DETAIL_MESSAGE_LIMIT = 100
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

CONVERSATION_FILTERS = {"all", "me", "unassigned", "bot"}


# -----------------------------------------------------------------------------
# Normalización de la entrada del BSP
# -----------------------------------------------------------------------------

def parse_whatsapp_webhook(payload: dict) -> list[dict]:
    """Aplana el JSON del BSP a una lista de eventos de texto.

    Soporta el formato de YCloud y, como respaldo, el de la Cloud API de Meta.
    Solo se aceptan mensajes `type: "text"`; el resto se descarta con log (punto
    de extensión natural hacia multimedia).
    """
    if not isinstance(payload, dict):
        return []

    event_type = payload.get("type")

    if event_type == "whatsapp.inbound_message.received":
        return _parse_ycloud_inbound(payload)

    if event_type == "whatsapp.smb.message.echoes":
        # Ecos de mensajes salientes: procesarlos duplicaría el hilo.
        logger.info("webhook: eco de mensaje saliente ignorado")
        return []

    if event_type:
        logger.info("webhook: tipo de evento no manejado (%s)", event_type)
        return []

    return _parse_meta_cloud_inbound(payload)


def _parse_ycloud_inbound(payload: dict) -> list[dict]:
    message = payload.get("whatsappInboundMessage") or {}
    if message.get("type") != "text":
        logger.info("webhook: mensaje YCloud no textual (%s) descartado", message.get("type"))
        return []

    text = (message.get("text") or {}).get("body") or ""
    phone = normalize_phone(message.get("from") or "")
    if not phone or not text:
        return []

    return [{
        "phone": phone,
        "name": ((message.get("customerProfile") or {}).get("name") or "").strip(),
        "text": text,
        "wa_message_id": message.get("wamid") or message.get("id"),
        "timestamp": parse_wa_timestamp(message.get("sendTime")),
        # Se parsean pero no se persisten; quedan disponibles para logs.
        "business_phone": normalize_phone(message.get("to") or ""),
        "ycloud_message_id": message.get("id"),
    }]


def _parse_meta_cloud_inbound(payload: dict) -> list[dict]:
    events = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            profiles = {
                (contact.get("wa_id") or ""): ((contact.get("profile") or {}).get("name") or "")
                for contact in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                if message.get("type") != "text":
                    logger.info("webhook: mensaje Meta no textual (%s) descartado", message.get("type"))
                    continue
                raw_phone = message.get("from") or ""
                phone = normalize_phone(raw_phone)
                text = (message.get("text") or {}).get("body") or ""
                if not phone or not text:
                    continue
                events.append({
                    "phone": phone,
                    "name": (profiles.get(raw_phone) or "").strip(),
                    "text": text,
                    "wa_message_id": message.get("id"),
                    "timestamp": parse_wa_timestamp(message.get("timestamp")),
                })
    return events


# -----------------------------------------------------------------------------
# Resolución de contacto y conversación
# -----------------------------------------------------------------------------

def _link_client(contact: Contact) -> None:
    """Enlace best-effort con el cliente de agenda que tenga el mismo teléfono."""
    if contact.client_id or not contact.phone_number:
        return
    client = Client.objects.filter(company_id=contact.company_id, normalized_phone=contact.phone_number).first()
    if client:
        contact.client = client
        contact.save(update_fields=["client", "updated_at"])


def get_or_create_contact_conversation(*, company, phone: str, name: str = "", channel=None):
    """Devuelve `(contact, conversation)` resolviendo por teléfono normalizado."""
    normalized = normalize_phone(phone)
    if not normalized:
        raise InboxValidationError("El teléfono no contiene dígitos.")

    contact, created = Contact.objects.get_or_create(
        company=company,
        phone_number=normalized,
        defaults={"name": name or normalized},
    )
    if created:
        _link_client(contact)
    elif name and (not contact.name or contact.name == contact.phone_number):
        # Un nombre editado a mano en el CRM no se pisa con el de WhatsApp.
        contact.name = name
        contact.save(update_fields=["name", "updated_at"])

    conversation = get_or_create_open_conversation(contact, channel=channel)
    return contact, conversation


def get_or_create_open_conversation(contact: Contact, *, channel=None) -> Conversation:
    conversation = (
        Conversation.objects.filter(contact=contact, status=Conversation.Status.OPEN)
        .order_by("-last_activity_at", "-created_at")
        .first()
    )
    if conversation:
        return conversation

    inbox_name = channel.inbox_name if channel else Conversation._meta.get_field("inbox").get_default()
    return Conversation.objects.create(company_id=contact.company_id, contact=contact, inbox=inbox_name)


def _last_message_id(conversation: Conversation) -> int:
    """`MAX(id)` actual de la conversación: es el `after_id` correcto (R5)."""
    return Message.objects.filter(conversation=conversation).aggregate(value=Max("id"))["value"] or 0


def _touch_conversation(conversation: Conversation, *, content: str, moment, assignment=None, bump_unread=False):
    """Actualiza la denormalización de la lista con un UPDATE dirigido.

    El no leídos sube con `F()` y no con una lectura-modificación-escritura: dos
    webhooks simultáneos sobre la misma conversación perderían un incremento.
    """
    updates = {
        "last_message_preview": content[:PREVIEW_MAX_LENGTH],
        "last_activity_at": moment,
        # `auto_now` solo actúa en `save()`, así que aquí se fija a mano.
        "updated_at": timezone.now(),
    }
    if assignment is not None:
        updates["assignment"] = assignment
    if bump_unread:
        updates["unread_count"] = F("unread_count") + 1

    Conversation.objects.filter(pk=conversation.pk).update(**updates)
    conversation.refresh_from_db(fields=list(updates))


# -----------------------------------------------------------------------------
# R2 / R3 / R4 — ingesta de entrantes
# -----------------------------------------------------------------------------

def ingest_inbound_text(*, company, phone, text, name="", wa_message_id=None, timestamp=None, channel=None) -> dict:
    """Persiste un mensaje entrante. Idempotente por `wa_message_id` dentro del tenant."""
    content = (text or "").strip()
    if not content:
        raise InboxValidationError("El contenido del mensaje es obligatorio.")

    if wa_message_id:
        existing = (
            Message.objects.select_related("conversation__contact")
            .filter(company=company, wa_message_id=wa_message_id)
            .first()
        )
        if existing:
            # R2: ni se inserta, ni se difunde, ni se reenvía a n8n.
            conversation = existing.conversation
            return {
                "duplicate": True,
                "contact_id": str(conversation.contact_id),
                "conversation_id": str(conversation.id),
                "display_id": conversation.display_id,
                "chatbot_enabled": conversation.contact.chatbot_enabled,
                "inbound": serialize_message(existing),
            }

    moment = timestamp or timezone.now()

    with transaction.atomic():
        contact, conversation = get_or_create_contact_conversation(
            company=company, phone=phone, name=name, channel=channel
        )
        after_id = _last_message_id(conversation)

        message = Message.objects.create(
            company_id=company.pk,
            conversation=conversation,
            contact=contact,
            content=content,
            message_type=Message.Type.INBOUND,
            sender_type=Message.Sender.CONTACT,
            status=Message.Status.RECEIVED,
            wa_message_id=wa_message_id or None,
        )
        # `created_at` es auto_now_add: se sobreescribe con la hora real de WhatsApp.
        Message.objects.filter(pk=message.pk).update(created_at=moment)
        message.created_at = moment

        # R3: un entrante con el chatbot encendido pasa la conversación al bot.
        assignment = Conversation.Assignment.BOT if contact.chatbot_enabled else None
        # R4: cada entrante suma uno al no leídos.
        _touch_conversation(conversation, content=content, moment=moment, assignment=assignment, bump_unread=True)

        contact.last_contact_at = moment
        contact.save(update_fields=["last_contact_at", "updated_at"])

    # La difusión ocurre fuera de la transacción: si se emitiera dentro, un
    # cliente podría re-consultar antes del commit y no encontrar el mensaje.
    realtime.broadcast_messages_updated(
        company_id=company.pk,
        conversation_id=conversation.id,
        after_id=after_id,
        display_id=conversation.display_id,
    )
    realtime.broadcast_conversations_changed(company_id=company.pk)

    return {
        "duplicate": False,
        "contact_id": str(contact.id),
        "conversation_id": str(conversation.id),
        "display_id": conversation.display_id,
        "chatbot_enabled": contact.chatbot_enabled,
        "inbound": serialize_message(message),
    }


# -----------------------------------------------------------------------------
# R1 / R6 — salientes
# -----------------------------------------------------------------------------

def _apply_ycloud_send_result(message: Message, result: dict) -> None:
    """Traduce el resultado del envío al registro persistido."""
    fields = ["status", "updated_at"]
    message.status = Message.Status.SENT if result["ok"] else Message.Status.FAILED

    wamid = result.get("wamid")
    if result["ok"] and wamid:
        # La columna es única por empresa: un choque abortaría la escritura.
        collides = (
            Message.objects.filter(company_id=message.company_id, wa_message_id=wamid)
            .exclude(pk=message.pk)
            .exists()
        )
        if collides:
            logger.warning("wamid %s ya existente en la empresa; no se guarda", wamid)
        else:
            message.wa_message_id = wamid
            fields.append("wa_message_id")

    if not result["ok"]:
        logger.warning("envío a YCloud fallido para el mensaje %s: %s", message.pk, result.get("error"))

    message.save(update_fields=fields)


def _dispatch_outbound(*, channel, contact: Contact, message: Message, conversation: Conversation) -> dict:
    """Llama a YCloud **fuera** de la transacción y persiste el resultado (R6)."""
    result = ycloud.send_whatsapp_text(channel=channel, to=contact.phone_number, body=message.content)
    _apply_ycloud_send_result(message, result)
    return result


def send_agent_message(*, company, conversation_id, content: str, channel=None) -> dict:
    """Envío del asesor. Lanza `ChatbotEnabledError` si el chatbot está encendido (R1)."""
    body = (content or "").strip()
    if not body:
        raise InboxValidationError("El contenido del mensaje es obligatorio.")

    with transaction.atomic():
        conversation = (
            Conversation.objects.select_related("contact")
            .filter(company=company, pk=conversation_id)
            .first()
        )
        if conversation is None:
            raise ConversationNotFoundError("La conversación no existe.")

        contact = conversation.contact
        if contact.chatbot_enabled:
            raise ChatbotEnabledError("El chatbot está encendido; apágalo para responder manualmente.")

        after_id = _last_message_id(conversation)
        moment = timezone.now()
        message = Message.objects.create(
            company_id=company.pk,
            conversation=conversation,
            contact=contact,
            content=body,
            message_type=Message.Type.OUTBOUND,
            sender_type=Message.Sender.AGENT,
            status=Message.Status.SENT,
        )
        _touch_conversation(
            conversation, content=body, moment=moment, assignment=Conversation.Assignment.ME
        )

    result = _dispatch_outbound(channel=channel, contact=contact, message=message, conversation=conversation)

    realtime.broadcast_messages_updated(
        company_id=company.pk,
        conversation_id=conversation.id,
        after_id=after_id,
        display_id=conversation.display_id,
    )
    realtime.broadcast_conversations_changed(company_id=company.pk)

    return {
        "message": serialize_message(message),
        "conversation": serialize_conversation(conversation),
        "ycloud_ok": result["ok"],
        "ycloud_error": result["error"],
    }


def send_bot_reply_from_n8n(*, company, phone, text, channel=None, message_id=None, update_id=None) -> dict:
    """Callback del agente de IA. Devuelve `status: "skipped"` si el chatbot está apagado (R1).

    Un webhook no debe recibir un error por una regla de negocio esperada, así
    que aquí se devuelve el estado en vez de lanzar excepción.
    """
    body = (text or "").strip()
    if not body:
        raise InboxValidationError("El texto de la respuesta es obligatorio.")

    logger.info("callback n8n: phone=%s message_id=%s update_id=%s", phone, message_id, update_id)

    with transaction.atomic():
        contact, conversation = get_or_create_contact_conversation(
            company=company, phone=phone, channel=channel
        )
        if not contact.chatbot_enabled:
            return {
                "status": "skipped",
                "reason": "chatbot_disabled",
                "contact_id": str(contact.id),
                "conversation_id": str(conversation.id),
                "conversation": serialize_conversation(conversation),
            }

        after_id = _last_message_id(conversation)
        moment = timezone.now()
        message = Message.objects.create(
            company_id=company.pk,
            conversation=conversation,
            contact=contact,
            content=body,
            message_type=Message.Type.OUTBOUND,
            sender_type=Message.Sender.BOT,
            status=Message.Status.SENT,
        )
        _touch_conversation(
            conversation, content=body, moment=moment, assignment=Conversation.Assignment.BOT
        )

    result = _dispatch_outbound(channel=channel, contact=contact, message=message, conversation=conversation)

    realtime.broadcast_messages_updated(
        company_id=company.pk,
        conversation_id=conversation.id,
        after_id=after_id,
        display_id=conversation.display_id,
    )
    realtime.broadcast_conversations_changed(company_id=company.pk)

    return {
        "status": "ok",
        "message": serialize_message(message),
        "conversation": serialize_conversation(conversation),
        "contact_id": str(contact.id),
        "conversation_id": str(conversation.id),
        "ycloud_ok": result["ok"],
        "ycloud_error": result["error"],
    }


# -----------------------------------------------------------------------------
# Interruptor, listados y lectura
# -----------------------------------------------------------------------------

def set_chatbot_enabled(*, company, contact_id, enabled: bool) -> dict:
    contact = Contact.objects.filter(company=company, pk=contact_id).first()
    if contact is None:
        raise ContactNotFoundError("El contacto no existe.")

    contact.chatbot_enabled = bool(enabled)
    contact.save(update_fields=["chatbot_enabled", "updated_at"])

    contact_data = serialize_contact(contact)
    realtime.broadcast_contact_updated(company_id=company.pk, contact_data=contact_data)
    return {"contact_id": str(contact.id), "chatbot_enabled": contact.chatbot_enabled, "contact": contact_data}


def list_conversations(*, company, filter_id: str = "all") -> list[dict]:
    if filter_id not in CONVERSATION_FILTERS:
        filter_id = "all"

    queryset = (
        Conversation.objects.select_related("contact")
        .filter(company=company)
        .order_by("-last_activity_at", "-created_at")
    )
    if filter_id != "all":
        queryset = queryset.filter(assignment=filter_id)

    return [serialize_conversation(conversation) for conversation in queryset]


def _get_conversation(company, conversation_id) -> Conversation:
    conversation = (
        Conversation.objects.select_related("contact").filter(company=company, pk=conversation_id).first()
    )
    if conversation is None:
        raise ConversationNotFoundError("La conversación no existe.")
    return conversation


def list_messages(*, company, conversation_id, limit=None, after_id=None, before_id=None) -> list[dict]:
    """Paginación por cursor, sin OFFSET. Siempre devuelve orden cronológico ascendente."""
    conversation = _get_conversation(company, conversation_id)
    # `limit=0` se acota a 1, no cae al default: 0 es un valor dado, no la ausencia de valor.
    limit = DEFAULT_PAGE_LIMIT if limit is None else limit
    limit = max(1, min(limit, MAX_PAGE_LIMIT))

    base = Message.objects.filter(conversation=conversation)
    if after_id is not None:
        messages = list(base.filter(id__gt=after_id).order_by("id")[:limit])
    elif before_id is not None:
        messages = list(reversed(base.filter(id__lt=before_id).order_by("-id")[:limit]))
    else:
        messages = list(reversed(base.order_by("-id")[:limit]))

    return [serialize_message(message) for message in messages]


def get_conversation_payload(*, company, conversation_id, mark_read: bool = True) -> dict:
    """Carga inicial de un chat. Con `mark_read`, pone `unread_count` a 0 (R4)."""
    conversation = _get_conversation(company, conversation_id)

    if mark_read and conversation.unread_count:
        # UPDATE dirigido: no toca `last_activity_at` ni `updated_at`, para que
        # abrir un chat no lo reordene en la lista.
        Conversation.objects.filter(pk=conversation.pk).update(unread_count=0)
        conversation.unread_count = 0

    messages = list(reversed(Message.objects.filter(conversation=conversation).order_by("-id")[:DETAIL_MESSAGE_LIMIT]))

    return {
        "conversation": serialize_conversation(conversation),
        "messages": [serialize_message(message) for message in messages],
        "contact": serialize_contact(conversation.contact),
    }


def forward_inbound_to_n8n(*, channel, payload: dict, processed: list[dict]) -> dict | None:
    """R7: un único POST por payload de webhook, con el JSON crudo de YCloud.

    Se dispara solo si n8n está configurado, algún mensaje del payload es nuevo
    y su contacto tiene el chatbot encendido.
    """
    if not n8n.is_configured(channel):
        return None
    if not any(item["chatbot_enabled"] and not item["duplicate"] for item in processed):
        return None
    return n8n.forward_ycloud_event(channel, payload)
