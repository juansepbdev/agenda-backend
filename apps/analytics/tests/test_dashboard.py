"""Cobertura de los endpoints de `/api/v1/dashboard/`."""

from apps.scheduling.models import Event

BASE = "/api/v1/dashboard"


# -----------------------------------------------------------------------------
# Vista general
# -----------------------------------------------------------------------------

def test_overview_returns_both_blocks_for_an_admin(api, conversation_data, events):
    response = api.get(f"{BASE}/overview/?period=7d")

    assert response.status_code == 200
    body = response.json()
    assert body["period"]["granularity"] == "day"
    assert body["inbox"]["messages"]["total"] == 4
    assert body["agenda"]["total"] == 5


def test_overview_hides_inbox_from_an_advisor(advisor_api, conversation_data, events):
    response = advisor_api.get(f"{BASE}/overview/?period=7d")

    assert response.status_code == 200
    body = response.json()
    assert body["inbox"] is None
    # Solo sus tres eventos, no los cinco de la empresa.
    assert body["agenda"]["total"] == 3


def test_overview_includes_trend_against_the_previous_period(api, conversation_data):
    body = api.get(f"{BASE}/overview/?period=7d").json()
    trend = body["inbox"]["trend"]["messages_total"]

    assert trend["current"] == 4
    assert trend["previous"] == 0
    # Sin base de comparación no se inventa un porcentaje.
    assert trend["change_pct"] is None


def test_overview_rejects_an_unknown_period(api):
    response = api.get(f"{BASE}/overview/?period=siempre")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ANALYTICS_VALIDATION_ERROR"


# -----------------------------------------------------------------------------
# Mensajes
# -----------------------------------------------------------------------------

def test_messages_totals_split_by_direction_and_sender(api, conversation_data):
    body = api.get(f"{BASE}/messages/?period=7d").json()["totals"]

    assert body["total"] == 4
    assert body["inbound"] == 2
    assert body["outbound"] == 2
    assert body["from_bot"] == 1
    assert body["from_agent"] == 1
    assert body["bot_share_pct"] == 50.0


def test_messages_series_fills_empty_buckets(api, conversation_data):
    series = api.get(f"{BASE}/messages/?period=7d").json()["series"]

    assert len(series) == 7
    assert sum(point["total"] for point in series) == 4
    # Los días sin tráfico existen en la serie, con ceros.
    assert any(point["total"] == 0 for point in series)


def test_messages_series_honours_explicit_granularity(api, conversation_data):
    series = api.get(f"{BASE}/messages/?period=7d&granularity=week").json()["series"]

    assert len(series) <= 2


def test_message_status_breakdown_adds_up(api, conversation_data):
    rows = api.get(f"{BASE}/messages/?period=7d").json()["by_status"]

    assert sum(row["total"] for row in rows) == 4
    assert round(sum(row["share_pct"] for row in rows)) == 100


def test_heatmap_is_a_seven_by_twentyfour_grid(api, conversation_data):
    heatmap = api.get(f"{BASE}/messages/heatmap/?period=7d").json()["heatmap"]

    assert len(heatmap["matrix"]) == 7
    assert all(len(row) == 24 for row in heatmap["matrix"])
    assert sum(sum(row) for row in heatmap["matrix"]) == 4
    assert heatmap["peak"]["count"] >= 1


def test_advisor_cannot_read_inbox_metrics(advisor_api, conversation_data):
    assert advisor_api.get(f"{BASE}/messages/?period=7d").status_code == 403
    assert advisor_api.get(f"{BASE}/conversations/?period=7d").status_code == 403
    assert advisor_api.get(f"{BASE}/funnel/?period=7d").status_code == 403


# -----------------------------------------------------------------------------
# Conversaciones
# -----------------------------------------------------------------------------

def test_response_times_separate_bot_from_agent(api, conversation_data):
    times = api.get(f"{BASE}/conversations/?period=7d").json()["response_times"]

    assert times["by_sender"]["bot"]["samples"] == 1
    assert times["by_sender"]["bot"]["avg"] == 60.0
    assert times["by_sender"]["agent"]["samples"] == 1
    assert times["by_sender"]["agent"]["avg"] == 600.0
    # La primera respuesta de la conversación se contabiliza una sola vez.
    assert times["first_response"]["samples"] == 1


def test_unanswered_conversations_are_counted(api, conversation_data, unanswered_conversation):
    times = api.get(f"{BASE}/conversations/?period=7d").json()["response_times"]

    assert times["unanswered_conversations"] == 1


def test_automation_summary_detects_handoff(api, conversation_data):
    automation = api.get(f"{BASE}/conversations/?period=7d").json()["automation"]

    assert automation["conversations_with_reply"] == 1
    assert automation["handoff_conversations"] == 1
    assert automation["handoff_rate_pct"] == 100.0
    assert automation["full_automation_rate_pct"] == 0.0


def test_conversation_current_state_is_reported_apart(api, conversation_data):
    conversations = api.get(f"{BASE}/conversations/?period=7d").json()["conversations"]

    assert conversations["new"] == 1
    assert conversations["current"]["by_status"]["open"] == 1


def test_top_contacts_are_ranked_and_limited(api, conversation_data, unanswered_conversation):
    body = api.get(f"{BASE}/conversations/?period=7d&limit=1").json()

    assert len(body["top_contacts"]) == 1
    assert body["top_contacts"][0]["messages"] == 4


def test_contact_totals_count_the_active_ones(api, conversation_data, unanswered_conversation):
    contacts = api.get(f"{BASE}/conversations/?period=7d").json()["contacts"]

    assert contacts["total"] == 2
    assert contacts["active"] == 2


# -----------------------------------------------------------------------------
# Agenda
# -----------------------------------------------------------------------------

def test_event_rates_are_computed_over_closed_events(api, events):
    totals = api.get(f"{BASE}/events/?period=7d").json()["totals"]

    assert totals["total"] == 5
    assert totals["closed"] == 4  # 2 completados, 1 cancelado, 1 no-show
    assert totals["completion_rate_pct"] == 50.0
    assert totals["cancellation_rate_pct"] == 25.0
    assert totals["no_show_rate_pct"] == 25.0
    assert totals["avg_duration_minutes"] == 60.0


def test_events_can_be_dated_by_creation_instead_of_start(api, events):
    body = api.get(f"{BASE}/events/?period=7d&date_field=created_at").json()

    assert body["totals"]["date_field"] == "created_at"
    assert body["totals"]["total"] == 5


def test_unknown_date_field_is_rejected(api, events):
    response = api.get(f"{BASE}/events/?date_field=fecha_bonita")

    assert response.status_code == 400


def test_event_breakdowns_cover_type_and_source(api, events):
    breakdowns = api.get(f"{BASE}/events/?period=7d").json()["breakdowns"]

    sources = {row["source"]: row["total"] for row in breakdowns["by_source"]}
    assert sources[Event.Source.CHATBOT] == 1
    assert sources[Event.Source.MANUAL] == 4


def test_advisor_ranking_orders_by_completed(api, events):
    advisors = api.get(f"{BASE}/advisors/?period=7d").json()["advisors"]

    assert len(advisors) == 2
    assert advisors[0]["completed"] >= advisors[1]["completed"]
    assert advisors[0]["name"] in {"Carlos Pérez", "Ana Ruiz"}


def test_advisor_only_sees_their_own_row(advisor_api, events):
    advisors = advisor_api.get(f"{BASE}/advisors/?period=7d").json()["advisors"]

    assert len(advisors) == 1
    assert advisors[0]["code"] == "A-001"


# -----------------------------------------------------------------------------
# Embudo
# -----------------------------------------------------------------------------

def test_funnel_walks_from_contacts_to_completed_visits(api, conversation_data, events):
    funnel = api.get(f"{BASE}/funnel/?period=7d").json()["funnel"]

    steps = {step["key"]: step["value"] for step in funnel["steps"]}
    assert steps["contacts"] == 1
    assert steps["conversations"] == 1
    assert steps["clients"] == 1
    assert steps["events_scheduled"] == 1
    assert steps["events_completed"] == 1
    assert funnel["steps"][0]["conversion_from_previous_pct"] is None
    assert funnel["overall_conversion_pct"] == 100.0


# -----------------------------------------------------------------------------
# Aislamiento y acceso
# -----------------------------------------------------------------------------

def test_anonymous_access_is_refused(client):
    assert client.get(f"{BASE}/overview/").status_code in {401, 403}


def test_metrics_do_not_leak_across_companies(api, conversation_data, django_user_model):
    from apps.companies.models import Company
    from apps.inbox.models import Contact, Conversation

    other = Company.objects.create(name="Otra", slug="otra", status=Company.Status.ACTIVE)
    contact = Contact.objects.create(company=other, phone_number="+573007776655")
    Conversation.objects.create(company=other, contact=contact)

    body = api.get(f"{BASE}/conversations/?period=7d").json()

    assert body["contacts"]["total"] == 1
    assert body["conversations"]["current"]["total"] == 1
