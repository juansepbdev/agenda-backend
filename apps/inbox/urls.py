from django.urls import path

from .views import (
    contact_chatbot,
    conversation_claim,
    conversation_detail,
    conversation_list,
    conversation_messages,
    conversation_release,
    manual_incoming,
    message_store,
    n8n_bot_reply,
    whatsapp_webhook,
)

urlpatterns = [
    path("conversations/", conversation_list, name="inbox-conversation-list"),
    path("conversations/<uuid:conversation_id>/", conversation_detail, name="inbox-conversation-detail"),
    path("conversations/<uuid:conversation_id>/messages/", conversation_messages, name="inbox-conversation-messages"),
    path("conversations/<uuid:conversation_id>/claim/", conversation_claim, name="inbox-conversation-claim"),
    path("conversations/<uuid:conversation_id>/release/", conversation_release, name="inbox-conversation-release"),
    path("contacts/<uuid:contact_id>/chatbot/", contact_chatbot, name="inbox-contact-chatbot"),
    path("messages/", message_store, name="inbox-message-store"),
    path("messages/incoming/", manual_incoming, name="inbox-manual-incoming"),
    path("webhook/whatsapp/", whatsapp_webhook, name="inbox-whatsapp-webhook"),
    path("n8n/bot-reply/", n8n_bot_reply, name="inbox-n8n-bot-reply"),
]
