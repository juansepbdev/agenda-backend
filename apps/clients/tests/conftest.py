"""Escenario del seguimiento de leads.

Dos empresas, como en el resto del proyecto: lo primero que hay que poder
demostrar es que la B nunca ve un lead de la A. Los plazos se dejan a cero en la
configuración por defecto para que un evento de ayer venza ya y las pruebas no
dependan de esperar treinta días.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.advisors.models import Advisor, AdvisorSupervision
from apps.clients.models import Client
from apps.companies.models import Company
from apps.inbox.models import Contact, WhatsAppChannel
from apps.inbox.services import ycloud
from apps.scheduling.models import Event, SchedulingConfiguration
from apps.users.models import User


def make_company(name, slug):
    return Company.objects.create(name=name, slug=slug, status=Company.Status.ACTIVE, timezone="America/Bogota")


def make_advisor(company, email, code, *, role=User.Role.ADVISOR):
    user = User.objects.create_user(email=email, password="x", company=company, role=role)
    return Advisor.objects.create(company=company, user=user, code=code)


def make_client(company, *, phone, first_name="Lead", **kwargs):
    return Client.objects.create(
        company=company, first_name=first_name, phone=phone, normalized_phone=phone, **kwargs
    )


def make_event(*, company, advisor, client, status, days_ago=None, days_ahead=None, minutes=60):
    """Un evento situado en el pasado o en el futuro respecto a ahora."""
    offset = timedelta(days=days_ahead) if days_ahead is not None else -timedelta(days=days_ago or 1)
    start = timezone.now() + offset
    return Event.objects.create(
        company=company,
        advisor=advisor,
        client=client,
        status=status,
        title="Visita",
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
    )


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
    """Plazos a cero: lo de ayer vence hoy y las pruebas no esperan un mes."""
    return SchedulingConfiguration.objects.create(
        company=company,
        is_default=True,
        is_active=True,
        follow_up_after_cancelled_days=0,
        follow_up_after_no_show_days=0,
        follow_up_after_completed_days=0,
        follow_up_inactive_days=0,
        follow_up_cooldown_days=30,
    )


@pytest.fixture
def admin_user(company, configuration):
    return User.objects.create_user(email="admin@a.co", password="x", company=company, role=User.Role.ADMIN)


@pytest.fixture
def advisor(company):
    return make_advisor(company, "asesor@a.co", "A-001")


@pytest.fixture
def other_advisor(company):
    return make_advisor(company, "asesora@a.co", "A-002")


@pytest.fixture
def supervisor_user(company, advisor, admin_user):
    user = User.objects.create_user(
        email="supervisor@a.co", password="x", company=company, role=User.Role.SUPERVISOR
    )
    AdvisorSupervision.objects.create(
        company=company,
        supervisor_user=user,
        advisor=advisor,
        assigned_by=admin_user,
        valid_from=timezone.localdate(),
    )
    return user


@pytest.fixture
def channel(company):
    return WhatsAppChannel.objects.create(
        company=company, ycloud_api_key="key-demo", ycloud_from="+573001111111"
    )


@pytest.fixture
def api(admin_user):
    return authenticate(admin_user)


@pytest.fixture
def advisor_api(advisor):
    return authenticate(advisor.user)


@pytest.fixture
def supervisor_api(supervisor_user):
    return authenticate(supervisor_user)


# -----------------------------------------------------------------------------
# Empresa B
# -----------------------------------------------------------------------------


@pytest.fixture
def other_company(db):
    company = make_company("Inmobiliaria B", "empresa-b")
    SchedulingConfiguration.objects.create(
        company=company,
        is_default=True,
        is_active=True,
        follow_up_after_completed_days=0,
        follow_up_inactive_days=0,
    )
    return company


# -----------------------------------------------------------------------------
# Nadie toca la red
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_ycloud(monkeypatch):
    """YCloud responde OK sin salir a internet. Devuelve lo que se le mandó."""
    sent = []

    def _send(*, channel, to, template, language="es", variables=(), from_number=None):
        sent.append({"to": to, "template": template, "language": language, "variables": tuple(variables)})
        return {"ok": True, "error": None, "wamid": f"wamid.f{len(sent)}", "raw": {}, "status_code": 200}

    monkeypatch.setattr(ycloud, "send_whatsapp_template", _send)
    return sent


@pytest.fixture
def contact(company):
    """Contacto del chatbot que escribió hace tiempo y nunca agendó."""
    return Contact.objects.create(
        company=company,
        phone_number="+573009998877",
        name="Curioso",
        last_contact_at=timezone.now() - timedelta(days=1),
    )
