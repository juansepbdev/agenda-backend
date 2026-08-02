from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.companies.admin import TenantAdminMixin

from .models import User


@admin.register(User)
class CustomUserAdmin(TenantAdminMixin, UserAdmin):
    list_display = ("email", "first_name", "last_name", "company", "role", "is_active", "is_staff")
    list_filter = ("company", "role", "is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("email",)
    readonly_fields = ("id", "created_at", "updated_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")} ),
        ("Perfil", {"fields": ("company", "first_name", "last_name", "phone", "role")} ),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")} ),
        ("Auditoría", {"fields": ("created_by", "updated_by", "created_at", "updated_at", "last_login")} ),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "company", "role", "password1", "password2")} ),)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name in {"created_by", "updated_by"}:
            kwargs["queryset"] = User.objects.filter(company=request.user.company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
