import random
from datetime import UTC, datetime

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..exceptions import AdvisorUnavailableError
from ..models import AdvisorAssignmentState, Event
from .availability import find_available_advisors


@transaction.atomic
def assign_advisor_automatically(*, company, start_at, end_at, configuration):
    candidates = find_available_advisors(company=company, start_at=start_at, end_at=end_at, configuration=configuration)
    if not candidates:
        raise AdvisorUnavailableError("No hay asesores disponibles para ese horario.")
    strategy = configuration.assignment_strategy
    if strategy == configuration.Strategy.RANDOM:
        return random.choice(candidates)
    if strategy == configuration.Strategy.PRIORITY:
        return min(candidates, key=lambda item: (item.assignment_priority, str(item.id)))
    if strategy == configuration.Strategy.LEAST_EVENTS:
        counts = {
            row["advisor"]: row["total"]
            for row in Event.objects.filter(company=company, advisor__in=candidates, start_at__date=start_at.date())
            .values("advisor")
            .annotate(total=Count("id"))
        }
        return min(candidates, key=lambda item: (counts.get(item.id, 0), str(item.id)))
    if strategy == configuration.Strategy.ROUND_ROBIN:
        states = {
            state.advisor_id: state
            for state in AdvisorAssignmentState.objects.select_for_update().filter(
                company=company, advisor__in=candidates
            )
        }
        advisor = min(
            candidates,
            key=lambda item: (
                states.get(item.id).last_assigned_at
                if item.id in states and states[item.id].last_assigned_at
                else datetime.min.replace(tzinfo=UTC),
                str(item.id),
            ),
        )
        AdvisorAssignmentState.objects.update_or_create(
            company=company, advisor=advisor, defaults={"last_assigned_at": timezone.now()}
        )
        return advisor
    return min(candidates, key=lambda item: str(item.id))
