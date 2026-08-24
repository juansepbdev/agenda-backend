"""Vistas HTTP del inbox.

Dos familias:

* **Internas** (`/conversations/`, `/contacts/`, `/messages/incoming/`): exigen
  usuario autenticado y resuelven la empresa desde `request.user.company`.
* **Máquina a máquina** (`/webhook/whatsapp/`, `/n8n/bot-reply/`): sin sesión;
  la empresa se resuelve **solo** desde la credencial `X-API-Key` del canal.
  Nunca desde el cuerpo de la petición.

Los errores de dominio se propagan como `DomainError` y el manejador global de
DRF los serializa con el envoltorio `{"error": {...}}` del resto de la API.
"""

import logging

from django.http import HttpResponse
from django.utils.crypto import constant_time_compare
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import NotAuthenticated, ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.scheduling.exceptions import CompanyInactiveError, PermissionScopeError

from .exceptions import InboxValidationError, InvalidWebhookCredentialError
from .models import WhatsAppChannel
from .services import messaging

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Resolución de tenant
# -----------------------------------------------------------------------------

def _company(request):
    company = getattr(request.user, "company", None)
    if company is None:
        raise PermissionScopeError("El usuario no pertenece a ninguna empresa.")
    return company


def _channel_of(company):
    """Canal configurado de la empresa, o `None` (el inbox funciona sin él)."""
    return WhatsAppChannel.objects.filter(company=company, is_active=True).first()


def _channel_from_credential(request):
    """Resuelve el canal (y con él la empresa) desde la credencial del webhook.

    Se acepta la cabecera `X-API-Key` y, como alternativa para paneles de BSP
    que solo permiten personalizar la URL, el parámetro `api_key`. La cabecera
    es la vía preferida: un secreto en la URL acaba en los logs del proxy.
    """
    raw_key = request.headers.get("X-API-Key") or request.query_params.get("api_key") or ""
    channel = WhatsAppChannel.resolve(raw_key)
    if channel is None:
        raise InvalidWebhookCredentialError("Credencial de webhook inválida o inactiva.")
    return channel


def _int_param(request, name, default=None):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _company_and_channel(request):
    """Resuelve el tenant por credencial de canal o, si no la hay, por sesión.

    El orden importa: si llega `X-API-Key` manda la credencial, aunque la
    petición traiga además una cookie de sesión. Así un mismo endpoint sirve al
    panel y al flujo de n8n sin que la identidad de uno pueda suplantar la del
    otro.
    """
    has_credential = bool(request.headers.get("X-API-Key") or request.query_params.get("api_key"))
    if has_credential:
        channel = _channel_from_credential(request)
        return channel.company, channel

    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated("Autentícate con sesión o con la cabecera X-API-Key del canal.")

    company = _company(request)
    return company, _channel_of(company)


def _payload(request):
    """Cuerpo JSON, o error de dominio si viene mal formado."""
    try:
        data = request.data
    except ParseError as exc:
        raise InboxValidationError("JSON inválido.") from exc
    if not isinstance(data, dict):
        raise InboxValidationError("Se esperaba un objeto JSON.")
    return data


# -----------------------------------------------------------------------------
# Endpoints internos (dashboard)
# -----------------------------------------------------------------------------

@extend_schema(
    summary="Lista de conversaciones ordenada por actividad descendente.",
    parameters=[OpenApiParameter("filter", str, description="all | me | unassigned | bot")],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def conversation_list(request):
    company = _company(request)
    conversations = messaging.list_conversations(
        company=company, filter_id=request.query_params.get("filter", "all")
    )
    return Response({"conversations": conversations})


@extend_schema(
    summary="Carga inicial de un chat: conversación, últimos 100 mensajes y contacto.",
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def conversation_detail(request, conversation_id):
    """Carga inicial de un chat. Efecto secundario: pone `unread_count` a 0 (R4)."""
    company = _company(request)
    return Response(messaging.get_conversation_payload(company=company, conversation_id=conversation_id))


@extend_schema(
    summary="GET: mensajes paginados por cursor. POST: envío del asesor.",
    parameters=[
        OpenApiParameter("limit", int, description="1..200, por defecto 50."),
        OpenApiParameter("after_id", int, description="Mensajes con id mayor, orden ascendente."),
        OpenApiParameter("before_id", int, description="Mensajes con id menor (scroll hacia arriba)."),
    ],
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET", "POST"])
def conversation_messages(request, conversation_id):
    company = _company(request)

    if request.method == "GET":
        messages = messaging.list_messages(
            company=company,
            conversation_id=conversation_id,
            limit=_int_param(request, "limit"),
            after_id=_int_param(request, "after_id"),
            before_id=_int_param(request, "before_id"),
        )
        return Response({"messages": messages})

    if not company.can_operate:
        raise CompanyInactiveError("La empresa no está activa.")

    data = _payload(request)
    result = messaging.send_agent_message(
        company=company,
        conversation_id=conversation_id,
        content=data.get("content", ""),
        channel=_channel_of(company),
    )
    return Response(result, status=201)


@extend_schema(
    summary="Enciende o apaga el chatbot de un contacto.",
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["POST"])
def contact_chatbot(request, contact_id):
    """Interruptor del bot por contacto (R1)."""
    company = _company(request)
    data = _payload(request)
    if "enabled" not in data:
        raise InboxValidationError("El campo `enabled` es obligatorio.")
    return Response(
        messaging.set_chatbot_enabled(company=company, contact_id=contact_id, enabled=bool(data["enabled"]))
    )


@extend_schema(
    summary="Ingesta manual de un entrante, para probar sin WhatsApp.",
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["POST"])
def manual_incoming(request):
    """Ingesta manual para probar sin WhatsApp.

    No reenvía a n8n: el puente con el bot vive únicamente en el webhook.
    """
    company = _company(request)
    data = _payload(request)
    phone = data.get("phone") or data.get("phone_number") or ""
    content = data.get("content") or data.get("message") or ""
    if not phone:
        raise InboxValidationError("El teléfono es obligatorio.")

    result = messaging.ingest_inbound_text(
        company=company,
        phone=phone,
        text=content,
        name=data.get("name", ""),
        wa_message_id=data.get("wa_message_id"),
        channel=_channel_of(company),
    )
    return Response({"status": "ok", **result})


# -----------------------------------------------------------------------------
# Endpoints máquina a máquina
# -----------------------------------------------------------------------------

@extend_schema(
    summary="Webhook de YCloud. Autenticado con la credencial del canal (X-API-Key).",
    parameters=[
        OpenApiParameter("api_key", str, description="Alternativa a la cabecera X-API-Key."),
    ],
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def whatsapp_webhook(request):
    """Webhook de YCloud. **Siempre responde 200** si la credencial es válida.

    Un webhook que devuelve error provoca reintentos innecesarios del BSP, así
    que los payloads no procesables se acusan con `status: "ignored"`.
    """
    if request.method == "GET":
        return _verify_webhook(request)

    channel = _channel_from_credential(request)

    if not channel.company.can_operate:
        return Response({"status": "ignored", "reason": "company_inactive"})

    try:
        payload = request.data
    except ParseError:
        logger.warning("webhook: JSON inválido descartado")
        return Response({"status": "ignored", "reason": "invalid_json"})

    if not isinstance(payload, dict):
        return Response({"status": "ignored", "reason": "unexpected_payload"})

    events = messaging.parse_whatsapp_webhook(payload)
    processed = []
    for event in events:
        try:
            processed.append(
                messaging.ingest_inbound_text(
                    company=channel.company,
                    phone=event["phone"],
                    text=event["text"],
                    name=event.get("name", ""),
                    wa_message_id=event.get("wa_message_id"),
                    timestamp=event.get("timestamp"),
                    channel=channel,
                )
            )
        except Exception:
            logger.exception("webhook: fallo procesando un mensaje entrante")

    body = {"status": "ok", "event_type": payload.get("type"), "processed": processed}

    # R7: un único reenvío por payload, con el JSON crudo tal cual llegó.
    forward = messaging.forward_inbound_to_n8n(channel=channel, payload=payload, processed=processed)
    if forward is not None:
        body["n8n_forward"] = forward

    return Response(body)


def _verify_webhook(request):
    """Handshake estilo Meta. YCloud no lo usa; está por compatibilidad."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token") or ""
    challenge = request.query_params.get("hub.challenge") or ""

    if mode == "subscribe" and token:
        for candidate in WhatsAppChannel.objects.filter(is_active=True).exclude(verify_token=""):
            if constant_time_compare(candidate.verify_token, token):
                return HttpResponse(challenge, content_type="text/plain")

    return Response({"error": {"code": "VERIFICATION_FAILED", "message": "Verificación fallida.", "details": {}}}, status=403)


@extend_schema(
    summary="Callback del agente de n8n. Autenticado con la credencial del canal (X-API-Key).",
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def n8n_bot_reply(request):
    """Callback del agente de IA: persiste la respuesta del bot y la despacha."""
    channel = _channel_from_credential(request)

    if not channel.company.can_operate:
        return Response({"status": "skipped", "reason": "company_inactive"})

    data = _payload(request)
    phone = data.get("phone") or data.get("phone_number") or ""
    text = data.get("text") or data.get("content") or ""
    if not phone:
        raise InboxValidationError("El campo `phone` es obligatorio.")
    if not str(text).strip():
        raise InboxValidationError("El campo `text` es obligatorio.")

    result = messaging.send_bot_reply_from_n8n(
        company=channel.company,
        phone=phone,
        text=text,
        channel=channel,
        message_id=data.get("message_id"),
        update_id=data.get("update_id"),
    )
    status_code = 201 if result["status"] == "ok" else 200
    return Response(result, status=status_code)


@extend_schema(
    summary="Guarda uno o varios mensajes en el historial, sin enviarlos a WhatsApp.",
    description=(
        "Persistencia pura: no llama a YCloud ni reenvía a n8n. Autentica con "
        "sesión del panel o con la cabecera `X-API-Key` del canal. Idempotente "
        "por `wa_message_id` dentro de la empresa. Para un lote, envía "
        "`{\"messages\": [...]}` (máximo 200)."
    ),
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["POST"])
@permission_classes([AllowAny])
def message_store(request):
    """`POST /inbox/messages/` — registra en el historial lo ya ocurrido fuera."""
    company, channel = _company_and_channel(request)

    if not company.can_operate:
        raise CompanyInactiveError("La empresa no está activa.")

    data = _payload(request)

    if "messages" in data:
        result = messaging.store_messages(company=company, items=data["messages"], channel=channel)
        # 207: el lote puede tener éxitos y fallos a la vez y ambos importan.
        status_code = 207 if result["errors"] else 201
        return Response(result, status=status_code)

    result = messaging.store_from_payload(company=company, data=data, channel=channel)
    return Response(result, status=200 if result["duplicate"] else 201)
