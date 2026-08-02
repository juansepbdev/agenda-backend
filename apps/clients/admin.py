from django.contrib import admin

from apps.companies.admin import TenantAdminMixin

from .models import Client


@admin.register(Client)
class ClientAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("first_name", "last_name", "phone", "email", "company", "source", "is_active")
    list_filter = ("company", "source", "preferred_contact_channel", "is_active")
    search_fields = ("first_name", "last_name", "phone", "normalized_phone", "email", "document_number")
    readonly_fields = ("id", "normalized_phone", "created_at", "updated_at")
    list_select_related = ("company",)
