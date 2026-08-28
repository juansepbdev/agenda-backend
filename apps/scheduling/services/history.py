from ..models import EventHistory


def create_event_history(
    *,
    event,
    action,
    actor_user=None,
    actor_type="USER",
    previous_status="",
    new_status="",
    previous_data=None,
    new_data=None,
    notes="",
):
    return EventHistory.objects.create(
        company=event.company,
        event=event,
        action=action,
        actor_user=actor_user,
        actor_type=actor_type,
        source=event.source,
        previous_status=previous_status,
        new_status=new_status,
        previous_data=previous_data or {},
        new_data=new_data or {},
        notes=notes,
    )
