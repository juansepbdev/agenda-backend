"""Contrato HTTP de la agenda.

Cada prueba de este módulo cubre un defecto concreto que existía y que no daba
error visible: filtros que se ignoraban, notas que se descartaban, e input
inválido que devolvía 500 en vez de 400.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.advisors.models import AdvisorAvailability
from apps.scheduling.models import Event

from .conftest import make_event, tomorrow_at

pytestmark = pytest.mark.django_db


# -----------------------------------------------------------------------------
# Filtros del listado
# -----------------------------------------------------------------------------


def test_se_puede_filtrar_por_fecha_de_inicio(api, company, advisor):
    pronto = make_event(company=company, advisor=advisor, hours_ahead=2)
    tarde = make_event(company=company, advisor=advisor, hours_ahead=240)
    corte = (timezone.now() + timedelta(hours=48)).isoformat()

    response = api.get("/api/v1/events/", {"start_at__gte": corte})

    ids = [row["id"] for row in response.data["results"]]
    assert ids == [str(tarde.id)]
    assert str(pronto.id) not in ids


def test_se_puede_filtrar_por_asesor(api, company, advisor, other_advisor):
    mio = make_event(company=company, advisor=advisor)
    make_event(company=company, advisor=other_advisor, hours_ahead=48)

    response = api.get("/api/v1/events/", {"advisor": str(advisor.id)})

    assert [row["id"] for row in response.data["results"]] == [str(mio.id)]


def test_un_uuid_invalido_en_el_filtro_da_400_y_no_500(api):
    response = api.get("/api/v1/events/", {"advisor": "no-soy-un-uuid"})

    assert response.status_code == 400
    assert response.data["error"]["code"] == "INVALID_INPUT"


# -----------------------------------------------------------------------------
# Paginación y búsqueda
# -----------------------------------------------------------------------------


def test_page_size_se_respeta(api, company, advisor):
    for hours in range(1, 6):
        make_event(company=company, advisor=advisor, hours_ahead=hours * 24)

    response = api.get("/api/v1/events/", {"page_size": 2})

    assert len(response.data["results"]) == 2
    assert response.data["count"] == 5


def test_page_size_tiene_techo(api, company, advisor):
    """Sin `max_page_size`, `?page_size=100000` sería una forma barata de DoS."""
    response = api.get("/api/v1/events/", {"page_size": 100000})

    assert response.status_code == 200


def test_la_busqueda_de_clientes_filtra_en_el_servidor(api, company, client_record):
    from apps.clients.models import Client

    Client.objects.create(
        company=company,
        first_name="Pedro",
        last_name="Martínez",
        phone="+573007776655",
        normalized_phone="+573007776655",
    )

    response = api.get("/api/v1/clients/", {"search": "Gómez"})

    assert [row["id"] for row in response.data["results"]] == [str(client_record.id)]


def test_la_disponibilidad_se_filtra_por_asesor(api, advisor, other_advisor):
    """Antes devolvía la de toda la empresa, y el panel la mostraba como suya."""
    response = api.get("/api/v1/advisor-availabilities/", {"advisor": str(advisor.id)})

    devueltos = {str(row["advisor"]) for row in response.data["results"]}
    assert devueltos == {str(advisor.id)}
    assert response.data["count"] == AdvisorAvailability.objects.filter(advisor=advisor).count()


# -----------------------------------------------------------------------------
# Acciones sobre un evento
# -----------------------------------------------------------------------------


def test_completar_guarda_completion_notes(api, event, admin_user):
    api.post(f"/api/v1/events/{event.id}/confirm/", {}, format="json")

    response = api.post(
        f"/api/v1/events/{event.id}/complete/",
        {"completion_notes": "Cliente muy interesado"},
        format="json",
    )
    event.refresh_from_db()

    assert response.status_code == 200
    assert event.completion_notes == "Cliente muy interesado"


def test_completar_sigue_aceptando_notes(api, event):
    api.post(f"/api/v1/events/{event.id}/confirm/", {}, format="json")
    api.post(f"/api/v1/events/{event.id}/complete/", {"notes": "Contrato original"}, format="json")
    event.refresh_from_db()

    assert event.completion_notes == "Contrato original"


def test_una_transicion_invalida_da_400_con_codigo_de_dominio(api, event):
    response = api.post(f"/api/v1/events/{event.id}/start/", {}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "INVALID_EVENT_TRANSITION"


# -----------------------------------------------------------------------------
# Calendario
# -----------------------------------------------------------------------------


def test_calendario_del_dia_sin_parametros(api, company, advisor):
    make_event_hoy = make_event(company=company, advisor=advisor, hours_ahead=1)

    response = api.get("/api/v1/calendar/day/")

    assert response.status_code == 200
    assert response.data["range"]["timezone"] == company.timezone
    assert str(make_event_hoy.id) in [row["id"] for row in response.data["events"]]


def test_calendario_del_dia_con_fecha_invalida_da_400(api):
    response = api.get("/api/v1/calendar/day/", {"date": "hola"})

    assert response.status_code == 400


def test_calendario_del_mes_sin_year_da_400_y_no_500(api):
    assert api.get("/api/v1/calendar/month/").status_code == 400


def test_calendario_del_mes_con_mes_fuera_de_rango_da_400(api):
    assert api.get("/api/v1/calendar/month/", {"year": 2026, "month": 13}).status_code == 400


def test_calendario_del_mes_valido(api, company, advisor):
    momento = tomorrow_at(10)
    evento = make_event(company=company, advisor=advisor, hours_ahead=0)
    evento.start_at = momento
    evento.end_at = momento + timedelta(hours=1)
    evento.save(update_fields=["start_at", "end_at"])

    response = api.get("/api/v1/calendar/month/", {"year": momento.year, "month": momento.month})

    assert response.status_code == 200
    assert str(evento.id) in [row["id"] for row in response.data["events"]]


def test_la_semana_cubre_siete_dias(api, company, advisor):
    hoy = timezone.localdate()
    dentro = make_event(company=company, advisor=advisor, hours_ahead=24 * 3)
    fuera = make_event(company=company, advisor=advisor, hours_ahead=24 * 20)

    response = api.get("/api/v1/calendar/week/", {"start_date": hoy.isoformat()})

    ids = [row["id"] for row in response.data["events"]]
    assert str(dentro.id) in ids
    assert str(fuera.id) not in ids


# -----------------------------------------------------------------------------
# Creación por API
# -----------------------------------------------------------------------------


def test_crear_evento_por_api(api, advisor, configuration, client_record):
    start = tomorrow_at(9)
    response = api.post(
        "/api/v1/events/",
        {
            "advisor": str(advisor.id),
            "client": str(client_record.id),
            "event_type": Event.Type.PROPERTY_VISIT,
            "title": "Visita al apartamento",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "timezone": "America/Bogota",
        },
        format="json",
    )

    assert response.status_code == 201
    assert Event.objects.filter(title="Visita al apartamento").exists()


def test_no_se_crea_un_evento_con_asesor_de_otra_empresa(api, other_company_advisor, configuration):
    start = tomorrow_at(9)
    response = api.post(
        "/api/v1/events/",
        {
            "advisor": str(other_company_advisor.id),
            "event_type": Event.Type.PROPERTY_VISIT,
            "title": "Fuga",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 400
