"""Detección de solapes y asignación automática.

`check_event_conflicts` es lo que impide doble reserva; `find_available_advisors`
es lo que el chatbot usa para elegir asesor. Si el primero es demasiado estricto
se bloquean horarios libres, y si el segundo propaga sus errores en vez de
saltar candidatos, un solo asesor ocupado tumba toda la asignación.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.scheduling.models import Event, SchedulingConfiguration
from apps.scheduling.services.assignment import assign_advisor_automatically
from apps.scheduling.services.availability import find_available_advisors
from apps.scheduling.services.conflicts import check_event_conflicts

from .conftest import make_event_at, tomorrow_at

pytestmark = pytest.mark.django_db


# -----------------------------------------------------------------------------
# Solapes
# -----------------------------------------------------------------------------


def test_hay_conflicto_cuando_los_rangos_se_cruzan(company, advisor, event):
    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=event.start_at + timedelta(minutes=30),
        end_at=event.end_at + timedelta(minutes=30),
        raise_error=False,
    )
    assert conflicto == event


def test_no_hay_conflicto_si_uno_empieza_donde_acaba_el_otro(company, advisor, event):
    """El rango es semiabierto: pegar dos visitas seguidas es legítimo."""
    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=event.end_at,
        end_at=event.end_at + timedelta(hours=1),
        raise_error=False,
    )
    assert conflicto is None


def test_los_buffers_separan_eventos_contiguos(company, advisor, event):
    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=event.end_at,
        end_at=event.end_at + timedelta(hours=1),
        buffer_before=15,
        raise_error=False,
    )
    assert conflicto == event


def test_un_evento_cancelado_no_bloquea(company, advisor, event):
    event.status = Event.Status.CANCELLED
    event.save(update_fields=["status"])

    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=event.start_at,
        end_at=event.end_at,
        raise_error=False,
    )
    assert conflicto is None


def test_exclude_event_ignora_el_propio_evento(company, advisor, event):
    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=event.start_at,
        end_at=event.end_at,
        exclude_event=event,
        raise_error=False,
    )
    assert conflicto is None


def test_el_evento_de_otra_empresa_no_cuenta(company, advisor, other_company_event):
    conflicto = check_event_conflicts(
        company=company,
        advisor=advisor,
        start_at=other_company_event.start_at,
        end_at=other_company_event.end_at,
        raise_error=False,
    )
    assert conflicto is None


# -----------------------------------------------------------------------------
# Candidatos disponibles
# -----------------------------------------------------------------------------


def test_un_asesor_ocupado_se_salta_en_vez_de_romper(company, advisor, other_advisor, configuration, event):
    """Regresión: `find_available_advisors` no capturaba `EventConflictError`.

    Con un solo asesor ocupado la excepción escapaba del bucle y tumbaba la
    asignación automática entera en vez de pasar al siguiente candidato.
    """
    disponibles = find_available_advisors(
        company=company,
        start_at=event.start_at,
        end_at=event.end_at,
        configuration=configuration,
    )

    assert disponibles == [other_advisor]


def test_un_asesor_inactivo_no_es_candidato(company, advisor, other_advisor, configuration):
    advisor.is_active = False
    advisor.save(update_fields=["is_active"])
    start = timezone.now() + timedelta(days=1)

    disponibles = find_available_advisors(
        company=company, start_at=start, end_at=start + timedelta(hours=1), configuration=configuration
    )

    assert disponibles == [other_advisor]


def test_se_respeta_el_maximo_diario(company, advisor, other_advisor, configuration):
    advisor.max_daily_events = 1
    advisor.save(update_fields=["max_daily_events"])
    start = tomorrow_at(9)
    make_event_at(company=company, advisor=advisor, start=tomorrow_at(15))

    disponibles = find_available_advisors(
        company=company, start_at=start, end_at=start + timedelta(hours=1), configuration=configuration
    )

    assert advisor not in disponibles


# -----------------------------------------------------------------------------
# Estrategias de asignación
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy",
    [
        SchedulingConfiguration.Strategy.FIRST_AVAILABLE,
        SchedulingConfiguration.Strategy.LEAST_EVENTS,
        SchedulingConfiguration.Strategy.ROUND_ROBIN,
        SchedulingConfiguration.Strategy.PRIORITY,
        SchedulingConfiguration.Strategy.RANDOM,
    ],
)
def test_toda_estrategia_devuelve_un_candidato_valido(company, advisor, other_advisor, configuration, strategy):
    configuration.assignment_strategy = strategy
    configuration.save(update_fields=["assignment_strategy"])
    start = timezone.now() + timedelta(days=1)

    elegido = assign_advisor_automatically(
        company=company, start_at=start, end_at=start + timedelta(hours=1), configuration=configuration
    )

    assert elegido in (advisor, other_advisor)


def test_la_estrategia_por_prioridad_elige_el_numero_mas_bajo(company, advisor, other_advisor, configuration):
    advisor.assignment_priority = 10
    advisor.save(update_fields=["assignment_priority"])
    other_advisor.assignment_priority = 90
    other_advisor.save(update_fields=["assignment_priority"])
    configuration.assignment_strategy = SchedulingConfiguration.Strategy.PRIORITY
    configuration.save(update_fields=["assignment_strategy"])
    start = timezone.now() + timedelta(days=1)

    elegido = assign_advisor_automatically(
        company=company, start_at=start, end_at=start + timedelta(hours=1), configuration=configuration
    )

    assert elegido == advisor


def test_la_estrategia_por_carga_elige_al_que_tiene_menos_eventos(company, advisor, other_advisor, configuration):
    start = tomorrow_at(9)
    make_event_at(company=company, advisor=advisor, start=tomorrow_at(15))
    configuration.assignment_strategy = SchedulingConfiguration.Strategy.LEAST_EVENTS
    configuration.save(update_fields=["assignment_strategy"])

    elegido = assign_advisor_automatically(
        company=company, start_at=start, end_at=start + timedelta(hours=1), configuration=configuration
    )

    assert elegido == other_advisor
