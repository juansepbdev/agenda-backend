from datetime import timedelta

from django.db import transaction
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.clients.services import create_or_get_client
from apps.scheduling.exceptions import CompanyInactiveError
from apps.scheduling.models import Event
from apps.scheduling.services.assignment import assign_advisor_automatically
from apps.scheduling.services.availability import find_available_advisors, get_configuration
from apps.scheduling.services.event_actions import cancel_event
from apps.scheduling.services.history import create_event_history


class ChatbotRequestSerializer(serializers.Serializer):
    idempotency_key=serializers.CharField(max_length=255); client=serializers.DictField(); event=serializers.DictField(); chatbot_conversation_id=serializers.CharField(required=False,allow_blank=True); chatbot_message_id=serializers.CharField(required=False,allow_blank=True)
    def validate(self,data):
        event=data["event"]; start=serializers.DateTimeField().to_internal_value(event["start_at"]); duration=int(event.get("duration_minutes",60))
        if duration <= 0: raise serializers.ValidationError("duration_minutes debe ser positivo.")
        data["start_at"],data["end_at"]=start,start+timedelta(minutes=duration); return data


class AvailabilityRequestSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(min_value=1, max_value=480, default=60)


class ChatbotCancelSerializer(serializers.Serializer):
    cancellation_reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)


def _event_response(event):
    return {
        "id": str(event.id),
        "status": event.status,
        "assigned_automatically": event.assigned_automatically,
        "advisor": {"id": str(event.advisor_id), "name": event.advisor.user.get_full_name()},
        "client": {"id": str(event.client_id), "name": str(event.client)},
        "start_at": event.start_at,
        "end_at": event.end_at,
        "property_external_id": event.property_external_id,
    }

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def chatbot_event(request):
    """El tenant se resuelve por la credencial autenticada; jamás por el cuerpo."""
    company=request.user.company
    if not company.can_operate: raise CompanyInactiveError("La empresa no está activa.")
    serializer=ChatbotRequestSerializer(data=request.data); serializer.is_valid(raise_exception=True); data=serializer.validated_data
    existing=Event.objects.select_for_update().filter(company=company,idempotency_key=data["idempotency_key"]).select_related("advisor__user","client").first()
    if existing:
        return Response(_event_response(existing))
    client_data=data["client"]; client,_=create_or_get_client(company=company,phone=client_data["phone"],defaults={"first_name":client_data["first_name"],"last_name":client_data.get("last_name",""),"email":client_data.get("email",""),"source":"CHATBOT"})
    configuration=get_configuration(company)
    advisor=assign_advisor_automatically(company=company,start_at=data["start_at"],end_at=data["end_at"],configuration=configuration)
    event_data=data["event"]; event=Event.objects.create(company=company,advisor=advisor,client=client,event_type="PROPERTY_VISIT",source="CHATBOT",title=event_data["title"],description=event_data.get("description",""),start_at=data["start_at"],end_at=data["end_at"],property_external_id=event_data.get("property_external_id",""),property_code=event_data.get("property_code",""),property_title=event_data.get("property_title",""),property_address=event_data.get("property_address",""),property_url=event_data.get("property_url",""),chatbot_conversation_id=data.get("chatbot_conversation_id",""),chatbot_message_id=data.get("chatbot_message_id",""),idempotency_key=data["idempotency_key"],assigned_automatically=True,requires_confirmation=configuration.require_advisor_confirmation if configuration else False)
    create_event_history(event=event,action="CREATED",actor_type="CHATBOT",new_status=event.status)
    return Response(_event_response(event), status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def chatbot_availability(request):
    """Returns candidates already validated against availability, buffers and conflicts."""
    company = request.user.company
    if not company.can_operate:
        raise CompanyInactiveError("La empresa no está activa.")
    serializer = AvailabilityRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    start_at = serializer.validated_data["start_at"]
    end_at = start_at + timedelta(minutes=serializer.validated_data["duration_minutes"])
    configuration = get_configuration(company)
    candidates = find_available_advisors(company=company, start_at=start_at, end_at=end_at, configuration=configuration)
    candidates.sort(key=lambda advisor: (advisor.assignment_priority, str(advisor.id)))
    return Response({
        "start_at": start_at,
        "end_at": end_at,
        "assignment_strategy": configuration.assignment_strategy if configuration else "FIRST_AVAILABLE",
        "available": bool(candidates),
        "advisors": [{"id": str(advisor.id), "name": advisor.user.get_full_name(), "priority": advisor.assignment_priority} for advisor in candidates],
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def chatbot_cancel_event(request, event_id):
    """Cancels only an event that belongs to the authenticated integration tenant."""
    company = request.user.company
    event = Event.objects.select_for_update().select_related("advisor__user", "client").filter(company=company, pk=event_id).first()
    if event is None:
        return Response({"detail": "Not found."}, status=404)
    serializer = ChatbotCancelSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    event = cancel_event(event=event, actor=request.user, reason=serializer.validated_data.get("cancellation_reason", ""), source="CHATBOT")
    return Response(_event_response(event))
