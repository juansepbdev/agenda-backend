"""R8 — cada asesor ve solo sus conversaciones, y cómo llegan a ser suyas.

Mismo criterio y mismo estilo que `apps/scheduling/tests/test_scope_and_isolation.py`:
lo que queda fuera de alcance responde 404, nunca 403, para no confirmar que
existe.
"""

import pytest

from apps.inbox.models import Contact, Conversation, Message
from apps.inbox.services import messaging
from apps.inbox.services.assignment import pick_advisor

pytestmark = pytest.mark.django_db

CONVERSATIONS_URL = "/api/v1/inbox/conversations/"


def make_conversation(company, *, advisor=None, phone="+573001234567", chatbot=False):
    contact = Contact.objects.create(company=company, phone_number=phone, name="Contacto", chatbot_enabled=chatbot)
    return Conversation.objects.create(company=company, contact=contact, advisor=advisor)


def names(response):
    return {c["contact"]["phone_number"] for c in response.json()["conversations"]}


# -----------------------------------------------------------------------------
# Alcance por rol
# -----------------------------------------------------------------------------

def test_administracion_ve_todas_las_conversaciones_de_la_empresa(api, company, advisor, other_advisor):
    make_conversation(company, advisor=advisor, phone="+573001111111")
    make_conversation(company, advisor=other_advisor, phone="+573002222222")
    make_conversation(company, advisor=None, phone="+573003333333")

    response = api.get(CONVERSATIONS_URL)

    assert response.status_code == 200
    assert len(response.json()["conversations"]) == 3


def test_un_asesor_solo_ve_las_suyas(advisor_api, company, advisor, other_advisor):
    make_conversation(company, advisor=advisor, phone="+573001111111")
    make_conversation(company, advisor=other_advisor, phone="+573002222222")
    make_conversation(company, advisor=None, phone="+573003333333")

    response = advisor_api.get(CONVERSATIONS_URL)

    assert names(response) == {"+573001111111"}


def test_un_supervisor_ve_las_de_su_equipo_y_las_sin_asignar(supervisor_api, company, advisor, other_advisor):
    make_conversation(company, advisor=advisor, phone="+573001111111")
    make_conversation(company, advisor=other_advisor, phone="+573002222222")
    make_conversation(company, advisor=None, phone="+573003333333")

    response = supervisor_api.get(CONVERSATIONS_URL)

    assert names(response) == {"+573001111111", "+573003333333"}


def test_la_conversacion_de_otro_asesor_responde_404(advisor_api, company, other_advisor):
    conversation = make_conversation(company, advisor=other_advisor)

    assert advisor_api.get(f"{CONVERSATIONS_URL}{conversation.id}/").status_code == 404
    assert advisor_api.get(f"{CONVERSATIONS_URL}{conversation.id}/messages/").status_code == 404
    respuesta = advisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/messages/", {"content": "hola"}, format="json")
    assert respuesta.status_code == 404


def test_una_sin_asignar_la_toma_supervision_no_el_asesor(advisor_api, supervisor_api, company, supervisor):
    conversation = make_conversation(company, advisor=None, chatbot=True, phone="+573003333333")

    assert advisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/claim/", {}, format="json").status_code == 404

    # El supervisor no es asesor: tiene que decir a quién se la asigna.
    sin_destino = supervisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/claim/", {}, format="json")
    assert sin_destino.status_code == 404
    assert sin_destino.json()["error"]["code"] == "ADVISOR_NOT_ASSIGNABLE"


def test_una_conversacion_de_otra_empresa_responde_404(advisor_api, other_company):
    conversation = make_conversation(other_company, phone="+573009999999")

    assert advisor_api.get(f"{CONVERSATIONS_URL}{conversation.id}/").status_code == 404


def test_un_asesor_no_puede_apagar_el_chatbot_de_un_contacto_ajeno(advisor_api, company, other_advisor):
    conversation = make_conversation(company, advisor=other_advisor, chatbot=True)

    response = advisor_api.post(
        f"/api/v1/inbox/contacts/{conversation.contact_id}/chatbot/", {"enabled": False}, format="json"
    )

    assert response.status_code == 404
    conversation.contact.refresh_from_db()
    assert conversation.contact.chatbot_enabled is True


def test_el_filtro_por_asesor_no_amplia_el_alcance(advisor_api, company, advisor, other_advisor):
    make_conversation(company, advisor=advisor, phone="+573001111111")
    make_conversation(company, advisor=other_advisor, phone="+573002222222")

    response = advisor_api.get(CONVERSATIONS_URL, {"advisor": str(other_advisor.id)})

    assert response.json()["conversations"] == []


# -----------------------------------------------------------------------------
# Asignación automática
# -----------------------------------------------------------------------------

def test_un_entrante_nuevo_se_asigna_al_asesor_con_menos_conversaciones(company, advisor, other_advisor):
    # `advisor` arranca con una conversación abierta; el reparto debe evitarlo.
    make_conversation(company, advisor=advisor, phone="+573001111111")

    messaging.ingest_inbound_text(company=company, phone="+573005555555", text="Hola")

    conversation = Conversation.objects.get(contact__phone_number="+573005555555")
    assert conversation.advisor_id == other_advisor.id


def test_sin_asesores_elegibles_la_conversacion_queda_sin_asignar(company):
    messaging.ingest_inbound_text(company=company, phone="+573005555555", text="Hola")

    conversation = Conversation.objects.get(contact__phone_number="+573005555555")
    assert conversation.advisor_id is None
    # El entrante se guarda igual: una regla de reparto no puede perder un mensaje.
    assert Message.objects.filter(conversation=conversation).count() == 1


def test_un_asesor_que_no_acepta_asignaciones_automaticas_se_descarta(company, advisor):
    advisor.accepts_automatic_assignments = False
    advisor.save(update_fields=["accepts_automatic_assignments"])

    assert pick_advisor(company=company) is None


# -----------------------------------------------------------------------------
# Tomar y devolver la conversación
# -----------------------------------------------------------------------------

def test_tomar_conversacion_apaga_el_bot_y_asigna(advisor_api, company, advisor):
    # El flujo real: el reparto ya se la dio, el bot la está atendiendo, y el
    # asesor toma el relevo con un botón.
    conversation = make_conversation(company, advisor=advisor, chatbot=True)

    response = advisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/claim/", {}, format="json")

    assert response.status_code == 200
    conversation.refresh_from_db()
    conversation.contact.refresh_from_db()
    assert conversation.advisor_id == advisor.id
    assert conversation.assignment == Conversation.Assignment.ME
    assert conversation.contact.chatbot_enabled is False


def test_enviar_con_el_bot_encendido_responde_403_y_tras_tomarla_201(advisor_api, company, advisor, channel):
    conversation = make_conversation(company, advisor=advisor, chatbot=True)
    url = f"{CONVERSATIONS_URL}{conversation.id}/messages/"

    bloqueado = advisor_api.post(url, {"content": "hola"}, format="json")
    assert bloqueado.status_code == 403
    assert bloqueado.json()["error"]["code"] == "CHATBOT_ENABLED"

    advisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/claim/", {}, format="json")

    enviado = advisor_api.post(url, {"content": "hola"}, format="json")
    assert enviado.status_code == 201
    assert Message.objects.filter(conversation=conversation, sender_type=Message.Sender.AGENT).count() == 1


def test_devolver_al_bot_reactiva_el_chatbot(advisor_api, company, advisor):
    conversation = make_conversation(company, advisor=advisor, chatbot=False)

    response = advisor_api.post(f"{CONVERSATIONS_URL}{conversation.id}/release/", {}, format="json")

    assert response.status_code == 200
    conversation.refresh_from_db()
    conversation.contact.refresh_from_db()
    assert conversation.contact.chatbot_enabled is True
    assert conversation.assignment == Conversation.Assignment.BOT
    # El dueño se conserva: si no, desaparecería de su lista al devolverla.
    assert conversation.advisor_id == advisor.id


def test_un_asesor_no_puede_reasignar_a_otro(advisor_api, company, advisor, other_advisor):
    conversation = make_conversation(company, advisor=advisor)

    response = advisor_api.post(
        f"{CONVERSATIONS_URL}{conversation.id}/claim/", {"advisor_id": str(other_advisor.id)}, format="json"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ADVISOR_NOT_ASSIGNABLE"
    conversation.refresh_from_db()
    assert conversation.advisor_id == advisor.id


def test_administracion_reasigna_y_la_conversacion_cambia_de_dueno(api, company, advisor, other_advisor):
    conversation = make_conversation(company, advisor=advisor)

    response = api.post(
        f"{CONVERSATIONS_URL}{conversation.id}/claim/", {"advisor_id": str(other_advisor.id)}, format="json"
    )

    assert response.status_code == 200
    conversation.refresh_from_db()
    assert conversation.advisor_id == other_advisor.id


def test_no_se_puede_reasignar_a_un_asesor_de_otra_empresa(api, company, other_company, advisor):
    from apps.inbox.tests.conftest import make_advisor

    ajeno = make_advisor(other_company, email="ajeno@otra.co", code="ASE-100")
    conversation = make_conversation(company, advisor=advisor)

    response = api.post(
        f"{CONVERSATIONS_URL}{conversation.id}/claim/", {"advisor_id": str(ajeno.id)}, format="json"
    )

    assert response.status_code == 404
