import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.companies.models import CompanyOwnedModel


class Advisor(CompanyOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField("users.User", on_delete=models.PROTECT, related_name="advisor")
    code = models.CharField(max_length=40)
    phone = models.CharField(max_length=32, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    default_event_duration_minutes = models.PositiveIntegerField(default=60)
    max_daily_events = models.PositiveIntegerField(null=True, blank=True)
    assignment_priority = models.PositiveIntegerField(default=100, help_text="Un número menor tiene mayor prioridad.")
    accepts_automatic_assignments = models.BooleanField(default=True)
    is_available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_advisor_code_per_company"),
            models.CheckConstraint(condition=models.Q(default_event_duration_minutes__gt=0), name="advisor_duration_gt_zero"),
            models.CheckConstraint(condition=models.Q(max_daily_events__isnull=True) | models.Q(max_daily_events__gt=0), name="advisor_daily_limit_gt_zero"),
        ]
        indexes = [models.Index(fields=["company", "is_active", "is_available"])]

    def clean(self):
        if self.user_id and self.user.company_id != self.company_id:
            raise ValidationError("El usuario del asesor debe pertenecer a la misma empresa.")

    def __str__(self): return f"{self.code} - {self.user}"


class AdvisorSupervision(CompanyOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supervisor_user = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="supervised_assignments")
    advisor = models.ForeignKey(Advisor, on_delete=models.PROTECT, related_name="supervisions")
    assigned_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="assigned_supervisions")
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "advisor"], condition=models.Q(is_active=True), name="one_active_supervision_per_advisor")]
        indexes = [models.Index(fields=["company", "supervisor_user", "is_active"])]

    def clean(self):
        if self.supervisor_user.company_id != self.company_id or self.advisor.company_id != self.company_id or self.assigned_by.company_id != self.company_id:
            raise ValidationError("Todas las relaciones deben pertenecer a la empresa.")
        if self.supervisor_user.role not in {"SUPERVISOR", "ADMIN"}:
            raise ValidationError("El supervisor debe tener rol supervisor o administrador.")
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError("La fecha final no puede ser anterior a la inicial.")


class AdvisorAvailability(CompanyOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    advisor = models.ForeignKey(Advisor, on_delete=models.PROTECT, related_name="availabilities")
    day_of_week = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_duration_minutes = models.PositiveIntegerField(default=60)
    buffer_before_minutes = models.PositiveIntegerField(default=0)
    buffer_after_minutes = models.PositiveIntegerField(default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    configured_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="configured_availabilities")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["advisor", "day_of_week", "start_time", "end_time"], name="unique_availability_block"),
            models.CheckConstraint(condition=models.Q(day_of_week__gte=0) & models.Q(day_of_week__lte=6), name="availability_valid_day"),
            models.CheckConstraint(condition=models.Q(slot_duration_minutes__gt=0), name="availability_slot_gt_zero"),
        ]

    def clean(self):
        if self.end_time <= self.start_time: raise ValidationError("La hora final debe ser posterior a la inicial.")
        if self.advisor_id and self.advisor.company_id != self.company_id: raise ValidationError("El asesor debe pertenecer a la empresa.")
        if self.configured_by_id and self.configured_by.company_id != self.company_id: raise ValidationError("El configurador debe pertenecer a la empresa.")
