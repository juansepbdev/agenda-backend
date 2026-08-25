import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.companies.models import CompanyOwnedModel


class SchedulingConfiguration(CompanyOwnedModel):
    class Strategy(models.TextChoices):
        FIRST_AVAILABLE = "FIRST_AVAILABLE", "First available"
        LEAST_EVENTS = "LEAST_EVENTS", "Least events"
        ROUND_ROBIN = "ROUND_ROBIN", "Round robin"
        PRIORITY = "PRIORITY", "Priority"
        RANDOM = "RANDOM", "Random"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default="Default")
    default_event_duration_minutes = models.PositiveIntegerField(default=60)
    minimum_advance_minutes = models.PositiveIntegerField(default=0)
    maximum_advance_days = models.PositiveIntegerField(default=90)
    default_buffer_before_minutes = models.PositiveIntegerField(default=0)
    default_buffer_after_minutes = models.PositiveIntegerField(default=0)
    assignment_strategy = models.CharField(max_length=20, choices=Strategy.choices, default=Strategy.FIRST_AVAILABLE)
    allow_same_day_booking = models.BooleanField(default=True)
    require_advisor_confirmation = models.BooleanField(default=False)
    allow_automatic_reassignment = models.BooleanField(default=False)
    allow_events_outside_availability = models.BooleanField(default=False)
    allow_advisor_create_events = models.BooleanField(default=True)
    allow_advisor_edit_availability = models.BooleanField(default=True)
    allow_supervisor_create_events = models.BooleanField(default=True)
    allow_supervisor_reassign_events = models.BooleanField(default=True)
    allow_supervisor_edit_availability = models.BooleanField(default=True)
    reminder_minutes_before = models.PositiveIntegerField(default=60)
    timezone = models.CharField(max_length=64, default="America/Bogota")
    is_default = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "users.User", null=True, on_delete=models.PROTECT, related_name="created_scheduling_configurations"
    )
    updated_by = models.ForeignKey(
        "users.User", null=True, on_delete=models.PROTECT, related_name="updated_scheduling_configurations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(is_default=True, is_active=True),
                name="one_active_default_config_per_company",
            ),
            models.CheckConstraint(
                condition=models.Q(default_event_duration_minutes__gt=0), name="config_duration_gt_zero"
            ),
        ]


class Event(CompanyOwnedModel):
    class Type(models.TextChoices):
        PROPERTY_VISIT = "PROPERTY_VISIT", "Property visit"
        CLIENT_MEETING = "CLIENT_MEETING", "Client meeting"
        PHONE_CALL = "PHONE_CALL", "Phone call"
        INTERNAL_MEETING = "INTERNAL_MEETING", "Internal meeting"
        PERSONAL_BLOCK = "PERSONAL_BLOCK", "Personal block"
        LUNCH = "LUNCH", "Lunch"
        VACATION = "VACATION", "Vacation"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No show"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"

    class Source(models.TextChoices):
        CHATBOT = "CHATBOT", "Chatbot"
        MANUAL = "MANUAL", "Manual"
        SYSTEM = "SYSTEM", "System"
        API = "API", "API"
        CALENDAR_SYNC = "CALENDAR_SYNC", "Calendar sync"

    class NoShow(models.TextChoices):
        CLIENT_NO_SHOW = "CLIENT_NO_SHOW", "Client"
        ADVISOR_NO_SHOW = "ADVISOR_NO_SHOW", "Advisor"
        UNKNOWN = "UNKNOWN", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    advisor = models.ForeignKey("advisors.Advisor", on_delete=models.PROTECT, related_name="events")
    client = models.ForeignKey("clients.Client", null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=24, choices=Type.choices, default=Type.PROPERTY_VISIT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default="America/Bogota")
    location = models.CharField(max_length=255, blank=True)
    meeting_url = models.URLField(blank=True)
    property_external_id = models.CharField(max_length=128, blank=True)
    property_code = models.CharField(max_length=128, blank=True)
    property_title = models.CharField(max_length=255, blank=True)
    property_address = models.CharField(max_length=255, blank=True)
    property_url = models.URLField(blank=True)
    chatbot_conversation_id = models.CharField(max_length=128, blank=True)
    chatbot_message_id = models.CharField(max_length=128, blank=True)
    external_reference = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    assigned_automatically = models.BooleanField(default=False)
    requires_confirmation = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="confirmed_events"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="started_events"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="completed_events"
    )
    completion_notes = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="cancelled_events"
    )
    cancellation_reason = models.TextField(blank=True)
    cancellation_source = models.CharField(max_length=16, blank=True)
    no_show_type = models.CharField(max_length=20, choices=NoShow.choices, blank=True)
    no_show_notes = models.TextField(blank=True)
    rescheduled_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="rescheduled_children"
    )
    rescheduled_to = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="rescheduled_parent"
    )
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="created_events"
    )
    updated_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.PROTECT, related_name="updated_events"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(end_at__gt=models.F("start_at")), name="event_end_after_start"),
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_event_idempotency_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "advisor", "start_at"]),
            models.Index(fields=["company", "status", "start_at"]),
        ]

    def clean(self):
        if self.end_at <= self.start_at:
            raise ValidationError("La fecha final debe ser posterior a la inicial.")
        for relation in (self.advisor, self.client):
            if relation and relation.company_id != self.company_id:
                raise ValidationError("No se permiten relaciones entre empresas.")


class EventHistory(models.Model):
    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        ASSIGNED = "ASSIGNED", "Assigned"
        CONFIRMED = "CONFIRMED", "Confirmed"
        STARTED = "STARTED", "Started"
        COMPLETED = "COMPLETED", "Completed"
        UPDATED = "UPDATED", "Updated"
        CANCELLED = "CANCELLED", "Cancelled"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"
        REASSIGNED = "REASSIGNED", "Reassigned"
        NO_SHOW = "NO_SHOW", "No show"
        REMINDER_SENT = "REMINDER_SENT", "Reminder sent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("companies.Company", on_delete=models.PROTECT, related_name="event_histories")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=16, choices=Action.choices)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    previous_data = models.JSONField(default=dict, blank=True)
    new_data = models.JSONField(default=dict, blank=True)
    actor_user = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.PROTECT)
    actor_type = models.CharField(max_length=16, default="USER")
    source = models.CharField(max_length=16, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["company", "event", "created_at"])]


class AdvisorAssignmentState(CompanyOwnedModel):
    advisor = models.OneToOneField("advisors.Advisor", on_delete=models.PROTECT, related_name="assignment_state")
    last_assigned_at = models.DateTimeField(null=True, blank=True)
