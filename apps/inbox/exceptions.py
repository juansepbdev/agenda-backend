"""Errores de dominio del inbox.

Se apoyan en `DomainError` de scheduling para que el manejador global de DRF
los serialice con el mismo envoltorio `{"error": {"code", "message", "details"}}`
que usa el resto de la API.
"""

from apps.scheduling.exceptions import DomainError


class ChatbotEnabledError(DomainError):
    """R1: con el chatbot encendido el asesor no puede responder."""

    code = "CHATBOT_ENABLED"
    status_code = 403


class ConversationNotFoundError(DomainError):
    code = "CONVERSATION_NOT_FOUND"
    status_code = 404


class ContactNotFoundError(DomainError):
    code = "CONTACT_NOT_FOUND"
    status_code = 404


class InvalidWebhookCredentialError(DomainError):
    code = "INVALID_WEBHOOK_CREDENTIAL"
    status_code = 401


class InboxValidationError(DomainError):
    code = "INBOX_VALIDATION_ERROR"
    status_code = 400
