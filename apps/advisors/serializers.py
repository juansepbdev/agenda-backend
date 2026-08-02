from rest_framework import serializers

from .models import Advisor, AdvisorAvailability, AdvisorSupervision


class AdvisorSerializer(serializers.ModelSerializer):
    class Meta: model=Advisor; exclude=("company",)
    def validate_user(self, value):
        if value.company_id != self.context["request"].user.company_id: raise serializers.ValidationError("El usuario debe ser del tenant.")
        return value
    def create(self, data): data["company"]=self.context["request"].user.company; return super().create(data)
class AdvisorAvailabilitySerializer(serializers.ModelSerializer):
    class Meta: model=AdvisorAvailability; exclude=("company","configured_by")
    def validate_advisor(self,value):
        if value.company_id != self.context["request"].user.company_id: raise serializers.ValidationError("El asesor debe ser del tenant.")
        return value
    def create(self,data): data.update(company=self.context["request"].user.company, configured_by=self.context["request"].user); return super().create(data)
class AdvisorSupervisionSerializer(serializers.ModelSerializer):
    class Meta: model=AdvisorSupervision; exclude=("company","assigned_by")
    def validate(self,data):
        company=self.context["request"].user.company
        if data["supervisor_user"].company_id != company.id or data["advisor"].company_id != company.id: raise serializers.ValidationError("Las relaciones deben pertenecer al tenant.")
        return data
    def create(self,data): data.update(company=self.context["request"].user.company,assigned_by=self.context["request"].user); return super().create(data)
