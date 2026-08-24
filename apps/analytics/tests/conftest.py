"""Escenario compartido del dashboard.

Se construye con fechas relativas a "ahora" para que las pruebas no caduquen, y
todo cae dentro de los últimos 7 días para poder consultarlo con `period=7d`.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.advisors.models import Advisor
from apps.clients.models import Client
from apps.companies.models import Company
from apps.inbox.models import Contact, Conversation, Message
from apps.scheduling.models import Event
from apps.users.models import User


@pytest.fixture
def company(db):
    return Company.objects.create(
        name="Inmobiliaria Demo", slug="demo", status=Company.Status.ACTIVE, timezone="America/Bogota"
    )


@pytest.fixture
def admin_user(company):
    return User.objects.create_user(
        email="admin@demo.co", password="x", company=company, role=User.Role.ADMIN
    )


@pytest.fixture
def advisor_user(company):
    return User.objects.create_user(
        email="asesor@demo.co", password="x", company=company, role=User.Role.ADVISOR,
        first_name="Carlos", last_name="Pérez",
    )


@pytest.fixture
def advisor(company, advisor_user):
    return Advisor.objects.create(company=company, user=advisor_user, code="A-001")


@pytest.fixture
def other_advisor(company):
    user = User.objects.create_user(
        email="asesora@demo.co", password="x", company=company, role=User.Role.ADVISOR,
        first_name="Ana", last_name="Ruiz",
    )
    return Advisor.objects.create(company=company, user=user, code="A-002")


@pytest.fixture
def api(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def advisor_api(advisor_user):
    client = APIClient()
    client.force_authenticate(user=advisor_user)
    return client


def make_message(*, company, conversation, contact, content, sender, minutes_ago):
    """Inserta un mensaje con `created_at` controlado (es `auto_now_add`)."""
    inbound = sender == Message.Sender.CONTACT
    message = Message.objects.create(
        company=company,
        conversation=conversation,
        contact=contact,
        content=content,
        message_type=Message.Type.INBOUND if inbound else Message.Type.OUTBOUND,
        sender_type=sender,
        status=Message.Status.RECEIVED if inbound else Message.Status.SENT,
    )
    moment = timezone.now() - timedelta(minutes=minutes_ago)
    Message.objects.filter(pk=message.pk).update(created_at=moment)
    message.created_at = moment
    return message


@pytest.fixture
def conversation_data(company):
    """Un hilo con dos entrantes y dos respuestas: una del bot, otra del asesor."""
    contact = Contact.objects.create(company=company, phone_number="+573001234567", name="Laura")
    conversation = Conversation.objects.create(company=company, contact=contact)

    # Entrante a los -60 min, el bot responde a los -59 (60 s de respuesta).
    make_message(company=company, conversation=conversation, contact=contact,
                 content="Hola", sender=Message.Sender.CONTACT, minutes_ago=60)
    make_message(company=company, conversation=conversation, contact=contact,
                 content="Hola, soy el bot", sender=Message.Sender.BOT, minutes_ago=59)
    # Entrante a los -30 min, el asesor responde a los -20 (600 s).
    make_message(company=company, conversation=conversation, contact=contact,
                 content="Quiero visitar", sender=Message.Sender.CONTACT, minutes_ago=30)
    make_message(company=company, conversation=conversation, contact=contact,
                 content="Claro, te agendo", sender=Message.Sender.AGENT, minutes_ago=20)

    conversation.last_activity_at = timezone.now() - timedelta(minutes=20)
    conversation.save(update_fields=["last_activity_at"])
    return {"contact": contact, "conversation": conversation}


@pytest.fixture
def unanswered_conversation(company):
    """Un contacto que escribió y nadie contestó."""
    contact = Contact.objects.create(company=company, phone_number="+573009998877", name="Pedro")
    conversation = Conversation.objects.create(company=company, contact=contact)
    make_message(company=company, conversation=conversation, contact=contact,
                 content="¿Hay alguien?", sender=Message.Sender.CONTACT, minutes_ago=15)
    return conversation


@pytest.fixture
def client_record(company):
    return Client.objects.create(
        company=company, first_name="Laura", last_name="Gómez",
        phone="+573001234567", normalized_phone="+573001234567", source=Client.Source.CHATBOT,
    )


def make_event(*, company, advisor, client=None, status, source=Event.Source.MANUAL, hours_ahead=2):
    start = timezone.now() + timedelta(hours=hours_ahead)
    return Event.objects.create(
        company=company, advisor=advisor, client=client, status=status, source=source,
        title="Visita", start_at=start, end_at=start + timedelta(minutes=60),
    )


@pytest.fixture
def events(company, advisor, other_advisor, client_record):
    """Cinco eventos: tres del asesor principal y dos de la otra asesora."""
    return [
        make_event(company=company, advisor=advisor, client=client_record,
                   status=Event.Status.COMPLETED, source=Event.Source.CHATBOT, hours_ahead=-5),
        make_event(company=company, advisor=advisor, client=client_record,
                   status=Event.Status.CANCELLED, hours_ahead=-3),
        make_event(company=company, advisor=advisor, status=Event.Status.PENDING, hours_ahead=4),
        make_event(company=company, advisor=other_advisor, status=Event.Status.COMPLETED, hours_ahead=-2),
        make_event(company=company, advisor=other_advisor, status=Event.Status.NO_SHOW, hours_ahead=-1),
    ]
