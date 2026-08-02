from django.contrib import admin

from apps.companies.admin import TenantAdminMixin

from .models import Advisor, AdvisorAvailability, AdvisorSupervision


@admin.register(Advisor)
class AdvisorAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("code", "user", "company", "is_active", "is_available", "accepts_automatic_assignments", "assignment_priority")
    list_filter = ("company", "is_active", "is_available", "accepts_automatic_assignments")
    search_fields = ("code", "user__email", "user__first_name", "user__last_name")
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("company", "user")


@admin.register(AdvisorSupervision)
class AdvisorSupervisionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("advisor", "supervisor_user", "company", "valid_from", "valid_until", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("advisor__code", "supervisor_user__email")
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("company", "advisor", "supervisor_user")


@admin.register(AdvisorAvailability)
class AdvisorAvailabilityAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("advisor", "company", "day_of_week", "start_time", "end_time", "slot_duration_minutes", "is_active")
    list_filter = ("company", "day_of_week", "is_active")
    search_fields = ("advisor__code", "advisor__user__email")
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("company", "advisor")
