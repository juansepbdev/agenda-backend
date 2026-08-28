from django.db import transaction
from django.utils import timezone

from ..exceptions import CompanyInactiveError, InvalidEventTransitionError
from ..models import Event
from .availability import check_advisor_availability, get_configuration
from .history import create_event_history


def _transition(event, actor, allowed, status, action, **fields):
    if event.status not in allowed:
        raise InvalidEventTransitionError(
            "La transición de estado no está permitida.", {"current_status": event.status}
        )
    previous = event.status
    event.status = status
    for key, value in fields.items():
        setattr(event, key, value)
    event.updated_by = actor
    event.save()
    create_event_history(event=event, action=action, actor_user=actor, previous_status=previous, new_status=status)
    return event


@transaction.atomic
def create_manual_event(*, company, advisor, actor, **data):
    if not company.can_operate:
        raise CompanyInactiveError("La empresa no está activa.")
    if advisor.company_id != company.id or (data.get("client") and data["client"].company_id != company.id):
        raise InvalidEventTransitionError("Relación fuera de la empresa.")
    check_advisor_availability(
        company=company,
        advisor=advisor,
        start_at=data["start_at"],
        end_at=data["end_at"],
        configuration=get_configuration(company),
    )
    event = Event.objects.create(
        company=company, advisor=advisor, source=Event.Source.MANUAL, created_by=actor, updated_by=actor, **data
    )
    create_event_history(event=event, action="CREATED", actor_user=actor, new_status=event.status)
    return event


def confirm_event(*, event, actor):
    return _transition(
        event,
        actor,
        [Event.Status.PENDING],
        Event.Status.CONFIRMED,
        "CONFIRMED",
        confirmed_at=timezone.now(),
        confirmed_by=actor,
    )


def start_event(*, event, actor):
    return _transition(
        event,
        actor,
        [Event.Status.CONFIRMED],
        Event.Status.IN_PROGRESS,
        "STARTED",
        started_at=timezone.now(),
        started_by=actor,
    )


def complete_event(*, event, actor, notes=""):
    return _transition(
        event,
        actor,
        [Event.Status.CONFIRMED, Event.Status.IN_PROGRESS],
        Event.Status.COMPLETED,
        "COMPLETED",
        completed_at=timezone.now(),
        completed_by=actor,
        completion_notes=notes,
    )


def cancel_event(*, event, actor, reason="", source="ADMIN"):
    return _transition(
        event,
        actor,
        [Event.Status.PENDING, Event.Status.CONFIRMED, Event.Status.IN_PROGRESS, Event.Status.NO_SHOW],
        Event.Status.CANCELLED,
        "CANCELLED",
        cancelled_at=timezone.now(),
        cancelled_by=actor,
        cancellation_reason=reason,
        cancellation_source=source,
    )


def mark_event_no_show(*, event, actor, no_show_type, notes=""):
    return _transition(
        event,
        actor,
        [Event.Status.PENDING, Event.Status.CONFIRMED, Event.Status.IN_PROGRESS],
        Event.Status.NO_SHOW,
        "NO_SHOW",
        no_show_type=no_show_type,
        no_show_notes=notes,
    )


@transaction.atomic
def reassign_event(*, event, advisor, actor):
    if advisor.company_id != event.company_id:
        raise InvalidEventTransitionError("No se puede reasignar a otra empresa.")
    check_advisor_availability(
        company=event.company,
        advisor=advisor,
        start_at=event.start_at,
        end_at=event.end_at,
        configuration=get_configuration(event.company),
        exclude_event=event,
    )
    old = str(event.advisor_id)
    event.advisor = advisor
    event.updated_by = actor
    event.save()
    create_event_history(
        event=event,
        action="REASSIGNED",
        actor_user=actor,
        previous_data={"advisor_id": old},
        new_data={"advisor_id": str(advisor.id)},
    )
    return event


@transaction.atomic
def reschedule_event(*, event, actor, start_at, end_at, advisor=None):
    if event.status in [Event.Status.COMPLETED, Event.Status.RESCHEDULED]:
        raise InvalidEventTransitionError("Este evento no se puede reprogramar.")
    advisor = advisor or event.advisor
    check_advisor_availability(
        company=event.company,
        advisor=advisor,
        start_at=start_at,
        end_at=end_at,
        configuration=get_configuration(event.company),
    )
    original = event.status
    event.status = Event.Status.RESCHEDULED
    event.updated_by = actor
    event.save(update_fields=["status", "updated_by", "updated_at"])
    new = Event.objects.create(
        company=event.company,
        advisor=advisor,
        client=event.client,
        event_type=event.event_type,
        status=Event.Status.PENDING,
        source=event.source,
        title=event.title,
        description=event.description,
        start_at=start_at,
        end_at=end_at,
        timezone=event.timezone,
        rescheduled_from=event,
        created_by=actor,
        updated_by=actor,
    )
    event.rescheduled_to = new
    event.save(update_fields=["rescheduled_to", "updated_at"])
    create_event_history(
        event=event,
        action="RESCHEDULED",
        actor_user=actor,
        previous_status=original,
        new_status=event.status,
        new_data={"new_event_id": str(new.id)},
    )
    create_event_history(event=new, action="CREATED", actor_user=actor, new_status=new.status)
    return new
