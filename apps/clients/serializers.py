from rest_framework import serializers

from .models import Client
from .services import normalize_phone


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        exclude = ("company",)
        read_only_fields = ("id", "normalized_phone", "created_at", "updated_at")
    def validate_phone(self, value):
        normalize_phone(value); return value
    def create(self, data):
        request = self.context["request"]; data["company"] = request.user.company; data["normalized_phone"] = normalize_phone(data["phone"]); return super().create(data)
