"""Modelo de datos del CRM conversacional de WhatsApp.

Adaptación multiempresa de la referencia técnica: cada fila cuelga de una
`Company` y las claves naturales (teléfono, `display_id`, `wa_message_id`) son
únicas *dentro* del tenant, no globalmente.

`Message` es la única tabla con PK entera: el contrato de paginación por cursor
(`after_id` / `before_id`) exige identificadores monótonos y comparables, y el
índice caliente es `(conversation, id DESC)`.
"""

import hashlib
import secrets
import uuid

from django.db import models, transaction

from apps.companies.models import Company, CompanyOwnedModel, TimeStampedModel

from .utils import (
    avatar_color_for_phone,
    avatar_initial_for,
    normalize_phone,
    source_id_for_phone,
)

DEFAULT_INBOX_NAME = "Inmobiliar-ia"
WEBHOOK_KEY_PREFIX = "wak_"


def _hash_webhook_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class WhatsAppChannel(TimeStampedModel):
    """Configuración y credencial de webhook de una empresa.

    Reemplaza las variables de entorno globales de la referencia: en un backend
    multiempresa cada tenant tiene su propio número, su propia API key de YCloud
    y su propio flujo de n8n.

    La credencial entrante (`X-API-Key`) se guarda **hasheada**; el valor en
    claro se muestra una sola vez, al generarla.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="whatsapp_channel")
    inbox_name = models.CharField(max_length=128, default=DEFAULT_INBOX_NAME)

    ycloud_api_key = models.CharField(max_length=255, blank=True)
    ycloud_from = models.CharField(max_length=20, blank=True)
    ycloud_api_base = models.CharField(max_length=255, blank=True)

    n8n_webhook_url = models.URLField(max_length=512, blank=True)
    n8n_timeout_seconds = models.FloatField(default=5.0)

    verify_token = models.CharField(max_length=128, blank=True)
    webhook_key_prefix = models.CharField(max_length=16, blank=True)
    webhook_key_hash = models.CharField(max_length=64, blank=True, db_index=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["webhook_key_hash"],
                condition=~models.Q(webhook_key_hash=""),
                name="unique_inbox_webhook_key_hash",
            )
        ]

    def __str__(self):
        return f"WhatsApp · {self.company.name}"

    def save(self, *args, **kwargs):
        self.ycloud_from = normalize_phone(self.ycloud_from)
        super().save(*args, **kwargs)

    # -- credencial ----------------------------------------------------------

    def rotate_webhook_key(self, *, save=True) -> str:
        """Genera una credencial nueva, guarda su hash y devuelve el valor en claro."""
        raw_key = f"{WEBHOOK_KEY_PREFIX}{secrets.token_hex(20)}"
        self.webhook_key_prefix = raw_key[:12]
        self.webhook_key_hash = _hash_webhook_key(raw_key)
        if save:
            self.save(update_fields=["webhook_key_prefix", "webhook_key_hash", "updated_at"])
        return raw_key

    @classmethod
    def resolve(cls, raw_key: str):
        """Devuelve el canal activo dueño de la credencial, o `None`."""
        if not raw_key:
            return None
        return (
            cls.objects.select_related("company")
            .filter(webhook_key_hash=_hash_webhook_key(raw_key.strip()), is_active=True)
            .first()
        )

    @property
    def can_send(self) -> bool:
        return bool(self.ycloud_api_key and self.ycloud_from)


class ConversationSequence(TimeStampedModel):
    """Contador atómico de `display_id` por empresa.

    La referencia calcula `MAX(display_id) + 1`, que compite consigo mismo bajo
    webhooks concurrentes. Una fila bloqueable con `SELECT ... FOR UPDATE` no.
    """

    company = models.OneToOneField(Company, on_delete=models.PROTECT, primary_key=True, related_name="inbox_sequence")
    last_display_id = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.company_id}: {self.last_display_id}"

    @classmethod
    def allocate(cls, company) -> int:
        with transaction.atomic():
            cls.objects.get_or_create(company=company)
            sequence = cls.objects.select_for_update().get(company=company)
            sequence.last_display_id += 1
            sequence.save(update_fields=["last_display_id", "updated_at"])
            return sequence.last_display_id


class Contact(CompanyOwnedModel):
    """Persona al otro lado de WhatsApp. Clave natural: `(company, phone_number)`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(max_length=150, blank=True)
    country = models.CharField(max_length=64, default="Colombia")
    avatar_initial = models.CharField(max_length=2, blank=True)
    avatar_color = models.CharField(max_length=7, blank=True)

    # Interruptor de negocio más importante del sistema (R1).
    chatbot_enabled = models.BooleanField(default=True)

    nickname = models.CharField(max_length=128, blank=True)
    owner = models.CharField(max_length=128, default="—", blank=True)
    source = models.CharField(max_length=128, default="Inbound message", blank=True)
    source_id = models.CharField(max_length=128, blank=True)
    source_url = models.CharField(max_length=512, blank=True)
    tags = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    last_contact_at = models.DateTimeField(null=True, blank=True)

    # Puente con la agenda: una conversación de WhatsApp puede acabar en evento.
    client = models.ForeignKey(
        "clients.Client",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbox_contacts",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "phone_number"], name="unique_inbox_contact_phone_per_company")
        ]
        indexes = [
            models.Index(fields=["company", "phone_number"]),
            models.Index(fields=["company", "last_contact_at"]),
        ]

    def __str__(self):
        return self.name or self.phone_number

    def save(self, *args, **kwargs):
        self.phone_number = normalize_phone(self.phone_number)
        if not self.avatar_initial:
            self.avatar_initial = avatar_initial_for(self.name, self.phone_number)
        if not self.avatar_color:
            # Sin default fijo en la columna: así cada contacto nace con su color.
            self.avatar_color = avatar_color_for_phone(self.phone_number)
        if not self.source_id:
            self.source_id = source_id_for_phone(self.phone_number)
        super().save(*args, **kwargs)


class Conversation(CompanyOwnedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        PENDING = "pending", "Pending"

    class Assignment(models.TextChoices):
        UNASSIGNED = "unassigned", "Unassigned"
        ME = "me", "Agent"
        BOT = "bot", "Bot"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="conversations")
    display_id = models.PositiveIntegerField(null=True, blank=True)
    inbox = models.CharField(max_length=128, default=DEFAULT_INBOX_NAME)
    channel = models.CharField(max_length=32, default="whatsapp")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assignment = models.CharField(max_length=16, choices=Assignment.choices, default=Assignment.UNASSIGNED)
    unread_count = models.PositiveIntegerField(default=0)
    last_message_preview = models.CharField(max_length=255, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "display_id"], name="unique_inbox_display_id_per_company")
        ]
        indexes = [
            models.Index(fields=["company", "-last_activity_at"]),
            models.Index(fields=["company", "assignment"]),
            models.Index(fields=["contact", "status"]),
        ]

    def __str__(self):
        return f"#{self.display_id} · {self.contact_id}"

    def save(self, *args, **kwargs):
        if self.display_id is None:
            self.display_id = ConversationSequence.allocate(self.company)
        super().save(*args, **kwargs)


class Message(models.Model):
    """Entrantes y salientes en una sola tabla, diferenciadas por `message_type`."""

    class Type(models.IntegerChoices):
        INBOUND = 0, "Inbound"
        OUTBOUND = 1, "Outbound"

    class Sender(models.TextChoices):
        CONTACT = "contact", "Contact"
        BOT = "bot", "Bot"
        AGENT = "agent", "Agent"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        RECEIVED = "received", "Received"

    class ContentType(models.TextChoices):
        TEXT = "text", "Text"
        SYSTEM = "system", "System"

    # PK entera a propósito: sostiene el cursor `after_id` / `before_id`.
    id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="inbox_messages")
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    # Denormalizado a propósito: permite analítica por contacto sin JOIN.
    contact = models.ForeignKey(Contact, null=True, on_delete=models.SET_NULL, related_name="messages")
    content = models.TextField()
    message_type = models.SmallIntegerField(choices=Type.choices)
    sender_type = models.CharField(max_length=20, choices=Sender.choices, default=Sender.CONTACT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    content_type = models.CharField(max_length=16, choices=ContentType.choices, default=ContentType.TEXT)
    wa_message_id = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "wa_message_id"],
                condition=models.Q(wa_message_id__isnull=False),
                name="unique_inbox_wa_message_id_per_company",
            )
        ]
        indexes = [
            models.Index(fields=["conversation", "-id"], name="idx_inbox_conv_id_desc"),
            models.Index(fields=["conversation", "created_at"], name="idx_inbox_conv_created"),
            models.Index(fields=["contact", "created_at"], name="idx_inbox_contact_created"),
            models.Index(fields=["sender_type", "created_at"], name="idx_inbox_sender_created"),
        ]
        ordering = ["id"]

    def __str__(self):
        return f"{self.id} · {self.sender_type}"

    @property
    def direction(self) -> str:
        if self.content_type == self.ContentType.SYSTEM:
            return "system"
        return "inbound" if self.message_type == self.Type.INBOUND else "outbound"
