from django.contrib import admin, messages

from .models import (
    Contact,
    Conversation,
    ConversationSequence,
    Message,
    WhatsAppChannel,
)


@admin.register(WhatsAppChannel)
class WhatsAppChannelAdmin(admin.ModelAdmin):
    list_display = ("company", "inbox_name", "ycloud_from", "webhook_key_prefix", "is_active")
    list_filter = ("is_active",)
    search_fields = ("company__name", "ycloud_from")
    readonly_fields = ("webhook_key_prefix", "webhook_key_hash", "created_at", "updated_at")
    actions = ("rotate_webhook_key",)

    @admin.action(description="Generar credencial de webhook nueva")
    def rotate_webhook_key(self, request, queryset):
        for channel in queryset:
            raw_key = channel.rotate_webhook_key()
            # Único momento en que la credencial se ve en claro: solo se guarda su hash.
            self.message_user(request, f"{channel.company.name}: {raw_key}", level=messages.WARNING)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "name", "company", "chatbot_enabled", "owner", "last_contact_at")
    list_filter = ("company", "chatbot_enabled")
    search_fields = ("phone_number", "name", "email", "nickname")
    raw_id_fields = ("client",)
    readonly_fields = ("source_id", "avatar_initial", "avatar_color", "created_at", "updated_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("display_id", "contact", "company", "status", "assignment", "unread_count", "last_activity_at")
    list_filter = ("company", "status", "assignment")
    search_fields = ("contact__phone_number", "contact__name")
    raw_id_fields = ("contact",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender_type", "status", "created_at")
    list_filter = ("company", "sender_type", "status", "content_type")
    search_fields = ("content", "wa_message_id")
    raw_id_fields = ("conversation", "contact")


@admin.register(ConversationSequence)
class ConversationSequenceAdmin(admin.ModelAdmin):
    list_display = ("company", "last_display_id")
