from django.contrib import admin

from apps.companies.admin import TenantAdminMixin

from .models import AdvisorAssignmentState, Event, EventHistory, SchedulingConfiguration


@admin.register(Event)
class EventAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("title", "company", "advisor", "client", "status", "event_type", "start_at", "end_at", "source")
    list_filter = ("company", "status", "event_type", "source", "assigned_automatically", "requires_confirmation")
    search_fields = ("title", "description", "property_code", "property_title", "client__first_name", "client__last_name", "client__phone", "advisor__user__email")
    readonly_fields = ("id", "created_at", "updated_at", "confirmed_at", "started_at", "completed_at", "cancelled_at")
    list_select_related = ("company", "advisor__user", "client")
    date_hierarchy = "start_at"


@admin.register(EventHistory)
class EventHistoryAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("event", "company", "action", "previous_status", "new_status", "actor_user", "created_at")
    list_filter = ("company", "action", "actor_type", "source")
    search_fields = ("event__title", "actor_user__email", "notes")
    readonly_fields = ("id", "company", "event", "action", "previous_status", "new_status", "previous_data", "new_data", "actor_user", "actor_type", "source", "notes", "created_at")
    list_select_related = ("company", "event", "actor_user")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(SchedulingConfiguration)
class SchedulingConfigurationAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "assignment_strategy", "is_default", "is_active", "timezone")
    list_filter = ("company", "assignment_strategy", "is_default", "is_active")
    search_fields = ("name", "company__name")
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("company",)


@admin.register(AdvisorAssignmentState)
class AdvisorAssignmentStateAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("advisor", "company", "last_assigned_at", "updated_at")
    list_filter = ("company",)
    search_fields = ("advisor__code", "advisor__user__email")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("company", "advisor")
