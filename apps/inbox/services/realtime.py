"""Difusión de eventos en vivo.

La referencia usa Django Channels; aquí el transporte queda desacoplado porque
el despliegue actual es WSGI (Vercel), donde no hay WebSocket. El dashboard
funciona con el *polling* de respaldo que la propia referencia describe.

`INBOX_REALTIME_BACKEND` (ruta punteada a un callable
`backend(company_id: str, event: str, payload: dict) -> None`) permite enchufar
Channels, Redis pub/sub o SSE sin tocar la capa de servicios. Sin backend
configurado, la difusión es un no-op registrado en log: el sistema degrada sin
romperse.
"""

import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

_backend = None
_backend_loaded = False


def set_backend(backend):
    """Inyecta un backend en caliente (lo usan los tests y quien monte Channels)."""
    global _backend, _backend_loaded
    _backend, _backend_loaded = backend, True


def _get_backend():
    global _backend, _backend_loaded
    if not _backend_loaded:
        path = getattr(settings, "INBOX_REALTIME_BACKEND", "")
        _backend = import_string(path) if path else None
        _backend_loaded = True
    return _backend


def broadcast_event(company_id, event: str, payload: dict) -> None:
    backend = _get_backend()
    if backend is None:
        logger.debug("inbox realtime no configurado; evento %s descartado", event)
        return
    try:
        backend(str(company_id), event, payload)
    except Exception:
        logger.exception("fallo difundiendo el evento %s", event)


def broadcast_messages_updated(*, company_id, conversation_id, after_id, display_id=None) -> None:
    """R5: el evento lleva punteros, nunca el contenido del mensaje."""
    broadcast_event(
        company_id,
        "messages.updated",
        {"conversation_id": str(conversation_id), "after_id": after_id, "display_id": display_id},
    )


def broadcast_conversations_changed(*, company_id) -> None:
    broadcast_event(company_id, "conversations.changed", {})


def broadcast_contact_updated(*, company_id, contact_data: dict) -> None:
    """Única excepción a R5: el contacto es pequeño y no se pagina."""
    broadcast_event(company_id, "contact.updated", {"contact": contact_data})
