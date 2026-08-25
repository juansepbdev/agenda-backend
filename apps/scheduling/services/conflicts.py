from datetime import timedelta

from ..exceptions import EventConflictError
from ..models import Event

BLOCKING = [Event.Status.PENDING, Event.Status.CONFIRMED, Event.Status.IN_PROGRESS]


def check_event_conflicts(
    *, company, advisor, start_at, end_at, exclude_event=None, buffer_before=0, buffer_after=0, raise_error=True
):
    start = start_at - timedelta(minutes=buffer_before)
    end = end_at + timedelta(minutes=buffer_after)
    qs = Event.objects.filter(company=company, advisor=advisor, status__in=BLOCKING, start_at__lt=end, end_at__gt=start)
    if exclude_event:
        qs = qs.exclude(pk=exclude_event.pk)
    conflict = qs.first()
    if conflict and raise_error:
        raise EventConflictError(
            "El asesor ya tiene un evento en el horario solicitado.",
            {"advisor_id": str(advisor.id), "start_at": start_at.isoformat(), "end_at": end_at.isoformat()},
        )
    return conflict
