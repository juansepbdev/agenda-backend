from rest_framework import serializers

from .models import Client
from .services import normalize_phone


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        exclude = ("company",)
        read_only_fields = ("id", "normalized_phone", "created_at", "updated_at")

    def validate_phone(self, value):
        # normalize_phone lanza ValueError, que DRF no reconoce y convertiría en
        # un 500 ante un teléfono demasiado corto.
        try:
            normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def create(self, data):
        request = self.context["request"]
        data["company"] = request.user.company
        data["normalized_phone"] = normalize_phone(data["phone"])
        return super().create(data)

    def update(self, instance, data):
        # Si cambia el teléfono hay que recalcular la clave normalizada: si no,
        # el cliente queda indexado por el número viejo y la deduplicación del
        # chatbot (create_or_get_client) deja de encontrarlo.
        if "phone" in data:
            data["normalized_phone"] = normalize_phone(data["phone"])
        return super().update(instance, data)
