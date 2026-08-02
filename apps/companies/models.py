import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Company(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "TRIAL", "Trial"
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    legal_name = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(unique=True)
    nit = models.CharField(max_length=64, unique=True, null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, default="CO")
    timezone = models.CharField(max_length=64, default="America/Bogota")
    default_language = models.CharField(max_length=12, default="es-co")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TRIAL)
    subscription_plan = models.CharField(max_length=80, blank=True)
    subscription_started_at = models.DateTimeField(null=True, blank=True)
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    max_advisors = models.PositiveIntegerField(null=True, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        indexes = [models.Index(fields=["status", "is_active"])]

    @property
    def can_operate(self):
        return self.is_active and self.status in {self.Status.TRIAL, self.Status.ACTIVE}

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at", "updated_at"])

    def __str__(self):
        return self.name


class CompanyOwnedModel(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="%(app_label)s_%(class)ss")

    class Meta:
        abstract = True
