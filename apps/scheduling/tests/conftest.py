"""Escenario compartido de agenda.

Dos empresas completas: casi todo lo que hay que probar aquí es que la B no ve
ni toca nada de la A. Las fechas son relativas a "ahora" para que las pruebas no
caduquen, y la disponibilidad cubre los siete días de la semana para que el
horario elegido nunca dependa del día en que se ejecuten.
"""

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.advisors.models import Advisor, AdvisorAvailability, AdvisorSupervision
from apps.clients.models import Client
from apps.companies.models import Company
from apps.scheduling.models import Event, SchedulingConfiguration
from apps.users.models import User


def make_company(name, slug):
    return Company.objects.create(name=name, slug=slug, status=Company.Status.ACTIVE, timezone="America/Bogota")


def make_advisor(company, email, code, *, role=User.Role.ADVISOR, **advisor_kwargs):
    user = User.objects.create_user(email=email, password="x", company=company, role=role)
    advisor = Advisor.objects.create(company=company, user=user, code=code, **advisor_kwargs)
    full_week_availability(advisor)
    return advisor


def full_week_availability(advisor):
    """Disponible de 00:00 a 23:59 los siete días.

    Las pruebas de este módulo comprueban transiciones y conflictos, no
    horarios: sin esto cada una tendría que calcular un día laborable válido.
    """
    for day in range(7):
        AdvisorAvailability.objects.create(
            company=advisor.company,
            advisor=advisor,
            day_of_week=day,
            start_time=time(0, 0),
            end_time=time(23, 59),
            configured_by=advisor.user,
        )


def make_event(*, company, advisor, client=None, status=Event.Status.PENDING, hours_ahead=24, minutes=60):
    return make_event_at(
        company=company,
        advisor=advisor,
        client=client,
        status=status,
        start=timezone.now() + timedelta(hours=hours_ahead),
        minutes=minutes,
    )


def make_event_at(*, company, advisor, start, client=None, status=Event.Status.PENDING, minutes=60):
    return Event.objects.create(
        company=company,
        advisor=advisor,
        client=client,
        status=status,
        title="Visita",
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
    )


def tomorrow_at(hour, minute=0):
    """Mañana a una hora local concreta.

    Los límites diarios (`max_daily_events`, estrategia LEAST_EVENTS) agrupan
    por fecha local. Con "ahora + N horas" el resultado depende de la hora a la
    que se ejecute la prueba; con una fecha fija, no.
    """
    day = timezone.localdate() + timedelta(days=1)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def authenticate(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


# -----------------------------------------------------------------------------
# Empresa A
# -----------------------------------------------------------------------------


@pytest.fixture
def company(db):
    return make_company("Inmobiliaria A", "empresa-a")


@pytest.fixture
def configuration(company):
    return SchedulingConfiguration.objects.create(company=company, is_default=True, is_active=True)


@pytest.fixture
def admin_user(company):
    return User.objects.create_user(email="admin@a.co", password="x", company=company, role=User.Role.ADMIN)


@pytest.fixture
def advisor(company):
    return make_advisor(company, "asesor@a.co", "A-001")


@pytest.fixture
def other_advisor(company):
    return make_advisor(company, "asesora@a.co", "A-002")


@pytest.fixture
def supervisor_user(company):
    return User.objects.create_user(email="supervisor@a.co", password="x", company=company, role=User.Role.SUPERVISOR)


@pytest.fixture
def supervision(company, supervisor_user, advisor, admin_user):
    """El supervisor supervisa a `advisor`, pero no a `other_advisor`."""
    return AdvisorSupervision.objects.create(
        company=company,
        supervisor_user=supervisor_user,
        advisor=advisor,
        assigned_by=admin_user,
        valid_from=timezone.localdate(),
    )


@pytest.fixture
def client_record(company):
    return Client.objects.create(
        company=company,
        first_name="Laura",
        last_name="Gómez",
        phone="+573001234567",
        normalized_phone="+573001234567",
    )


@pytest.fixture
def event(company, advisor, client_record):
    return make_event(company=company, advisor=advisor, client=client_record)


# -----------------------------------------------------------------------------
# Empresa B (la vecina, para las pruebas de aislamiento)
# -----------------------------------------------------------------------------


@pytest.fixture
def other_company(db):
    return make_company("Inmobiliaria B", "empresa-b")


@pytest.fixture
def other_company_advisor(other_company):
    return make_advisor(other_company, "asesor@b.co", "B-001")


@pytest.fixture
def other_company_admin(other_company):
    return User.objects.create_user(email="admin@b.co", password="x", company=other_company, role=User.Role.ADMIN)


@pytest.fixture
def other_company_event(other_company, other_company_advisor):
    return make_event(company=other_company, advisor=other_company_advisor)


# -----------------------------------------------------------------------------
# Clientes HTTP
# -----------------------------------------------------------------------------


@pytest.fixture
def api(admin_user):
    return authenticate(admin_user)


@pytest.fixture
def advisor_api(advisor):
    return authenticate(advisor.user)


@pytest.fixture
def supervisor_api(supervisor_user):
    return authenticate(supervisor_user)
