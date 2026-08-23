"""Funciones puras ORM -> dict.

Diferencias deliberadas con la referencia (sección «descartable» de su propio
checklist): no se emiten los alias duplicados (`phone`, `last_message_at`) ni
los campos preformateados (`timestamp`, `last_message_day`, `create_time`). Las
fechas viajan en ISO-8601 con zona y el cliente decide cómo mostrarlas.

Los identificadores UUID se emiten como cadena; `message.id` es entero porque
el frontend hace aritmética con él para los cursores.
"""

from .models import Contact, Conversation, Message


def _iso(value):
    return value.isoformat() if value else None


def serialize_contact(contact: Contact) -> dict:
    return {
        "id": str(contact.id),
        "name": contact.name,
        "phone_number": contact.phone_number,
        "email": contact.email,
        "country": contact.country,
        "avatar_initial": contact.avatar_initial,
        "avatar_color": contact.avatar_color,
        "chatbot_enabled": contact.chatbot_enabled,
        "nickname": contact.nickname,
        "owner": contact.owner,
        "source": contact.source,
        "source_id": contact.source_id,
        "source_url": contact.source_url,
        "tags": contact.tags or [],
        "notes": contact.notes,
        "client_id": str(contact.client_id) if contact.client_id else None,
        "created_at": _iso(contact.created_at),
        "updated_at": _iso(contact.updated_at),
        "last_contact_at": _iso(contact.last_contact_at),
    }


def serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "direction": message.direction,
        "message_type": message.message_type,
        "sender_type": message.sender_type,
        "content": message.content,
        "status": message.status,
        "type": message.content_type,
        "wa_message_id": message.wa_message_id,
        "contact_id": str(message.contact_id) if message.contact_id else None,
        "conversation_id": str(message.conversation_id),
        "created_at": _iso(message.created_at),
        "updated_at": _iso(message.updated_at),
    }


def serialize_conversation(conversation: Conversation, *, include_contact: bool = True) -> dict:
    data = {
        "id": str(conversation.id),
        "display_id": conversation.display_id,
        "contact_id": str(conversation.contact_id),
        "inbox": conversation.inbox,
        "channel": conversation.channel,
        "status": conversation.status,
        "assignment": conversation.assignment,
        "unread_count": conversation.unread_count,
        "last_message_preview": conversation.last_message_preview,
        "last_activity_at": _iso(conversation.last_activity_at),
        "created_at": _iso(conversation.created_at),
        "updated_at": _iso(conversation.updated_at),
    }
    if include_contact:
        data["contact"] = serialize_contact(conversation.contact)
    return data
