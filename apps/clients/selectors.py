"""Cola de seguimiento de leads, derivada al consultarla.

No hay nada precalculado ni ningún proceso que mantener sincronizado: los
candidatos salen de lo que ya existe (eventos, clientes, contactos del inbox) y
`FollowUp` solo guarda las decisiones que alguien tomó, que son las que sacan a
un lead de la cola.

Un candidato puede ser un `Client` o, si escribió al chatbot y nunca agendó, un
`Contact` del inbox que todavía no tiene cliente. El cliente se materializa al
**actuar** sobre él, nunca al consultarlo: una lectura no escribe.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from apps.scheduling.models import Event, SchedulingConfiguration
from apps.scheduling.selectors import get_events_visible_to_user, get_user_company

from .models import Client, FollowUp

# Cuánto historial se mira hacia atrás. Sin este tope, la consulta crecería con
# la vida de la empresa para responder siempre lo mismo.
# ponytail: la unión de los tres orígenes se resuelve en memoria; pasar a SQL
# paginado si una empresa llega a decenas de miles de clientes.
LOOKBACK_DAYS = 365

# Qué desenlace de cita abre un seguimiento, y con qué campo de configuración
# se decide cuánto hay que esperar.
REASON_BY_STATUS = {
    Event.Status.CANCELLED: (FollowUp.Reason.CANCELLED, "follow_up_after_cancelled_days"),
    Event.Status.NO_SHOW: (FollowUp.Reason.NO_SHOW, "follow_up_after_no_show_days"),
    Event.Status.COMPLETED: (FollowUp.Reason.COMPLETED, "follow_up_after_completed_days"),
}


@dataclass(frozen=True)
class FollowUpCandidate:
    """Un lead esperando en la cola. `client` o `contact`, nunca los dos vacíos."""

    phone: str
    name: str
    reason: str
    #: Desde cuándo espera. Ordena la cola: lo más viejo, primero.
    since: object
    client: Client | None = None
    contact: object | None = None
    advisor: object | None = None
    source_event: object | None = None

    @property
    def client_id(self):
        return getattr(self.client, "id", None)

    @property
    def advisor_id(self):
        return getattr(self.advisor, "id", None)


def get_configuration(company):
    """Configuración por defecto de la empresa, o una sin guardar con los defaults."""
    configuration = SchedulingConfiguration.objects.filter(
        company=company, is_default=True, is_active=True
    ).first()
    return configuration or SchedulingConfiguration(company=company)


def follow_ups_due(*, user, at=None) -> list[FollowUpCandidate]:
    """Leads que toca contactar, recortados al alcance del usuario.

    El alcance de los que vienen de una cita lo da `get_events_visible_to_user`,
    el mismo de la agenda. Los que no tienen cita (inactivos y contactos del
    chatbot) no tienen asesor, así que solo los ve quien ve más de una agenda.
    """
    at = at or timezone.now()
    company = get_user_company(user)
    configuration = get_configuration(company)
    if not configuration.follow_up_enabled:
        return []

    candidates = _from_events(user=user, at=at, configuration=configuration)

    if user.is_superuser or user.role in (user.Role.ADMIN, user.Role.SUPERVISOR):
        seen = {candidate.phone for candidate in candidates}
        candidates += _inactive_clients(company=company, at=at, configuration=configuration, exclude=seen)
        seen |= {candidate.phone for candidate in candidates}
        candidates += _chatbot_contacts(company=company, at=at, configuration=configuration, exclude=seen)

    candidates = _without_decided(company=company, at=at, configuration=configuration, candidates=candidates)
    candidates.sort(key=lambda candidate: candidate.since)
    return candidates


# -----------------------------------------------------------------------------
# Los tres orígenes
# -----------------------------------------------------------------------------

def _from_events(*, user, at, configuration) -> list[FollowUpCandidate]:
    """Citas sin cierre: la última del cliente terminó mal o sin concretar."""
    events = (
        get_events_visible_to_user(user=user)
        .filter(client__isnull=False, start_at__gte=at - timedelta(days=LOOKBACK_DAYS))
        .order_by("client_id", "start_at")
    )

    by_client = defaultdict(list)
    for event in events:
        by_client[event.client_id].append(event)

    candidates = []
    for client_events in by_client.values():
        # Con una cita por delante no hay nada que recuperar: el lead está vivo.
        if any(event.start_at > at for event in client_events):
            continue

        last = client_events[-1]
        rule = REASON_BY_STATUS.get(last.status)
        if rule is None:
            continue

        reason, setting = rule
        closed_at = last.end_at or last.start_at
        if closed_at + timedelta(days=getattr(configuration, setting)) > at:
            continue

        candidates.append(
            FollowUpCandidate(
                phone=last.client.normalized_phone,
                name=str(last.client),
                reason=reason,
                since=closed_at,
                client=last.client,
                advisor=last.advisor,
                source_event=last,
            )
        )
    return candidates


def _inactive_clients(*, company, at, configuration, exclude) -> list[FollowUpCandidate]:
    """Clientes vivos de los que hace mucho que no se sabe nada."""
    threshold = at - timedelta(days=configuration.follow_up_inactive_days)

    clients = (
        Client.objects.filter(company=company, is_active=True, created_at__lte=threshold)
        .exclude(normalized_phone__in=exclude)
        .annotate(last_event_at=Max("events__start_at"))
        .filter(last_event_at__isnull=True)
    )
    return [
        FollowUpCandidate(
            phone=client.normalized_phone,
            name=str(client),
            reason=FollowUp.Reason.INACTIVE,
            since=client.created_at,
            client=client,
        )
        for client in clients
    ]


def _chatbot_contacts(*, company, at, configuration, exclude) -> list[FollowUpCandidate]:
    """Escribieron por WhatsApp y nunca llegaron a agendar."""
    # Importado aquí y no arriba: `clients` no debe depender de `inbox` para
    # poder cargarse, solo para esta consulta concreta.
    from apps.inbox.models import Contact

    threshold = at - timedelta(days=configuration.follow_up_inactive_days)
    contacts = (
        Contact.objects.filter(company=company, last_contact_at__lte=threshold)
        .exclude(phone_number__in=exclude)
        .select_related("client")
        .annotate(client_events=Max("client__events__start_at"))
        .filter(client_events__isnull=True)
    )
    return [
        FollowUpCandidate(
            phone=contact.phone_number,
            name=contact.name or contact.phone_number,
            reason=FollowUp.Reason.INACTIVE,
            since=contact.last_contact_at,
            client=contact.client,
            contact=contact,
        )
        for contact in contacts
    ]


# -----------------------------------------------------------------------------
# Lo que sale de la cola
# -----------------------------------------------------------------------------

def _without_decided(*, company, at, configuration, candidates) -> list[FollowUpCandidate]:
    """Quita los leads sobre los que ya se decidió algo que sigue vigente.

    La decisión caduca de dos maneras, y las dos importan:

    * **Actividad nueva.** Si el lead ha vuelto a moverse *después* de que se
      decidiera algo sobre él (`since > decidido_en`), resurge solo. Es lo que
      hace que un descarte no sea una condena: si el cliente vuelve a llamar o
      a cancelar otra cita, reaparece.
    * **El enfriamiento.** Un seguimiento enviado o gestionado vuelve a la cola
      pasado `follow_up_cooldown_days`. Eso es lo que hace que esto sea
      periódico y no un único intento.

    Un descarte explícito no caduca por enfriamiento: solo lo revive la
    actividad nueva.
    """
    if not candidates:
        return []

    phones = {candidate.phone for candidate in candidates}
    cooldown_start = at - timedelta(days=configuration.follow_up_cooldown_days)
    decided = {
        follow_up.normalized_phone: follow_up
        for follow_up in FollowUp.objects.filter(company=company, normalized_phone__in=phones)
    }

    def is_open(candidate) -> bool:
        follow_up = decided.get(candidate.phone)
        if follow_up is None:
            return True
        if candidate.since > follow_up.updated_at:
            return True
        if follow_up.status == FollowUp.Status.DISMISSED:
            return False
        if follow_up.status == FollowUp.Status.SNOOZED:
            return bool(follow_up.due_at) and follow_up.due_at <= at
        return (follow_up.sent_at or follow_up.updated_at) <= cooldown_start

    return [candidate for candidate in candidates if is_open(candidate)]
