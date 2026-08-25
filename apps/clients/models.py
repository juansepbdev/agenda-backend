import uuid

from django.db import models

from apps.companies.models import CompanyOwnedModel


class Client(CompanyOwnedModel):
    class Source(models.TextChoices):
        CHATBOT = "CHATBOT", "Chatbot"
        MANUAL = "MANUAL", "Manual"
        WEBSITE = "WEBSITE", "Website"
        PHONE_CALL = "PHONE_CALL", "Phone call"
        OTHER = "OTHER", "Other"

    class Channel(models.TextChoices):
        WHATSAPP = "WHATSAPP", "WhatsApp"
        PHONE = "PHONE", "Phone"
        EMAIL = "EMAIL", "Email"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32)
    normalized_phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    document_type = models.CharField(max_length=30, blank=True)
    document_number = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    preferred_contact_channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.WHATSAPP)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "normalized_phone"], name="unique_client_phone_per_company")
        ]
        indexes = [models.Index(fields=["company", "normalized_phone"]), models.Index(fields=["company", "last_name"])]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()
