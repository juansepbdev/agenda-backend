from rest_framework import serializers

from .models import Advisor, AdvisorAvailability, AdvisorSupervision


class AdvisorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Advisor
        exclude = ("company",)

    def validate_user(self, value):
        if value.company_id != self.context["request"].user.company_id:
            raise serializers.ValidationError("El usuario debe ser del tenant.")
        return value

    def create(self, data):
        data["company"] = self.context["request"].user.company
        return super().create(data)


class AdvisorAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvisorAvailability
        exclude = ("company", "configured_by")

    def validate_advisor(self, value):
        if value.company_id != self.context["request"].user.company_id:
            raise serializers.ValidationError("El asesor debe ser del tenant.")
        return value

    def create(self, data):
        data.update(company=self.context["request"].user.company, configured_by=self.context["request"].user)
        return super().create(data)


class AdvisorSupervisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvisorSupervision
        exclude = ("company", "assigned_by")

    def validate(self, data):
        # En un PATCH parcial las claves pueden no venir: indexar directamente
        # provocaba un KeyError (500). Se cae al valor ya guardado.
        company = self.context["request"].user.company
        supervisor = data.get("supervisor_user") or getattr(self.instance, "supervisor_user", None)
        advisor = data.get("advisor") or getattr(self.instance, "advisor", None)
        for related in (supervisor, advisor):
            if related is None or related.company_id != company.id:
                raise serializers.ValidationError("Las relaciones deben pertenecer al tenant.")
        return data

    def create(self, data):
        data.update(company=self.context["request"].user.company, assigned_by=self.context["request"].user)
        return super().create(data)
