from rest_framework import serializers

from apps.advisors.models import Advisor

from .models import Event, EventHistory, SchedulingConfiguration
from .services.event_actions import create_manual_event


class EventListSerializer(serializers.ModelSerializer):
    advisor_name=serializers.CharField(source="advisor.user.get_full_name", read_only=True); client_name=serializers.CharField(source="client.__str__", read_only=True)
    class Meta: model=Event; fields=("id","advisor","advisor_name","client","client_name","event_type","status","source","title","start_at","end_at","timezone","assigned_automatically","requires_confirmation")
class EventDetailSerializer(serializers.ModelSerializer):
    class Meta: model=Event; exclude=("company","created_by","updated_by","confirmed_by","started_by","completed_by","cancelled_by")
class EventCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model=Event; fields=("advisor","client","event_type","title","description","start_at","end_at","timezone","location","meeting_url","property_external_id","property_code","property_title","property_address","property_url","requires_confirmation")
    def validate(self, attrs):
        company=self.context["request"].user.company
        if attrs["advisor"].company_id != company.id or (attrs.get("client") and attrs["client"].company_id != company.id): raise serializers.ValidationError("Las relaciones deben pertenecer a la empresa.")
        if attrs["end_at"] <= attrs["start_at"]: raise serializers.ValidationError({"end_at":"Debe ser posterior a start_at."})
        return attrs
    def create(self, data): return create_manual_event(company=self.context["request"].user.company, actor=self.context["request"].user, advisor=data.pop("advisor"), **data)
class EventActionSerializer(serializers.Serializer):
    notes=serializers.CharField(required=False, allow_blank=True); reason=serializers.CharField(required=False, allow_blank=True); cancellation_source=serializers.CharField(required=False, default="ADMIN"); no_show_type=serializers.ChoiceField(choices=Event.NoShow.choices, required=False); start_at=serializers.DateTimeField(required=False); end_at=serializers.DateTimeField(required=False); advisor=serializers.PrimaryKeyRelatedField(queryset=Advisor.objects.all(), required=False)
class EventHistorySerializer(serializers.ModelSerializer):
    class Meta: model=EventHistory; fields="__all__"; read_only_fields=fields
class SchedulingConfigurationSerializer(serializers.ModelSerializer):
    class Meta: model=SchedulingConfiguration; exclude=("company","created_by","updated_by")
