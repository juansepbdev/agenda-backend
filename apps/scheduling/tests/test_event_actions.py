"""Máquina de estados de un evento.

Lo importante no es que las transiciones válidas funcionen, sino que las
inválidas se rechacen: es lo único que impide que un evento cancelado vuelva a
"en curso" por una llamada duplicada del panel.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.scheduling.exceptions import (
    CompanyInactiveError,
    EventConflictError,
    InvalidEventTransitionError,
)
from apps.scheduling.models import Event, EventHistory
from apps.scheduling.services import event_actions

from .conftest import make_event

pytestmark = pytest.mark.django_db


def test_confirmar_pasa_de_pendiente_a_confirmado(event, admin_user):
    result = event_actions.confirm_event(event=event, actor=admin_user)

    assert result.status == Event.Status.CONFIRMED
    assert result.confirmed_at is not None
    assert result.confirmed_by == admin_user


def test_confirmar_dos_veces_se_rechaza(event, admin_user):
    event_actions.confirm_event(event=event, actor=admin_user)

    with pytest.raises(InvalidEventTransitionError):
        event_actions.confirm_event(event=event, actor=admin_user)


def test_iniciar_exige_confirmado_previo(event, admin_user):
    with pytest.raises(InvalidEventTransitionError):
        event_actions.start_event(event=event, actor=admin_user)


def test_completar_guarda_las_notas_de_cierre(event, admin_user):
    event_actions.confirm_event(event=event, actor=admin_user)
    result = event_actions.complete_event(event=event, actor=admin_user, notes="Todo bien")

    assert result.status == Event.Status.COMPLETED
    assert result.completion_notes == "Todo bien"


def test_completado_es_terminal(event, admin_user):
    event_actions.confirm_event(event=event, actor=admin_user)
    event_actions.complete_event(event=event, actor=admin_user)

    with pytest.raises(InvalidEventTransitionError):
        event_actions.cancel_event(event=event, actor=admin_user)


def test_cancelar_registra_motivo_y_origen(event, admin_user):
    result = event_actions.cancel_event(event=event, actor=admin_user, reason="El cliente no puede", source="CHATBOT")

    assert result.status == Event.Status.CANCELLED
    assert result.cancellation_reason == "El cliente no puede"
    assert result.cancellation_source == "CHATBOT"


def test_un_no_show_todavia_se_puede_cancelar(event, admin_user):
    event_actions.mark_event_no_show(event=event, actor=admin_user, no_show_type=Event.NoShow.CLIENT_NO_SHOW)
    result = event_actions.cancel_event(event=event, actor=admin_user)

    assert result.status == Event.Status.CANCELLED


def test_cada_transicion_deja_una_entrada_de_historial(event, admin_user):
    event_actions.confirm_event(event=event, actor=admin_user)
    event_actions.start_event(event=event, actor=admin_user)
    event_actions.complete_event(event=event, actor=admin_user)

    acciones = list(EventHistory.objects.filter(event=event).values_list("action", flat=True))
    assert acciones == ["CONFIRMED", "STARTED", "COMPLETED"]


# -----------------------------------------------------------------------------
# Creación manual
# -----------------------------------------------------------------------------


def test_crear_evento_manual(company, advisor, admin_user, configuration):
    start = timezone.now() + timedelta(days=1)
    event = event_actions.create_manual_event(
        company=company,
        advisor=advisor,
        actor=admin_user,
        title="Visita",
        start_at=start,
        end_at=start + timedelta(hours=1),
    )

    assert event.source == Event.Source.MANUAL
    assert EventHistory.objects.filter(event=event, action="CREATED").exists()


def test_no_se_crea_en_una_empresa_suspendida(company, advisor, admin_user):
    company.status = company.Status.SUSPENDED
    company.save(update_fields=["status"])
    start = timezone.now() + timedelta(days=1)

    with pytest.raises(CompanyInactiveError):
        event_actions.create_manual_event(
            company=company,
            advisor=advisor,
            actor=admin_user,
            title="Visita",
            start_at=start,
            end_at=start + timedelta(hours=1),
        )


def test_no_se_crea_con_un_asesor_de_otra_empresa(company, other_company_advisor, admin_user):
    start = timezone.now() + timedelta(days=1)

    with pytest.raises(InvalidEventTransitionError):
        event_actions.create_manual_event(
            company=company,
            advisor=other_company_advisor,
            actor=admin_user,
            title="Visita",
            start_at=start,
            end_at=start + timedelta(hours=1),
        )


def test_no_se_crea_encima_de_otro_evento(company, advisor, admin_user, configuration, event):
    with pytest.raises(EventConflictError):
        event_actions.create_manual_event(
            company=company,
            advisor=advisor,
            actor=admin_user,
            title="Visita solapada",
            start_at=event.start_at,
            end_at=event.end_at,
        )


# -----------------------------------------------------------------------------
# Reasignar y reprogramar
# -----------------------------------------------------------------------------


def test_reasignar_cambia_de_asesor(event, other_advisor, admin_user, configuration):
    result = event_actions.reassign_event(event=event, advisor=other_advisor, actor=admin_user)

    assert result.advisor == other_advisor
    assert EventHistory.objects.filter(event=event, action="REASSIGNED").exists()


def test_no_se_reasigna_a_otra_empresa(event, other_company_advisor, admin_user):
    with pytest.raises(InvalidEventTransitionError):
        event_actions.reassign_event(event=event, advisor=other_company_advisor, actor=admin_user)


def test_reprogramar_crea_un_evento_nuevo_y_marca_el_original(event, admin_user, configuration):
    start = timezone.now() + timedelta(days=3)
    nuevo = event_actions.reschedule_event(
        event=event, actor=admin_user, start_at=start, end_at=start + timedelta(hours=1)
    )
    event.refresh_from_db()

    assert nuevo.id != event.id
    assert nuevo.status == Event.Status.PENDING
    assert nuevo.rescheduled_from_id == event.id
    assert event.status == Event.Status.RESCHEDULED
    assert event.rescheduled_to_id == nuevo.id


def test_no_se_reprograma_un_evento_completado(event, admin_user, configuration):
    event_actions.confirm_event(event=event, actor=admin_user)
    event_actions.complete_event(event=event, actor=admin_user)
    start = timezone.now() + timedelta(days=3)

    with pytest.raises(InvalidEventTransitionError):
        event_actions.reschedule_event(event=event, actor=admin_user, start_at=start, end_at=start + timedelta(hours=1))


def test_reprogramar_conserva_al_cliente(event, admin_user, configuration, client_record):
    start = timezone.now() + timedelta(days=3)
    nuevo = event_actions.reschedule_event(
        event=event, actor=admin_user, start_at=start, end_at=start + timedelta(hours=1)
    )

    assert nuevo.client == client_record


def test_no_se_reprograma_sobre_otro_evento_del_mismo_asesor(company, advisor, admin_user, configuration, event):
    ocupado = make_event(company=company, advisor=advisor, hours_ahead=72)

    with pytest.raises(EventConflictError):
        event_actions.reschedule_event(
            event=event,
            actor=admin_user,
            start_at=ocupado.start_at,
            end_at=ocupado.end_at,
        )
