import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("El correo es obligatorio.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        SUPERVISOR = "SUPERVISOR", "Supervisor"
        ADVISOR = "ADVISOR", "Advisor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    company = models.ForeignKey("companies.Company", null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.ADVISOR)
    created_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="created_users")
    updated_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="updated_users")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        indexes = [models.Index(fields=["company", "role", "is_active"])]

    def clean(self):
        if not self.is_superuser and not self.company_id:
            raise ValidationError("Los usuarios normales deben pertenecer a una empresa.")
        for field in ("created_by", "updated_by"):
            actor = getattr(self, field)
            if actor and not actor.is_superuser and actor.company_id != self.company_id:
                raise ValidationError({field: "El auditor debe pertenecer a la misma empresa."})

    def __str__(self):
        return self.email
