from django.db.models import Q
from django.utils import timezone

from apps.advisors.models import Advisor, AdvisorAvailability

from ..exceptions import (
    AdvisorDailyLimitExceededError,
    AdvisorUnavailableError,
    EventConflictError,
)
from ..models import Event, SchedulingConfiguration
from .conflicts import BLOCKING, check_event_conflicts


def get_configuration(company):
    return SchedulingConfiguration.objects.filter(company=company, is_default=True, is_active=True).first()


def check_advisor_availability(*, company, advisor, start_at, end_at, configuration=None, exclude_event=None):
    configuration = configuration or get_configuration(company)
    if not advisor.is_active or not advisor.is_available:
        raise AdvisorUnavailableError("El asesor no está disponible.")
    local_date = timezone.localtime(start_at).date()
    local_start = timezone.localtime(start_at).time()
    local_end = timezone.localtime(end_at).time()
    blocks = AdvisorAvailability.objects.filter(
        company=company, advisor=advisor, day_of_week=local_date.weekday(), is_active=True
    ).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=local_date),
        Q(valid_until__isnull=True) | Q(valid_until__gte=local_date),
    )
    if (
        not (configuration and configuration.allow_events_outside_availability)
        and not blocks.filter(start_time__lte=local_start, end_time__gte=local_end).exists()
    ):
        raise AdvisorUnavailableError("El horario está fuera de la disponibilidad del asesor.")
    before = configuration.default_buffer_before_minutes if configuration else 0
    after = configuration.default_buffer_after_minutes if configuration else 0
    check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=start_at,
        end_at=end_at,
        exclude_event=exclude_event,
        buffer_before=before,
        buffer_after=after,
    )
    if (
        advisor.max_daily_events
        and Event.objects.filter(
            company=company, advisor=advisor, status__in=BLOCKING, start_at__date=local_date
        ).count()
        >= advisor.max_daily_events
    ):
        raise AdvisorDailyLimitExceededError("El asesor alcanzó su máximo diario.")
    return True


def find_available_advisors(*, company, start_at, end_at, configuration=None):
    candidates = (
        Advisor.objects.filter(company=company, is_active=True, is_available=True, accepts_automatic_assignments=True)
        .select_for_update()
        .select_related("user")
    )
    valid = []
    for advisor in candidates:
        try:
            check_advisor_availability(
                company=company, advisor=advisor, start_at=start_at, end_at=end_at, configuration=configuration
            )
        # EventConflictError es el motivo MÁS común por el que un asesor no
        # sirve para un horario. Sin capturarlo, un solo asesor ocupado hacía
        # fallar toda la asignación automática del chatbot en vez de pasar al
        # siguiente candidato.
        except (AdvisorUnavailableError, AdvisorDailyLimitExceededError, EventConflictError):
            continue
        valid.append(advisor)
    return valid
