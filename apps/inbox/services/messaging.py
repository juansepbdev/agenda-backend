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
from apps.scheduling.exceptions import DomainError

from ..exceptions import (
    AdvisorNotAssignableError,
    ChatbotEnabledError,
    ContactNotFoundError,
    ConversationNotFoundError,
    InboxValidationError,
)
from ..models import Contact, Conversation, Message
from ..selectors import advisors_assignable_by, conversations_visible_to_user
from ..serializers import serialize_contact, serialize_conversation, serialize_message
from ..utils import normalize_phone, parse_wa_timestamp
from . import n8n, realtime, ycloud
from .assignment import pick_advisor

logger = logging.getLogger(__name__)

PREVIEW_MAX_LENGTH = 255
DETAIL_MESSAGE_LIMIT = 100
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

CONVERSATION_FILTERS = {"all", "me", "unassigned", "bot"}
# Valor de `?advisor=` que pide las conversaciones sin dueño.
UNASSIGNED_ADVISOR = "none"


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
    # Reparto automático al nacer la conversación: es el único punto por el que
    # nace una. Sin asesores elegibles queda sin dueño, en la bandeja que ven
    # administración y supervisión; nunca se lanza desde aquí, porque este
    # camino lo recorre el webhook entrante.
    return Conversation.objects.create(
        company_id=contact.company_id,
        contact=contact,
        inbox=inbox_name,
        advisor=pick_advisor(company=contact.company),
    )


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


def send_agent_message(*, user, conversation_id, content: str, channel=None) -> dict:
    """Envío del asesor. Lanza `ChatbotEnabledError` si el chatbot está encendido (R1)."""
    body = (content or "").strip()
    if not body:
        raise InboxValidationError("El contenido del mensaje es obligatorio.")

    with transaction.atomic():
        conversation = _get_visible_conversation(user, conversation_id)
        company = conversation.company
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

def set_chatbot_enabled(*, user, contact_id, enabled: bool) -> dict:
    # El contacto se acota por las conversaciones visibles: sin eso, un asesor
    # podría apagarle el bot a un contacto de otro asesor conociendo su id.
    visible = conversations_visible_to_user(user=user).filter(contact_id=contact_id)
    contact = Contact.objects.filter(pk=contact_id).filter(conversations__in=visible).first()
    if contact is None:
        raise ContactNotFoundError("El contacto no existe.")
    company = contact.company

    contact.chatbot_enabled = bool(enabled)
    contact.save(update_fields=["chatbot_enabled", "updated_at"])

    contact_data = serialize_contact(contact)
    realtime.broadcast_contact_updated(company_id=company.pk, contact_data=contact_data)
    return {"contact_id": str(contact.id), "chatbot_enabled": contact.chatbot_enabled, "contact": contact_data}


def claim_conversation(*, user, conversation_id, advisor_id=None) -> dict:
    """Tomar (o reasignar) una conversación: apaga el bot y le pone dueño.

    Es una sola acción a propósito: responder con el bot encendido devuelve 403
    (R1), así que separar "asignar" de "apagar el bot" solo serviría para dejar
    al asesor a medio camino.
    """
    conversation = _get_visible_conversation(user, conversation_id)

    if advisor_id:
        advisor = advisors_assignable_by(user).filter(pk=advisor_id).first()
        if advisor is None:
            raise AdvisorNotAssignableError("Ese asesor no existe o no está a tu alcance.")
    else:
        advisor = getattr(user, "advisor", None)
        if advisor is None:
            raise AdvisorNotAssignableError("No eres asesor: indica `advisor_id` para asignar la conversación.")

    contact = conversation.contact
    with transaction.atomic():
        Conversation.objects.filter(pk=conversation.pk).update(
            advisor=advisor, assignment=Conversation.Assignment.ME, updated_at=timezone.now()
        )
        if contact.chatbot_enabled:
            contact.chatbot_enabled = False
            contact.save(update_fields=["chatbot_enabled", "updated_at"])

    conversation.refresh_from_db(fields=["advisor", "assignment", "updated_at"])
    realtime.broadcast_conversations_changed(company_id=conversation.company_id)
    return {
        "conversation": serialize_conversation(conversation),
        "contact": serialize_contact(contact),
    }


def release_conversation(*, user, conversation_id) -> dict:
    """Devolver la conversación al chatbot. El dueño se conserva."""
    conversation = _get_visible_conversation(user, conversation_id)
    contact = conversation.contact

    with transaction.atomic():
        Conversation.objects.filter(pk=conversation.pk).update(
            assignment=Conversation.Assignment.BOT, updated_at=timezone.now()
        )
        if not contact.chatbot_enabled:
            contact.chatbot_enabled = True
            contact.save(update_fields=["chatbot_enabled", "updated_at"])

    conversation.refresh_from_db(fields=["assignment", "updated_at"])
    realtime.broadcast_conversations_changed(company_id=conversation.company_id)
    return {
        "conversation": serialize_conversation(conversation),
        "contact": serialize_contact(contact),
    }


def list_conversations(*, user, filter_id: str = "all", advisor_id=None) -> list[dict]:
    if filter_id not in CONVERSATION_FILTERS:
        filter_id = "all"

    queryset = conversations_visible_to_user(user=user).order_by("-last_activity_at", "-created_at")
    if filter_id != "all":
        queryset = queryset.filter(assignment=filter_id)
    if advisor_id == UNASSIGNED_ADVISOR:
        queryset = queryset.filter(advisor__isnull=True)
    elif advisor_id:
        queryset = queryset.filter(advisor_id=advisor_id)

    return [serialize_conversation(conversation) for conversation in queryset]


def _get_visible_conversation(user, conversation_id) -> Conversation:
    """Conversación dentro del alcance del usuario, o 404.

    `_get_conversation` sigue siendo la vía de las rutas máquina-a-máquina, que
    resuelven la empresa desde la credencial del canal y no tienen usuario.
    """
    conversation = conversations_visible_to_user(user=user).filter(pk=conversation_id).first()
    if conversation is None:
        raise ConversationNotFoundError("La conversación no existe.")
    return conversation


def _get_conversation(company, conversation_id) -> Conversation:
    conversation = (
        Conversation.objects.select_related("contact").filter(company=company, pk=conversation_id).first()
    )
    if conversation is None:
        raise ConversationNotFoundError("La conversación no existe.")
    return conversation


def list_messages(*, user, conversation_id, limit=None, after_id=None, before_id=None) -> list[dict]:
    """Paginación por cursor, sin OFFSET. Siempre devuelve orden cronológico ascendente."""
    conversation = _get_visible_conversation(user, conversation_id)
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


def get_conversation_payload(*, user, conversation_id, mark_read: bool = True) -> dict:
    """Carga inicial de un chat. Con `mark_read`, pone `unread_count` a 0 (R4)."""
    conversation = _get_visible_conversation(user, conversation_id)

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


# -----------------------------------------------------------------------------
# Persistencia directa — POST /inbox/messages/
# -----------------------------------------------------------------------------
# `ingest_inbound_text` y `send_agent_message` modelan flujos completos: el
# primero acusa un entrante real, el segundo despacha a WhatsApp. Este bloque
# cubre el tercer caso: **solo escribir** en el historial un mensaje que ya
# ocurrió en otro sitio (el agente de n8n, una migración, una prueba). No llama
# a YCloud ni reenvía a n8n; por eso no aplica R1 (el interruptor del chatbot
# regula quién *puede enviar*, no quién puede *registrar lo ya enviado*).

SENDER_DIRECTION = {
    Message.Sender.CONTACT: Message.Type.INBOUND,
    Message.Sender.BOT: Message.Type.OUTBOUND,
    Message.Sender.AGENT: Message.Type.OUTBOUND,
}

SENDER_ASSIGNMENT = {
    Message.Sender.AGENT: Conversation.Assignment.ME,
    Message.Sender.BOT: Conversation.Assignment.BOT,
}

DEFAULT_STATUS = {
    Message.Type.INBOUND: Message.Status.RECEIVED,
    Message.Type.OUTBOUND: Message.Status.SENT,
}

DIRECTION_ALIASES = {
    "inbound": Message.Type.INBOUND,
    "in": Message.Type.INBOUND,
    "incoming": Message.Type.INBOUND,
    "received": Message.Type.INBOUND,
    "0": Message.Type.INBOUND,
    "outbound": Message.Type.OUTBOUND,
    "out": Message.Type.OUTBOUND,
    "outgoing": Message.Type.OUTBOUND,
    "sent": Message.Type.OUTBOUND,
    "1": Message.Type.OUTBOUND,
}

MAX_BATCH_SIZE = 200


def _coerce_choice(value, choices, field: str):
    """Normaliza a minúsculas y valida contra un `TextChoices`."""
    normalized = str(value).strip().lower()
    valid = {choice.value for choice in choices}
    if normalized not in valid:
        raise InboxValidationError(
            f"`{field}` inválido.", details={"received": value, "allowed": sorted(valid)}
        )
    return normalized


def _coerce_direction(value, sender_type):
    """Dirección explícita si viene; si no, la que implica el remitente.

    Una combinación incoherente (`contact` + `outbound`) se rechaza en vez de
    corregirse: un histórico con la dirección mal puesta arruina toda la
    analítica posterior, y es preferible que falle en la escritura.
    """
    implied = SENDER_DIRECTION[sender_type]
    if value in (None, ""):
        return implied

    key = str(value).strip().lower()
    if key not in DIRECTION_ALIASES:
        raise InboxValidationError(
            "`direction` inválida.",
            details={"received": value, "allowed": ["inbound", "outbound"]},
        )

    direction = DIRECTION_ALIASES[key]
    if direction != implied:
        raise InboxValidationError(
            "`direction` no concuerda con `sender_type`.",
            details={"sender_type": sender_type, "direction": key},
        )
    return direction


def store_message(
    *,
    company,
    content,
    phone=None,
    conversation_id=None,
    name="",
    sender_type=None,
    direction=None,
    status=None,
    content_type=None,
    wa_message_id=None,
    timestamp=None,
    channel=None,
    mark_unread=None,
) -> dict:
    """Guarda un mensaje en el historial. Idempotente por `wa_message_id`.

    La conversación se resuelve por `conversation_id` (si viene) o por
    `phone`, creando contacto y conversación cuando hace falta.
    """
    body = (content or "").strip()
    if not body:
        raise InboxValidationError("El contenido del mensaje es obligatorio.")

    sender = (
        Message.Sender.CONTACT
        if sender_type in (None, "")
        else _coerce_choice(sender_type, Message.Sender, "sender_type")
    )
    message_type = _coerce_direction(direction, sender)
    message_status = (
        DEFAULT_STATUS[message_type]
        if status in (None, "")
        else _coerce_choice(status, Message.Status, "status")
    )
    kind = (
        Message.ContentType.TEXT
        if content_type in (None, "")
        else _coerce_choice(content_type, Message.ContentType, "content_type")
    )

    if not conversation_id and not phone:
        raise InboxValidationError("Indica `conversation_id` o `phone`.")

    if wa_message_id:
        existing = (
            Message.objects.select_related("conversation__contact")
            .filter(company=company, wa_message_id=wa_message_id)
            .first()
        )
        if existing:
            conversation = existing.conversation
            return {
                "duplicate": True,
                "contact_id": str(conversation.contact_id),
                "conversation_id": str(conversation.id),
                "display_id": conversation.display_id,
                "chatbot_enabled": conversation.contact.chatbot_enabled,
                "message": serialize_message(existing),
                "conversation": serialize_conversation(conversation),
            }

    is_inbound = message_type == Message.Type.INBOUND
    bump_unread = is_inbound if mark_unread is None else bool(mark_unread)
    moment = timestamp or timezone.now()

    with transaction.atomic():
        if conversation_id:
            conversation = _get_conversation(company, conversation_id)
            contact = conversation.contact
        else:
            contact, conversation = get_or_create_contact_conversation(
                company=company, phone=phone, name=name or "", channel=channel
            )

        after_id = _last_message_id(conversation)

        message = Message.objects.create(
            company_id=company.pk,
            conversation=conversation,
            contact=contact,
            content=body,
            message_type=message_type,
            sender_type=sender,
            status=message_status,
            content_type=kind,
            wa_message_id=wa_message_id or None,
        )
        if timestamp:
            # `created_at` es auto_now_add; se reescribe con la hora real del
            # origen para que las series temporales del dashboard cuadren.
            Message.objects.filter(pk=message.pk).update(created_at=moment)
            message.created_at = moment

        # Los mensajes de sistema son anotaciones: no reasignan la conversación.
        assignment = None if kind == Message.ContentType.SYSTEM else SENDER_ASSIGNMENT.get(sender)
        if is_inbound and contact.chatbot_enabled:
            assignment = Conversation.Assignment.BOT

        _touch_conversation(
            conversation, content=body, moment=moment, assignment=assignment, bump_unread=bump_unread
        )

        if is_inbound and (contact.last_contact_at is None or contact.last_contact_at < moment):
            contact.last_contact_at = moment
            contact.save(update_fields=["last_contact_at", "updated_at"])

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
        "message": serialize_message(message),
        "conversation": serialize_conversation(conversation),
    }


def store_from_payload(*, company, data: dict, channel=None) -> dict:
    """Punto de entrada del endpoint: traduce el cuerpo HTTP y guarda."""
    return store_message(company=company, channel=channel, **_store_kwargs(data))


def store_messages(*, company, items, channel=None) -> dict:
    """Escritura por lotes. Un elemento inválido no tumba a los demás.

    Cada fallo se devuelve con su índice y el mismo envoltorio de error del
    resto de la API, para que el emisor pueda reintentar solo lo que falló.
    """
    if not isinstance(items, list):
        raise InboxValidationError("`messages` debe ser una lista.")
    if not items:
        raise InboxValidationError("`messages` no puede venir vacío.")
    if len(items) > MAX_BATCH_SIZE:
        raise InboxValidationError(
            f"Máximo {MAX_BATCH_SIZE} mensajes por lote.", details={"received": len(items)}
        )

    stored, errors = [], []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": {
                "code": InboxValidationError.code,
                "message": "Cada elemento debe ser un objeto JSON.",
                "details": {},
            }})
            continue
        try:
            stored.append({"index": index, **store_message(company=company, channel=channel, **_store_kwargs(item))})
        except DomainError as exc:
            errors.append({"index": index, "error": {
                "code": exc.code,
                "message": exc.message,
                "details": getattr(exc, "details", {}) or {},
            }})

    return {
        "stored": stored,
        "errors": errors,
        "counts": {
            "received": len(items),
            "created": sum(1 for item in stored if not item["duplicate"]),
            "duplicated": sum(1 for item in stored if item["duplicate"]),
            "failed": len(errors),
        },
    }


def _store_kwargs(data: dict) -> dict:
    """Traduce el cuerpo HTTP a los argumentos de `store_message`.

    Se aceptan alias (`text`, `body`, `phone_number`, `type`) porque los nodos
    de n8n y la referencia técnica no usan los mismos nombres.
    """
    return {
        "phone": data.get("phone") or data.get("phone_number") or None,
        "conversation_id": data.get("conversation_id") or None,
        "content": data.get("content") or data.get("text") or data.get("body") or "",
        "name": data.get("name") or "",
        "sender_type": data.get("sender_type") or data.get("sender"),
        "direction": data.get("direction"),
        "status": data.get("status"),
        "content_type": data.get("content_type") or data.get("type"),
        "wa_message_id": data.get("wa_message_id") or data.get("wamid") or None,
        "timestamp": _parse_optional_timestamp(data.get("timestamp") or data.get("created_at")),
        "mark_unread": data.get("mark_unread"),
    }


def _parse_optional_timestamp(value):
    """`None` si no viene: así `created_at` conserva su `auto_now_add`."""
    return None if value in (None, "") else parse_wa_timestamp(value)
