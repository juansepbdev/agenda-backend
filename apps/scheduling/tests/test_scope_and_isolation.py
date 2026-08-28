"""Alcance por rol y aislamiento entre empresas.

`get_events_visible_to_user` y `advisors_visible_to_user` son las dos funciones
de las que cuelga todo el recorte de lectura. Un fallo aquí no da error: da
datos de más, en silencio.
"""

import pytest

from apps.advisors.views import advisors_visible_to_user
from apps.scheduling.selectors import get_events_visible_to_user

from .conftest import make_event

pytestmark = pytest.mark.django_db


# -----------------------------------------------------------------------------
# Eventos visibles
# -----------------------------------------------------------------------------


def test_administracion_ve_toda_la_empresa(company, admin_user, advisor, other_advisor):
    mio = make_event(company=company, advisor=advisor)
    ajeno = make_event(company=company, advisor=other_advisor, hours_ahead=48)

    visibles = set(get_events_visible_to_user(user=admin_user))

    assert visibles == {mio, ajeno}


def test_un_asesor_solo_ve_los_suyos(company, advisor, other_advisor):
    mio = make_event(company=company, advisor=advisor)
    make_event(company=company, advisor=other_advisor, hours_ahead=48)

    visibles = set(get_events_visible_to_user(user=advisor.user))

    assert visibles == {mio}


def test_un_supervisor_ve_los_de_su_equipo_y_no_mas(company, supervisor_user, supervision, advisor, other_advisor):
    supervisado = make_event(company=company, advisor=advisor)
    make_event(company=company, advisor=other_advisor, hours_ahead=48)

    visibles = set(get_events_visible_to_user(user=supervisor_user))

    assert visibles == {supervisado}


def test_nadie_ve_los_eventos_de_otra_empresa(admin_user, other_company_event):
    assert other_company_event not in get_events_visible_to_user(user=admin_user)


# -----------------------------------------------------------------------------
# Asesores visibles
# -----------------------------------------------------------------------------


def test_administracion_ve_todos_los_asesores(admin_user, advisor, other_advisor):
    assert set(advisors_visible_to_user(admin_user)) == {advisor, other_advisor}


def test_un_asesor_se_ve_a_si_mismo(advisor, other_advisor):
    """Sin esto el frontend no puede descubrir su propio `advisor_id`."""
    assert set(advisors_visible_to_user(advisor.user)) == {advisor}


def test_un_supervisor_ve_a_su_equipo(supervisor_user, supervision, advisor, other_advisor):
    assert set(advisors_visible_to_user(supervisor_user)) == {advisor}


def test_los_asesores_de_otra_empresa_nunca_aparecen(admin_user, other_company_advisor):
    assert other_company_advisor not in advisors_visible_to_user(admin_user)


# -----------------------------------------------------------------------------
# La API respeta el mismo alcance
# -----------------------------------------------------------------------------


def test_la_api_de_eventos_recorta_por_rol(advisor_api, company, advisor, other_advisor):
    make_event(company=company, advisor=advisor)
    make_event(company=company, advisor=other_advisor, hours_ahead=48)

    response = advisor_api.get("/api/v1/events/")

    assert response.status_code == 200
    assert response.data["count"] == 1


def test_un_asesor_no_alcanza_el_evento_de_otra_empresa(advisor_api, other_company_event):
    response = advisor_api.get(f"/api/v1/events/{other_company_event.id}/")

    assert response.status_code == 404


def test_un_admin_no_alcanza_el_evento_de_otra_empresa(api, other_company_event):
    assert api.get(f"/api/v1/events/{other_company_event.id}/").status_code == 404


def test_un_asesor_ya_puede_listar_asesores(advisor_api, advisor, other_advisor):
    """Antes devolvía 403 y el frontend se quedaba sin `advisor_id` propio."""
    response = advisor_api.get("/api/v1/advisors/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [str(advisor.id)]


def test_un_asesor_no_puede_crear_asesores(advisor_api, company, advisor):
    response = advisor_api.post("/api/v1/advisors/", {"user": str(advisor.user_id), "code": "A-999"}, format="json")

    assert response.status_code == 403


def test_un_admin_no_ve_clientes_de_otra_empresa(api, client_record, other_company):
    from apps.clients.models import Client

    Client.objects.create(
        company=other_company,
        first_name="Ajeno",
        phone="+573009999999",
        normalized_phone="+573009999999",
    )

    response = api.get("/api/v1/clients/")

    assert [row["id"] for row in response.data["results"]] == [str(client_record.id)]
