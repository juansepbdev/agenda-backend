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


class FollowUp(CompanyOwnedModel):
    """El seguimiento vigente de un lead. Una fila por lead, no un histórico.

    La cola se **deriva** al consultarla (`apps.clients.selectors`): no hay nada
    precalculado. Esta tabla solo guarda la decisión vigente — se envió, se
    pospuso, se descartó o se gestionó — que es lo que saca al lead de la cola.
    El rastro de los envíos ya está en el hilo del inbox, como mensajes.

    La clave es el **teléfono normalizado**, no el cliente: un lead puede ser
    todavía un contacto de WhatsApp sin ficha de cliente, y el teléfono es lo
    único que ambos comparten. `client` y `contact` son informativos.
    """

    class Reason(models.TextChoices):
        CANCELLED = "CANCELLED", "Cita cancelada"
        NO_SHOW = "NO_SHOW", "El cliente no asistió"
        COMPLETED = "COMPLETED", "Visita sin cierre"
        INACTIVE = "INACTIVE", "Sin citas"

    class Status(models.TextChoices):
        SENT = "SENT", "Mensaje enviado"
        DONE = "DONE", "Gestionado"
        DISMISSED = "DISMISSED", "Descartado"
        SNOOZED = "SNOOZED", "Pospuesto"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    normalized_phone = models.CharField(max_length=32)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="follow_ups")
    contact = models.ForeignKey(
        "inbox.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="follow_ups"
    )
    advisor = models.ForeignKey(
        "advisors.Advisor", null=True, blank=True, on_delete=models.SET_NULL, related_name="follow_ups"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    status = models.CharField(max_length=12, choices=Status.choices)
    #: Solo lo usa `SNOOZED`: la fecha a la que el lead vuelve a la cola.
    due_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    #: `sent`, `failed` o `skipped:<motivo>`. Un envío que no sale nunca se
    #: pierde en silencio: queda escrito por qué.
    message_status = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="created_follow_ups"
    )
    updated_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="updated_follow_ups"
    )

    class Meta:
        constraints = [
            # Es el candado del envío automático: reclamar la fila con un UPDATE
            # condicional antes de enviar hace imposible el mensaje duplicado,
            # aunque el cron se ejecute dos veces.
            models.UniqueConstraint(fields=["company", "normalized_phone"], name="unique_follow_up_per_lead")
        ]
        indexes = [models.Index(fields=["company", "advisor", "due_at"])]

    def __str__(self):
        return f"{self.normalized_phone} · {self.reason} · {self.status}"
