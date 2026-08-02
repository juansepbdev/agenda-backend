from rest_framework import serializers

from .models import Company


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ("id", "name", "legal_name", "slug", "nit", "email", "phone", "address", "city", "country", "timezone", "default_language", "status", "subscription_plan", "max_users", "max_advisors", "settings", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "slug", "nit", "status", "subscription_plan", "max_users", "max_advisors", "is_active", "created_at", "updated_at")
