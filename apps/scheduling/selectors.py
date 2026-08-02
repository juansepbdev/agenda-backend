from apps.advisors.models import AdvisorSupervision

from .models import Event


def get_supervisor_advisor_ids(*, user):
    return AdvisorSupervision.objects.filter(company=user.company, supervisor_user=user, is_active=True).values_list("advisor_id", flat=True)

def get_events_visible_to_user(*, user):
    qs = Event.objects.filter(company=user.company).select_related("advisor__user", "client")
    if user.is_superuser or user.role == user.Role.ADMIN: return qs
    if user.role == user.Role.SUPERVISOR:
        ids = list(get_supervisor_advisor_ids(user=user)); own = getattr(user, "advisor", None)
        if own: ids.append(own.id)
        return qs.filter(advisor_id__in=ids)
    if user.role == user.Role.ADVISOR:
        return qs.filter(advisor__user=user)
    return qs.none()
