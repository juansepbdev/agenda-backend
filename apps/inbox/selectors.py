"""Qué conversaciones puede ver cada usuario.

Mismo criterio que la agenda (`apps.scheduling.selectors.get_events_visible_to_user`),
para que un asesor no vea en el chat lo que no ve en su agenda. Fuera de alcance
se traduce en 404, no en 403: el queryset recortado es la única fuente, así que
mirar una conversación ajena no revela ni que existe.
"""

from django.db.models import Q

from apps.advisors.models import Advisor
from apps.scheduling.selectors import get_supervisor_advisor_ids, get_user_company

from .models import Conversation


def _team_advisor_ids(user):
    """Asesores supervisados por `user`, más el suyo propio si también lo es."""
    ids = list(get_supervisor_advisor_ids(user=user))
    own = getattr(user, "advisor", None)
    if own:
        ids.append(own.id)
    return ids


def conversations_visible_to_user(*, user):
    qs = Conversation.objects.select_related("contact", "advisor__user").filter(company=get_user_company(user))

    if user.is_superuser or user.role == user.Role.ADMIN:
        return qs
    if user.role == user.Role.SUPERVISOR:
        # Las sin asignar entran aquí: alguien tiene que poder repartirlas.
        return qs.filter(Q(advisor_id__in=_team_advisor_ids(user)) | Q(advisor__isnull=True))
    if user.role == user.Role.ADVISOR:
        return qs.filter(advisor__user=user)
    return qs.none()


def advisors_assignable_by(user):
    """Asesores a los que este usuario puede asignarle una conversación."""
    qs = Advisor.objects.select_related("user").filter(company=get_user_company(user), is_active=True)

    if user.is_superuser or user.role == user.Role.ADMIN:
        return qs
    if user.role == user.Role.SUPERVISOR:
        return qs.filter(id__in=_team_advisor_ids(user))
    if user.role == user.Role.ADVISOR:
        # Un asesor solo puede tomar la conversación para sí mismo.
        return qs.filter(user=user)
    return qs.none()
