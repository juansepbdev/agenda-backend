"""Métricas de agenda: eventos, asesores y embudo de conversión.

El queryset base **siempre** llega desde fuera, ya recortado por
`scheduling.selectors.get_events_visible_to_user`. Así un asesor que abra el
dashboard ve sus números y no los de la empresa entera, sin que este módulo
tenga que conocer la matriz de permisos.

Sobre qué fecha se filtra es una decisión del que consulta, no del módulo:

* `start_at` (por defecto) responde "qué había agendado en estas fechas".
* `created_at` responde "cuánto se reservó en estas fechas".

Son dos preguntas distintas y ambas son legítimas; mezclarlas produce el clásico
informe que nadie sabe interpretar.
"""

from collections import defaultdict
from itertools import pairwise

from django.db.models import Avg, Count, F, Q

from apps.clients.models import Client
from apps.inbox.models import Contact, Conversation
from apps.scheduling.models import Event

from ..exceptions import AnalyticsValidationError
from ..periods import Period, bucket_start, iterate_buckets
from ..stats import rate

DATE_FIELDS = ("start_at", "created_at")


def resolve_date_field(raw) -> str:
    field = (raw or DATE_FIELDS[0]).strip().lower()
    if field not in DATE_FIELDS:
        raise AnalyticsValidationError(
            "`date_field` no reconocido.", details={"received": raw, "allowed": list(DATE_FIELDS)}
        )
    return field


def in_period(events, period: Period, date_field: str):
    return events.filter(**{
        f"{date_field}__gte": period.start,
        f"{date_field}__lt": period.end,
    })


# -----------------------------------------------------------------------------
# Totales y tasas
# -----------------------------------------------------------------------------

def event_totals(*, events, period: Period, date_field: str = "start_at") -> dict:
    scoped = in_period(events, period, date_field)
    row = scoped.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Event.Status.PENDING)),
        confirmed=Count("id", filter=Q(status=Event.Status.CONFIRMED)),
        in_progress=Count("id", filter=Q(status=Event.Status.IN_PROGRESS)),
        completed=Count("id", filter=Q(status=Event.Status.COMPLETED)),
        cancelled=Count("id", filter=Q(status=Event.Status.CANCELLED)),
        no_show=Count("id", filter=Q(status=Event.Status.NO_SHOW)),
        rescheduled=Count("id", filter=Q(status=Event.Status.RESCHEDULED)),
        from_chatbot=Count("id", filter=Q(source=Event.Source.CHATBOT)),
        auto_assigned=Count("id", filter=Q(assigned_automatically=True)),
    )
    closed = row["completed"] + row["cancelled"] + row["no_show"]

    return {
        "date_field": date_field,
        "total": row["total"],
        "by_status": {
            "pending": row["pending"],
            "confirmed": row["confirmed"],
            "in_progress": row["in_progress"],
            "completed": row["completed"],
            "cancelled": row["cancelled"],
            "no_show": row["no_show"],
            "rescheduled": row["rescheduled"],
        },
        "closed": closed,
        # Las tasas se calculan sobre los cerrados: un evento futuro todavía
        # pendiente no es un fracaso, y meterlo en el denominador lo aparenta.
        "completion_rate_pct": rate(row["completed"], closed),
        "cancellation_rate_pct": rate(row["cancelled"], closed),
        "no_show_rate_pct": rate(row["no_show"], closed),
        "from_chatbot": row["from_chatbot"],
        "chatbot_share_pct": rate(row["from_chatbot"], row["total"]),
        "auto_assigned": row["auto_assigned"],
        "avg_duration_minutes": _avg_duration_minutes(scoped),
    }


def _avg_duration_minutes(events) -> float | None:
    """Duración media programada. `None` si no hay eventos en el rango."""
    row = events.annotate(span=F("end_at") - F("start_at")).aggregate(value=Avg("span"))
    span = row["value"]
    if span is None:
        return None
    # SQLite devuelve microsegundos en float; Postgres, un timedelta.
    seconds = span.total_seconds() if hasattr(span, "total_seconds") else float(span) / 1_000_000
    return round(seconds / 60, 2)


def event_timeseries(*, events, period: Period, date_field: str = "start_at") -> list[dict]:
    rows = (
        in_period(events, period, date_field)
        .values(date_field, "status", "source")
        .order_by()
    )

    def blank():
        return {"total": 0, "completed": 0, "cancelled": 0, "no_show": 0, "from_chatbot": 0}

    series = defaultdict(blank)
    for row in rows.iterator(chunk_size=2000):
        key = bucket_start(row[date_field], period.granularity, period.tz)
        entry = series[key]
        entry["total"] += 1
        if row["status"] == Event.Status.COMPLETED:
            entry["completed"] += 1
        elif row["status"] == Event.Status.CANCELLED:
            entry["cancelled"] += 1
        elif row["status"] == Event.Status.NO_SHOW:
            entry["no_show"] += 1
        if row["source"] == Event.Source.CHATBOT:
            entry["from_chatbot"] += 1

    empty = blank()
    return [
        {"bucket": bucket.isoformat(), **series.get(bucket, empty)}
        for bucket in iterate_buckets(period)
    ]


def _breakdown(events, field: str, *, label: str) -> list[dict]:
    rows = events.values(field).annotate(total=Count("id")).order_by("-total")
    grand_total = sum(row["total"] for row in rows)
    return [{label: row[field], "total": row["total"], "share_pct": rate(row["total"], grand_total)} for row in rows]


def event_breakdowns(*, events, period: Period, date_field: str = "start_at") -> dict:
    scoped = in_period(events, period, date_field)
    return {
        "by_type": _breakdown(scoped, "event_type", label="event_type"),
        "by_source": _breakdown(scoped, "source", label="source"),
        "by_no_show_type": _breakdown(
            scoped.filter(status=Event.Status.NO_SHOW), "no_show_type", label="no_show_type"
        ),
    }


# -----------------------------------------------------------------------------
# Asesores
# -----------------------------------------------------------------------------

def advisor_performance(*, events, period: Period, date_field: str = "start_at") -> list[dict]:
    """Ranking de asesores por eventos del periodo.

    Se ordena por completados y no por totales: cargar la agenda no es el
    resultado, cerrarla sí.
    """
    rows = (
        in_period(events, period, date_field)
        .values("advisor_id", "advisor__code", "advisor__user__email", "advisor__user__first_name", "advisor__user__last_name")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=Event.Status.COMPLETED)),
            cancelled=Count("id", filter=Q(status=Event.Status.CANCELLED)),
            no_show=Count("id", filter=Q(status=Event.Status.NO_SHOW)),
            pending=Count("id", filter=Q(status=Event.Status.PENDING)),
            confirmed=Count("id", filter=Q(status=Event.Status.CONFIRMED)),
            from_chatbot=Count("id", filter=Q(source=Event.Source.CHATBOT)),
        )
        .order_by("-completed", "-total")
    )

    result = []
    for row in rows:
        closed = row["completed"] + row["cancelled"] + row["no_show"]
        name = f"{row['advisor__user__first_name']} {row['advisor__user__last_name']}".strip()
        result.append({
            "advisor_id": str(row["advisor_id"]),
            "code": row["advisor__code"],
            "name": name or row["advisor__user__email"],
            "email": row["advisor__user__email"],
            "total": row["total"],
            "pending": row["pending"],
            "confirmed": row["confirmed"],
            "completed": row["completed"],
            "cancelled": row["cancelled"],
            "no_show": row["no_show"],
            "from_chatbot": row["from_chatbot"],
            "completion_rate_pct": rate(row["completed"], closed),
            "no_show_rate_pct": rate(row["no_show"], closed),
        })
    return result


# -----------------------------------------------------------------------------
# Embudo
# -----------------------------------------------------------------------------

def conversion_funnel(*, company, events, period: Period) -> dict:
    """De contacto de WhatsApp a visita cerrada.

    Los cinco escalones se cuentan por su propia fecha de creación dentro del
    periodo, así que no son subconjuntos exactos unos de otros: un contacto de
    ayer puede agendar hoy. Es una foto de caudal, no una cohorte. Las tasas se
    dan entre escalones consecutivos, que es la lectura que aguanta ese matiz.
    """
    window = {"created_at__gte": period.start, "created_at__lt": period.end}

    contacts = Contact.objects.filter(company=company, **window).count()
    conversations = Conversation.objects.filter(company=company, **window).count()
    clients = Client.objects.filter(company=company, source=Client.Source.CHATBOT, **window).count()
    booked = events.filter(source=Event.Source.CHATBOT, **window)
    scheduled = booked.count()
    completed = booked.filter(status=Event.Status.COMPLETED).count()

    steps = [
        {"key": "contacts", "label": "Contactos nuevos", "value": contacts},
        {"key": "conversations", "label": "Conversaciones abiertas", "value": conversations},
        {"key": "clients", "label": "Clientes creados por el bot", "value": clients},
        {"key": "events_scheduled", "label": "Visitas agendadas por el bot", "value": scheduled},
        {"key": "events_completed", "label": "Visitas completadas", "value": completed},
    ]
    for previous, step in pairwise(steps):
        step["conversion_from_previous_pct"] = rate(step["value"], previous["value"])
    steps[0]["conversion_from_previous_pct"] = None

    return {
        "steps": steps,
        "overall_conversion_pct": rate(completed, contacts),
    }
