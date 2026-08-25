"""Endpoints de integración del chatbot.

Es la única superficie de la API expuesta a un sistema externo, así que es donde
más caro sale un 500: n8n reintenta, y un KeyError no distingue entre "payload
mal formado" y "el backend está roto".
"""

import pytest

from apps.clients.models import Client
from apps.scheduling.models import Event

from .conftest import make_event_at, tomorrow_at

pytestmark = pytest.mark.django_db


def payload(start, **overrides):
    body = {
        "idempotency_key": "conv-1-msg-1",
        "client": {"phone": "+573001234567", "first_name": "Laura"},
        "event": {
            "title": "Visita al apartamento",
            "start_at": start.isoformat(),
            "duration_minutes": 60,
        },
    }
    body.update(overrides)
    return body


def test_se_agenda_una_visita(api, advisor, configuration):
    response = api.post("/api/v1/integrations/chatbot/events/", payload(tomorrow_at(9)), format="json")

    assert response.status_code == 201
    assert response.data["assigned_automatically"] is True
    assert Client.objects.filter(normalized_phone="+573001234567").exists()


def test_la_misma_idempotency_key_no_duplica(api, advisor, configuration):
    body = payload(tomorrow_at(9))
    primera = api.post("/api/v1/integrations/chatbot/events/", body, format="json")
    segunda = api.post("/api/v1/integrations/chatbot/events/", body, format="json")

    assert segunda.status_code == 200
    assert segunda.data["id"] == primera.data["id"]
    assert Event.objects.count() == 1


def test_un_asesor_ocupado_no_tumba_la_asignacion(api, company, advisor, other_advisor, configuration):
    """Regresión: `EventConflictError` escapaba de `find_available_advisors`.

    Con el primer asesor ocupado, la petición fallaba entera en vez de asignar
    al que sí estaba libre.
    """
    start = tomorrow_at(9)
    make_event_at(company=company, advisor=advisor, start=start)

    response = api.post("/api/v1/integrations/chatbot/events/", payload(start), format="json")

    assert response.status_code == 201
    assert response.data["advisor"]["id"] == str(other_advisor.id)


def test_sin_asesores_libres_se_responde_con_codigo_de_dominio(api, company, advisor, other_advisor, configuration):
    start = tomorrow_at(9)
    make_event_at(company=company, advisor=advisor, start=start)
    make_event_at(company=company, advisor=other_advisor, start=start)

    response = api.post("/api/v1/integrations/chatbot/events/", payload(start), format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "ADVISOR_UNAVAILABLE"


# -----------------------------------------------------------------------------
# Validación del payload: antes cada campo ausente era un 500
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutacion",
    [
        {"client": {"first_name": "Laura"}},  # falta phone
        {"client": {"phone": "+573001234567"}},  # falta first_name
        {"event": {"start_at": "2030-01-01T10:00:00Z"}},  # falta title
        {"event": {"title": "Visita"}},  # falta start_at
    ],
    ids=["sin-phone", "sin-first_name", "sin-title", "sin-start_at"],
)
def test_un_payload_incompleto_da_400_y_no_500(api, advisor, configuration, mutacion):
    response = api.post(
        "/api/v1/integrations/chatbot/events/",
        payload(tomorrow_at(9), **mutacion),
        format="json",
    )

    assert response.status_code == 400


def test_un_telefono_imposible_da_400(api, advisor, configuration):
    response = api.post(
        "/api/v1/integrations/chatbot/events/",
        payload(tomorrow_at(9), client={"phone": "123", "first_name": "Laura"}),
        format="json",
    )

    assert response.status_code == 400


def test_una_duracion_negativa_da_400(api, advisor, configuration):
    start = tomorrow_at(9)
    response = api.post(
        "/api/v1/integrations/chatbot/events/",
        payload(start, event={"title": "Visita", "start_at": start.isoformat(), "duration_minutes": -30}),
        format="json",
    )

    assert response.status_code == 400


# -----------------------------------------------------------------------------
# Consulta de disponibilidad y cancelación
# -----------------------------------------------------------------------------


def test_consulta_de_disponibilidad(api, advisor, other_advisor, configuration):
    response = api.post(
        "/api/v1/integrations/chatbot/availability/",
        {"start_at": tomorrow_at(9).isoformat(), "duration_minutes": 60},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["available"] is True
    assert len(response.data["advisors"]) == 2


def test_la_disponibilidad_excluye_al_ocupado(api, company, advisor, other_advisor, configuration):
    start = tomorrow_at(9)
    make_event_at(company=company, advisor=advisor, start=start)

    response = api.post(
        "/api/v1/integrations/chatbot/availability/",
        {"start_at": start.isoformat(), "duration_minutes": 60},
        format="json",
    )

    assert [row["id"] for row in response.data["advisors"]] == [str(other_advisor.id)]


def test_cancelar_desde_el_chatbot(api, event):
    response = api.post(
        f"/api/v1/integrations/chatbot/events/{event.id}/cancel/",
        {"cancellation_reason": "El cliente ya no puede"},
        format="json",
    )
    event.refresh_from_db()

    assert response.status_code == 200
    assert event.status == Event.Status.CANCELLED
    assert event.cancellation_source == "CHATBOT"


def test_no_se_cancela_un_evento_de_otra_empresa(api, other_company_event):
    response = api.post(f"/api/v1/integrations/chatbot/events/{other_company_event.id}/cancel/", {}, format="json")

    assert response.status_code == 404
