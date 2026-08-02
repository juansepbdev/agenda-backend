from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist

from .models import Company


class TenantAdminMixin:
    """Limits Django-admin staff users to their own company."""

    company_field = "company"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        company = getattr(request.user, "company", None)
        return queryset.filter(**{self.company_field: company}) if company else queryset.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == self.company_field:
                kwargs["queryset"] = Company.objects.filter(pk=getattr(request.user, "company_id", None))
            elif db_field.remote_field and db_field.remote_field.model:
                related_model = db_field.remote_field.model
                try:
                    related_model._meta.get_field("company")
                except FieldDoesNotExist:
                    pass
                else:
                    kwargs["queryset"] = related_model._default_manager.filter(company=request.user.company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "status", "subscription_plan", "is_active", "updated_at")
    list_filter = ("status", "is_active", "country")
    search_fields = ("name", "legal_name", "slug", "nit", "email")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")
    ordering = ("name",)

    def has_module_permission(self, request):
        return request.user.is_superuser
