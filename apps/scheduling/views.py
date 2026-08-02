from datetime import datetime, time, timedelta

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import IsCompanyAdmin

from .models import SchedulingConfiguration
from .selectors import get_events_visible_to_user
from .serializers import (
    EventActionSerializer,
    EventCreateSerializer,
    EventDetailSerializer,
    EventHistorySerializer,
    EventListSerializer,
    SchedulingConfigurationSerializer,
)
from .services import event_actions


class EventViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        qs=get_events_visible_to_user(user=self.request.user)
        for field in ("advisor","client","status","event_type","source"):
            if self.request.query_params.get(field): qs=qs.filter(**{field:self.request.query_params[field]})
        return qs.order_by("start_at")
    def get_serializer_class(self): return EventCreateSerializer if self.action=="create" else EventDetailSerializer if self.action in ("retrieve","update","partial_update") else EventListSerializer
    def _validated_action(self, request):
        serializer=EventActionSerializer(data=request.data); serializer.is_valid(raise_exception=True); return self.get_object(), serializer.validated_data
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        event, _ = self._validated_action(request); return Response(EventDetailSerializer(event_actions.confirm_event(event=event, actor=request.user)).data)
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        event, _ = self._validated_action(request); return Response(EventDetailSerializer(event_actions.start_event(event=event, actor=request.user)).data)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        event, data = self._validated_action(request); return Response(EventDetailSerializer(event_actions.complete_event(event=event, actor=request.user, notes=data.get("notes", ""))).data)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        event, data = self._validated_action(request); return Response(EventDetailSerializer(event_actions.cancel_event(event=event, actor=request.user, reason=data.get("reason", ""), source=data.get("cancellation_source", "ADMIN"))).data)
    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, pk=None):
        event, data = self._validated_action(request); return Response(EventDetailSerializer(event_actions.mark_event_no_show(event=event, actor=request.user, no_show_type=data["no_show_type"], notes=data.get("notes", ""))).data)
    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        event, data = self._validated_action(request); return Response(EventDetailSerializer(event_actions.reassign_event(event=event, actor=request.user, advisor=data["advisor"])).data)
    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        event, data = self._validated_action(request); return Response(EventDetailSerializer(event_actions.reschedule_event(event=event, actor=request.user, start_at=data["start_at"], end_at=data["end_at"], advisor=data.get("advisor"))).data)
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None): return Response(EventHistorySerializer(self.get_object().history.filter(company=request.user.company), many=True).data)

class SchedulingConfigurationViewSet(viewsets.ModelViewSet):
    serializer_class=SchedulingConfigurationSerializer; permission_classes=[IsCompanyAdmin]
    def get_queryset(self): return SchedulingConfiguration.objects.filter(company=self.request.user.company)
    def perform_create(self, serializer): serializer.save(company=self.request.user.company, created_by=self.request.user, updated_by=self.request.user)
    def perform_update(self, serializer): serializer.save(updated_by=self.request.user)
    @action(detail=False, methods=["get"])
    def default(self, request): return Response(self.get_serializer(self.get_queryset().filter(is_default=True,is_active=True).first()).data)

def _calendar(request, start, end):
    return Response({"range":{"start":start.isoformat(),"end":end.isoformat(),"timezone":request.user.company.timezone},"events":EventListSerializer(get_events_visible_to_user(user=request.user).filter(start_at__lt=end,end_at__gt=start).order_by("start_at"),many=True).data})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_day(request):
    day=datetime.fromisoformat(request.query_params.get("date",timezone.localdate().isoformat())).date(); start=timezone.make_aware(datetime.combine(day,time.min)); return _calendar(request,start,start+timedelta(days=1))
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_week(request):
    day=datetime.fromisoformat(request.query_params.get("start_date",timezone.localdate().isoformat())).date(); start=timezone.make_aware(datetime.combine(day,time.min)); return _calendar(request,start,start+timedelta(days=7))
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_month(request):
    year=int(request.query_params["year"]); month=int(request.query_params["month"]); tz=timezone.get_current_timezone()
    start=datetime(year,month,1,tzinfo=tz); end=datetime(year+1,1,1,tzinfo=tz) if month==12 else datetime(year,month+1,1,tzinfo=tz); return _calendar(request,start,end)
