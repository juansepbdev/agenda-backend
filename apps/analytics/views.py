"""Endpoints de lectura del dashboard.

Todos comparten la misma forma de respuesta:

```json
{"period": {...}, "<bloque>": {...}}
```

`period` viaja siempre porque el cliente necesita saber qué rango y qué
granularidad acabó resolviendo el backend cuando no los especificó.

Las vistas no calculan nada: parsean la petición, resuelven el alcance del
usuario y delegan en `services/`.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.scheduling.exceptions import PermissionScopeError
from apps.scheduling.selectors import get_events_visible_to_user

from .periods import parse_period
from .permissions import CanViewInboxMetrics, can_view_inbox_metrics
from .services import agenda_metrics, inbox_metrics
from .stats import delta

# Parámetros comunes, documentados una vez y reutilizados en el esquema.
PERIOD_PARAMS = [
    OpenApiParameter("period", str, description="today | yesterday | 7d | 14d | 30d | 90d | 180d | 365d | mtd | ytd. Por defecto 30d."),
    OpenApiParameter("from", str, description="Inicio del rango: `YYYY-MM-DD` o ISO-8601. Tiene prioridad sobre `period`."),
    OpenApiParameter("to", str, description="Fin del rango, inclusivo si es una fecha suelta."),
    OpenApiParameter("granularity", str, description="hour | day | week | month. Si se omite, se deduce del rango."),
    OpenApiParameter("tz", str, description="Zona horaria IANA. Por defecto, la de la empresa."),
]

DATE_FIELD_PARAM = OpenApiParameter(
    "date_field", str, description="start_at (agendado en el rango, por defecto) | created_at (reservado en el rango)."
)


def _company(request):
    company = getattr(request.user, "company", None)
    if company is None:
        raise PermissionScopeError("El usuario no pertenece a ninguna empresa.")
    return company


def _events(request):
    """Eventos visibles para quien consulta, ya recortados por rol."""
    return get_events_visible_to_user(user=request.user)


def _period(request, company):
    return parse_period(request.query_params, company=company)


def _limit(request, default: int, ceiling: int = 100) -> int:
    raw = request.query_params.get("limit")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, ceiling))


# -----------------------------------------------------------------------------
# Vista general
# -----------------------------------------------------------------------------

@extend_schema(
    summary="Tarjetas KPI del dashboard, con variación contra el periodo anterior.",
    description=(
        "Devuelve el bloque `agenda` siempre (recortado al alcance del usuario) "
        "y el bloque `inbox` solo para administración y supervisión."
    ),
    parameters=PERIOD_PARAMS + [DATE_FIELD_PARAM],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def overview(request):
    company = _company(request)
    period = _period(request, company)
    previous = period.previous()
    date_field = agenda_metrics.resolve_date_field(request.query_params.get("date_field"))
    events = _events(request)

    agenda_now = agenda_metrics.event_totals(events=events, period=period, date_field=date_field)
    agenda_before = agenda_metrics.event_totals(events=events, period=previous, date_field=date_field)

    payload = {
        "period": period.as_dict(),
        "comparison_period": previous.as_dict(),
        "agenda": {
            **agenda_now,
            "trend": {
                "total": delta(agenda_now["total"], agenda_before["total"]),
                "completed": delta(agenda_now["by_status"]["completed"], agenda_before["by_status"]["completed"]),
                "cancelled": delta(agenda_now["by_status"]["cancelled"], agenda_before["by_status"]["cancelled"]),
                "no_show": delta(agenda_now["by_status"]["no_show"], agenda_before["by_status"]["no_show"]),
                "from_chatbot": delta(agenda_now["from_chatbot"], agenda_before["from_chatbot"]),
                "completion_rate_pct": delta(agenda_now["completion_rate_pct"], agenda_before["completion_rate_pct"]),
            },
        },
        "inbox": None,
    }

    if can_view_inbox_metrics(request.user):
        messages_now = inbox_metrics.message_totals(company=company, period=period)
        messages_before = inbox_metrics.message_totals(company=company, period=previous)
        conversations = inbox_metrics.conversation_totals(company=company, period=period)
        conversations_before = inbox_metrics.conversation_totals(company=company, period=previous)
        contacts = inbox_metrics.contact_totals(company=company, period=period)
        contacts_before = inbox_metrics.contact_totals(company=company, period=previous)

        payload["inbox"] = {
            "messages": messages_now,
            "conversations": conversations,
            "contacts": contacts,
            "automation": inbox_metrics.automation_summary(company=company, period=period),
            "response_times": inbox_metrics.response_times(company=company, period=period),
            "trend": {
                "messages_total": delta(messages_now["total"], messages_before["total"]),
                "messages_inbound": delta(messages_now["inbound"], messages_before["inbound"]),
                "messages_outbound": delta(messages_now["outbound"], messages_before["outbound"]),
                "conversations_new": delta(conversations["new"], conversations_before["new"]),
                "contacts_new": delta(contacts["new"], contacts_before["new"]),
                "bot_share_pct": delta(messages_now["bot_share_pct"], messages_before["bot_share_pct"]),
            },
        }

    return Response(payload)


# -----------------------------------------------------------------------------
# Inbox
# -----------------------------------------------------------------------------

@extend_schema(
    summary="Volumen de mensajes: totales, serie temporal y reparto por estado.",
    parameters=PERIOD_PARAMS,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
@permission_classes([CanViewInboxMetrics])
def messages_metrics(request):
    company = _company(request)
    period = _period(request, company)
    return Response({
        "period": period.as_dict(),
        "totals": inbox_metrics.message_totals(company=company, period=period),
        "series": inbox_metrics.message_timeseries(company=company, period=period),
        "by_status": inbox_metrics.status_breakdown(company=company, period=period),
    })


@extend_schema(
    summary="Mapa de calor de mensajes por día de la semana y hora local.",
    parameters=PERIOD_PARAMS,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
@permission_classes([CanViewInboxMetrics])
def messages_heatmap(request):
    company = _company(request)
    period = _period(request, company)
    return Response({
        "period": period.as_dict(),
        "heatmap": inbox_metrics.message_heatmap(company=company, period=period),
    })


@extend_schema(
    summary="Conversaciones, contactos, tiempos de respuesta y automatización.",
    parameters=PERIOD_PARAMS + [
        OpenApiParameter("limit", int, description="Tamaño del ranking de contactos (1..100, por defecto 10)."),
    ],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
@permission_classes([CanViewInboxMetrics])
def conversations_metrics(request):
    company = _company(request)
    period = _period(request, company)
    return Response({
        "period": period.as_dict(),
        "conversations": inbox_metrics.conversation_totals(company=company, period=period),
        "contacts": inbox_metrics.contact_totals(company=company, period=period),
        "response_times": inbox_metrics.response_times(company=company, period=period),
        "automation": inbox_metrics.automation_summary(company=company, period=period),
        "top_contacts": inbox_metrics.top_contacts(
            company=company, period=period, limit=_limit(request, inbox_metrics.TOP_CONTACTS_LIMIT)
        ),
    })


# -----------------------------------------------------------------------------
# Agenda
# -----------------------------------------------------------------------------

@extend_schema(
    summary="Eventos: totales, tasas, serie temporal y desgloses.",
    parameters=PERIOD_PARAMS + [DATE_FIELD_PARAM],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def events_metrics(request):
    company = _company(request)
    period = _period(request, company)
    date_field = agenda_metrics.resolve_date_field(request.query_params.get("date_field"))
    events = _events(request)

    return Response({
        "period": period.as_dict(),
        "totals": agenda_metrics.event_totals(events=events, period=period, date_field=date_field),
        "series": agenda_metrics.event_timeseries(events=events, period=period, date_field=date_field),
        "breakdowns": agenda_metrics.event_breakdowns(events=events, period=period, date_field=date_field),
    })


@extend_schema(
    summary="Rendimiento por asesor dentro del alcance del usuario.",
    parameters=PERIOD_PARAMS + [DATE_FIELD_PARAM],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def advisors_metrics(request):
    company = _company(request)
    period = _period(request, company)
    date_field = agenda_metrics.resolve_date_field(request.query_params.get("date_field"))

    return Response({
        "period": period.as_dict(),
        "advisors": agenda_metrics.advisor_performance(
            events=_events(request), period=period, date_field=date_field
        ),
    })


@extend_schema(
    summary="Embudo de conversión de WhatsApp a visita completada.",
    parameters=PERIOD_PARAMS,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
@permission_classes([CanViewInboxMetrics])
def funnel(request):
    company = _company(request)
    period = _period(request, company)
    return Response({
        "period": period.as_dict(),
        "funnel": agenda_metrics.conversion_funnel(
            company=company, events=_events(request), period=period
        ),
    })
