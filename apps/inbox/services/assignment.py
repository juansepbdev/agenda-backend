"""Reparto de conversaciones entre asesores.

No se reusa `apps.scheduling.services.assignment.assign_advisor_automatically`
a propósito: aquella exige `start_at`/`end_at`, comprueba franjas de
disponibilidad y **lanza** si nadie está libre en ese hueco. Un WhatsApp entra a
cualquier hora, así que engancharla reventaría la ingesta del webhook de
madrugada. Aquí solo hace falta el criterio de carga, sin horario.
"""

from django.db.models import Count, Q

from apps.advisors.models import Advisor

from ..models import Conversation


def pick_advisor(*, company):
    """Asesor elegible con menos conversaciones abiertas. `None` si no hay ninguno.

    Los candidatos son los mismos que acepta la asignación automática de la
    agenda (`scheduling.services.availability.find_available_advisors`), menos
    la comprobación de horario. Empate resuelto por `str(id)` para que el
    reparto sea determinista y comprobable en tests.
    """
    candidates = list(
        Advisor.objects.filter(
            company=company,
            is_active=True,
            is_available=True,
            accepts_automatic_assignments=True,
        ).annotate(open_conversations=Count("conversations", filter=Q(conversations__status=Conversation.Status.OPEN)))
    )
    if not candidates:
        return None
    return min(candidates, key=lambda advisor: (advisor.open_conversations, str(advisor.id)))
