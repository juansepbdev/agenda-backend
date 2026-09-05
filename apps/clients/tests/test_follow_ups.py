"""Seguimiento de leads: quién entra en la cola, quién la ve y qué se envía.

Mismo estilo que `apps/scheduling/tests/test_scope_and_isolation.py`: dos
empresas siempre, y lo que queda fuera de alcance no aparece.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.clients import follow_ups as service
from apps.clients.models import FollowUp
from apps.clients.selectors import follow_ups_due
from apps.inbox.models import Conversation, Message
from apps.scheduling.models import Event

from .conftest import make_client, make_event

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/follow-ups/"
CRON_URL = "/api/v1/cron/follow-ups/"


def phones(candidates):
    return {candidate.phone for candidate in candidates}


# -----------------------------------------------------------------------------
# Quién entra en la cola
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (Event.Status.CANCELLED, FollowUp.Reason.CANCELLED),
        (Event.Status.NO_SHOW, FollowUp.Reason.NO_SHOW),
        (Event.Status.COMPLETED, FollowUp.Reason.COMPLETED),
    ],
)
def test_una_cita_sin_cierre_entra_con_su_motivo(admin_user, company, advisor, status, reason):
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=status, days_ago=2)

    candidates = follow_ups_due(user=admin_user)

    assert [candidate.reason for candidate in candidates] == [reason]


def test_dentro_del_plazo_todavia_no_entra(admin_user, company, advisor, configuration):
    configuration.follow_up_after_completed_days = 30
    configuration.save(update_fields=["follow_up_after_completed_days"])
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.COMPLETED, days_ago=2)

    assert follow_ups_due(user=admin_user) == []


def test_un_cliente_que_reagendo_no_entra(admin_user, company, advisor):
    """Canceló, pero ya tiene otra cita: el lead está vivo, no hay nada que recuperar."""
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=2)
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CONFIRMED, days_ahead=3)

    assert follow_ups_due(user=admin_user) == []


def test_varias_cancelaciones_producen_un_solo_lead(admin_user, company, advisor):
    client = make_client(company, phone="+573001112233")
    for days_ago in (10, 6, 2):
        make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=days_ago)

    assert len(follow_ups_due(user=admin_user)) == 1


def test_un_contacto_del_chatbot_que_nunca_agendo_entra(admin_user, contact):
    candidates = follow_ups_due(user=admin_user)

    assert phones(candidates) == {contact.phone_number}
    assert candidates[0].reason == FollowUp.Reason.INACTIVE


def test_la_funcionalidad_se_puede_apagar_por_empresa(admin_user, company, advisor, configuration):
    configuration.follow_up_enabled = False
    configuration.save(update_fields=["follow_up_enabled"])
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=2)

    assert follow_ups_due(user=admin_user) == []


# -----------------------------------------------------------------------------
# Quién ve qué
# -----------------------------------------------------------------------------


def test_un_asesor_solo_ve_sus_leads(advisor_api, company, advisor, other_advisor, configuration):
    mine = make_client(company, phone="+573001111111", first_name="Mío")
    theirs = make_client(company, phone="+573002222222", first_name="Ajeno")
    make_event(company=company, advisor=advisor, client=mine, status=Event.Status.CANCELLED, days_ago=2)
    make_event(company=company, advisor=other_advisor, client=theirs, status=Event.Status.CANCELLED, days_ago=2)

    response = advisor_api.get(LIST_URL)

    assert [row["phone"] for row in response.json()["results"]] == ["+573001111111"]


def test_un_supervisor_ve_los_de_su_equipo(supervisor_api, company, advisor, other_advisor, configuration):
    mine = make_client(company, phone="+573001111111")
    theirs = make_client(company, phone="+573002222222")
    make_event(company=company, advisor=advisor, client=mine, status=Event.Status.CANCELLED, days_ago=2)
    make_event(company=company, advisor=other_advisor, client=theirs, status=Event.Status.CANCELLED, days_ago=2)

    response = supervisor_api.get(LIST_URL)

    assert [row["phone"] for row in response.json()["results"]] == ["+573001111111"]


def test_un_asesor_no_ve_los_leads_sin_asesor(advisor_api, contact, configuration):
    """Un contacto del bot no tiene dueño: lo reparte administración."""
    assert advisor_api.get(LIST_URL).json()["results"] == []


def test_ninguna_empresa_ve_los_leads_de_la_otra(admin_user, other_company, configuration):
    make_client(other_company, phone="+573009999999")

    assert follow_ups_due(user=admin_user) == []


# -----------------------------------------------------------------------------
# Lo que saca un lead de la cola
# -----------------------------------------------------------------------------


def test_descartar_saca_el_lead_de_la_cola(api, admin_user, company, advisor, configuration):
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=2)

    response = api.post(
        "/api/v1/follow-ups/decide/",
        {"phone": "+573001112233", "status": FollowUp.Status.DISMISSED},
        format="json",
    )

    assert response.status_code == 200
    assert follow_ups_due(user=admin_user) == []


def test_posponer_lo_devuelve_a_la_cola_cuando_vence(api, admin_user, company, advisor, configuration):
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=2)
    api.post(
        "/api/v1/follow-ups/decide/",
        {
            "phone": "+573001112233",
            "status": FollowUp.Status.SNOOZED,
            "due_at": (timezone.now() + timedelta(days=5)).isoformat(),
        },
        format="json",
    )

    assert follow_ups_due(user=admin_user) == []

    FollowUp.objects.filter(company=company).update(due_at=timezone.now() - timedelta(minutes=1))
    assert len(follow_ups_due(user=admin_user)) == 1


def test_una_cita_nueva_resucita_un_lead_descartado(admin_user, company, advisor, configuration):
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=30)
    follow_up = FollowUp.objects.create(
        company=company,
        normalized_phone="+573001112233",
        client=client,
        reason=FollowUp.Reason.CANCELLED,
        status=FollowUp.Status.DISMISSED,
    )
    # `updated_at` es `auto_now`: para que el descarte sea anterior al evento
    # nuevo hay que fecharlo a mano.
    FollowUp.objects.filter(pk=follow_up.pk).update(updated_at=timezone.now() - timedelta(days=10))
    assert follow_ups_due(user=admin_user) == []

    # Volvió a moverse después del descarte: un "no" no es una condena.
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.NO_SHOW, days_ago=1)

    assert len(follow_ups_due(user=admin_user)) == 1


# -----------------------------------------------------------------------------
# El envío
# -----------------------------------------------------------------------------


def _configure_template(configuration):
    configuration.follow_up_template = "seguimiento_lead"
    configuration.save(update_fields=["follow_up_template"])


def test_dos_despachos_seguidos_envian_una_sola_vez(admin_user, company, advisor, configuration, channel, fake_ycloud):
    _configure_template(configuration)
    client = make_client(company, phone="+573001112233")
    make_event(company=company, advisor=advisor, client=client, status=Event.Status.CANCELLED, days_ago=2)

    first = service.dispatch_company(company=company)
    second = service.dispatch_company(company=company)

    assert (first["sent"], second["sent"]) == (1, 0)
    assert len(fake_ycloud) == 1


def test_el_envio_no_le_roba_la_conversacion_al_asesor(admin_user, company, advisor, configuration, channel, contact):
    """Un seguimiento es una anotación del sistema, no un mensaje del asesor."""
    _configure_template(configuration)
    conversation = Conversation.objects.create(company=company, contact=contact, assignment=Conversation.Assignment.ME)

    service.dispatch_company(company=company)

    conversation.refresh_from_db()
    assert conversation.assignment == Conversation.Assignment.ME
    message = Message.objects.get(conversation=conversation)
    assert message.content_type == Message.ContentType.SYSTEM


def test_el_envio_funciona_aunque_el_chatbot_este_encendido(admin_user, company, configuration, channel, contact):
    """La regla R1 gobierna al asesor, no al despacho automático."""
    _configure_template(configuration)
    assert contact.chatbot_enabled is True

    result = service.dispatch_company(company=company)

    assert result["sent"] == 1
    assert FollowUp.objects.get(company=company).message_status == "sent"


def test_sin_plantilla_no_se_envia_pero_queda_escrito_por_que(
    admin_user, company, configuration, channel, contact, fake_ycloud
):
    result = service.dispatch_company(company=company)

    # Se cuenta como omitido, no como enviado: el informe no puede mentir.
    assert (result["sent"], result["skipped_sends"]) == (0, 1)
    assert fake_ycloud == []
    assert FollowUp.objects.get(company=company).message_status == "skipped:sin-plantilla"


def test_dry_run_cuenta_pero_no_envia(admin_user, company, configuration, channel, contact, fake_ycloud):
    _configure_template(configuration)

    result = service.dispatch_company(company=company, dry_run=True)

    assert result["pending"] == 1
    assert fake_ycloud == []
    assert not FollowUp.objects.exists()


# -----------------------------------------------------------------------------
# El cron
# -----------------------------------------------------------------------------


def test_el_cron_sin_credencial_responde_401(client, settings, admin_user, company, configuration):
    settings.CRON_SECRET = "s3creto"

    assert client.get(CRON_URL).status_code == 401


def test_el_cron_sin_secreto_configurado_deniega(client, settings, admin_user, company, configuration):
    """Vacío significa denegar, nunca «sin secreto, pasa»."""
    settings.CRON_SECRET = ""

    assert client.get(CRON_URL, HTTP_AUTHORIZATION="Bearer ").status_code == 401


def test_el_cron_con_la_credencial_correcta_despacha(
    client, settings, admin_user, company, advisor, configuration, channel, contact
):
    settings.CRON_SECRET = "s3creto"
    _configure_template(configuration)

    response = client.get(CRON_URL, HTTP_AUTHORIZATION="Bearer s3creto")

    assert response.status_code == 200
    assert response.json()["sent"] == 1
