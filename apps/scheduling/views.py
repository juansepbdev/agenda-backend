from datetime import datetime, time, timedelta

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import IsCompanyAdmin

from .models import SchedulingConfiguration
from .selectors import get_events_visible_to_user, get_user_company
from .serializers import (
    CalendarDayQuerySerializer,
    CalendarMonthQuerySerializer,
    CalendarWeekQuerySerializer,
    EventActionSerializer,
    EventCreateSerializer,
    EventDetailSerializer,
    EventHistorySerializer,
    EventListSerializer,
    SchedulingConfigurationSerializer,
)
from .services import event_actions

# Filtros de `GET /events/`: nombre del query param -> lookup del ORM.
EVENT_FILTERS = {
    "advisor": "advisor",
    "client": "client",
    "status": "status",
    "event_type": "event_type",
    "source": "source",
    "start_at__gte": "start_at__gte",
    "start_at__lte": "start_at__lte",
}


class EventViewSet(viewsets.ModelViewSet):
    ordering_fields = ["start_at", "end_at", "status", "created_at"]

    def get_queryset(self):
        qs = get_events_visible_to_user(user=self.request.user)
        filters = {
            lookup: self.request.query_params[param]
            for param, lookup in EVENT_FILTERS.items()
            if self.request.query_params.get(param)
        }
        # Un UUID o una fecha mal formados salen como ValidationError de Django
        # al evaluar la consulta; `api_exception_handler` los convierte en 400.
        return qs.filter(**filters).order_by("start_at")

    def get_serializer_class(self):
        if self.action == "create":
            return EventCreateSerializer
        if self.action in ("retrieve", "update", "partial_update"):
            return EventDetailSerializer
        return EventListSerializer

    def _validated_action(self, request):
        serializer = EventActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.get_object(), serializer.validated_data

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        event, _ = self._validated_action(request)
        event = event_actions.confirm_event(event=event, actor=request.user)
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        event, _ = self._validated_action(request)
        event = event_actions.start_event(event=event, actor=request.user)
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        event, data = self._validated_action(request)
        # El panel envía `completion_notes`; el contrato original documentaba
        # `notes`. Se aceptan las dos para no romper a ningún cliente.
        notes = data.get("completion_notes") or data.get("notes", "")
        event = event_actions.complete_event(event=event, actor=request.user, notes=notes)
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        event, data = self._validated_action(request)
        event = event_actions.cancel_event(
            event=event,
            actor=request.user,
            reason=data.get("reason", ""),
            source=data.get("cancellation_source", "ADMIN"),
        )
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, pk=None):
        event, data = self._validated_action(request)
        event = event_actions.mark_event_no_show(
            event=event,
            actor=request.user,
            no_show_type=data["no_show_type"],
            notes=data.get("notes", ""),
        )
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        event, data = self._validated_action(request)
        event = event_actions.reassign_event(event=event, actor=request.user, advisor=data["advisor"])
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        event, data = self._validated_action(request)
        event = event_actions.reschedule_event(
            event=event,
            actor=request.user,
            start_at=data["start_at"],
            end_at=data["end_at"],
            advisor=data.get("advisor"),
        )
        return Response(EventDetailSerializer(event).data)

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        history = self.get_object().history.filter(company=get_user_company(request.user))
        return Response(EventHistorySerializer(history, many=True).data)


class SchedulingConfigurationViewSet(viewsets.ModelViewSet):
    serializer_class = SchedulingConfigurationSerializer
    permission_classes = [IsCompanyAdmin]

    def get_queryset(self):
        return SchedulingConfiguration.objects.filter(company=get_user_company(self.request.user))

    def perform_create(self, serializer):
        serializer.save(
            company=get_user_company(self.request.user),
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=False, methods=["get"])
    def default(self, request):
        configuration = self.get_queryset().filter(is_default=True, is_active=True).first()
        return Response(self.get_serializer(configuration).data)


# -----------------------------------------------------------------------------
# Calendario
# -----------------------------------------------------------------------------


def _calendar(request, start, end):
    company = get_user_company(request.user)
    events = (
        get_events_visible_to_user(user=request.user).filter(start_at__lt=end, end_at__gt=start).order_by("start_at")
    )
    return Response(
        {
            "range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timezone": company.timezone,
            },
            "events": EventListSerializer(events, many=True).data,
        }
    )


def _validated_query(serializer_class, request):
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_day(request):
    data = _validated_query(CalendarDayQuerySerializer, request)
    start = _midnight(data.get("date") or timezone.localdate())
    return _calendar(request, start, start + timedelta(days=1))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_week(request):
    data = _validated_query(CalendarWeekQuerySerializer, request)
    start = _midnight(data.get("start_date") or timezone.localdate())
    return _calendar(request, start, start + timedelta(days=7))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_month(request):
    data = _validated_query(CalendarMonthQuerySerializer, request)
    year, month = data["year"], data["month"]
    tz = timezone.get_current_timezone()
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    return _calendar(request, start, end)
