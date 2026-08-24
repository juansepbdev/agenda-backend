"""Métricas del CRM conversacional.

Criterio de fechado, constante en todo el módulo: un mensaje cuenta en el
periodo en que fue **creado** (`Message.created_at`, que la ingesta reescribe
con la hora real de WhatsApp), y una conversación o contacto cuentan como
"nuevos" por su `created_at`. Lo que mide el estado actual —no leídos, chatbot
encendido— se calcula sobre la foto de hoy y se etiqueta como tal.

Las agregaciones se hacen con `values(...).annotate(Count(...))`: una consulta
por métrica, y el agrupado por cubeta temporal se resuelve en Python
(`periods.bucket_start`) para que la clave sea idéntica en SQLite y Postgres.
"""

from collections import Counter, defaultdict

from django.db.models import Count, Q

from apps.inbox.models import Contact, Conversation, Message

from ..periods import Period, bucket_start, iterate_buckets
from ..stats import distribution, rate, top_n

# Techo de mensajes que se recorren en Python para los tiempos de respuesta.
# Por encima, la métrica se marca como truncada en vez de degradar la petición.
RESPONSE_SCAN_LIMIT = 50_000

TOP_CONTACTS_LIMIT = 10


def _messages(company, period: Period):
    return Message.objects.filter(
        company=company, created_at__gte=period.start, created_at__lt=period.end
    )


# -----------------------------------------------------------------------------
# Volumen
# -----------------------------------------------------------------------------

def message_totals(*, company, period: Period) -> dict:
    """Recuento de mensajes por dirección y por remitente, en una sola consulta."""
    row = _messages(company, period).aggregate(
        total=Count("id"),
        inbound=Count("id", filter=Q(message_type=Message.Type.INBOUND)),
        outbound=Count("id", filter=Q(message_type=Message.Type.OUTBOUND)),
        from_contact=Count("id", filter=Q(sender_type=Message.Sender.CONTACT)),
        from_bot=Count("id", filter=Q(sender_type=Message.Sender.BOT)),
        from_agent=Count("id", filter=Q(sender_type=Message.Sender.AGENT)),
        failed=Count("id", filter=Q(status=Message.Status.FAILED)),
    )

    return {
        **row,
        "bot_share_pct": rate(row["from_bot"], row["outbound"]),
        "failure_rate_pct": rate(row["failed"], row["outbound"]),
    }


def message_timeseries(*, company, period: Period) -> list[dict]:
    """Serie temporal de mensajes, con las cubetas vacías rellenas a cero."""
    rows = (
        _messages(company, period)
        .values("created_at", "message_type", "sender_type")
        .order_by()
    )

    series = defaultdict(lambda: {"total": 0, "inbound": 0, "outbound": 0, "bot": 0, "agent": 0, "contact": 0})
    for row in rows.iterator(chunk_size=2000):
        key = bucket_start(row["created_at"], period.granularity, period.tz)
        entry = series[key]
        entry["total"] += 1
        entry["inbound" if row["message_type"] == Message.Type.INBOUND else "outbound"] += 1
        entry[row["sender_type"]] += 1

    empty = {"total": 0, "inbound": 0, "outbound": 0, "bot": 0, "agent": 0, "contact": 0}
    return [
        {"bucket": bucket.isoformat(), **series.get(bucket, empty)}
        for bucket in iterate_buckets(period)
    ]


def message_heatmap(*, company, period: Period) -> dict:
    """Mensajes por día de la semana × hora, en hora local de la empresa.

    Responde a "cuándo escribe la gente", que es la métrica con la que se
    decide el horario de los asesores. Lunes = 0.
    """
    grid = [[0] * 24 for _ in range(7)]
    rows = _messages(company, period).values_list("created_at", flat=True).order_by()

    peak = {"weekday": None, "hour": None, "count": 0}
    for created_at in rows.iterator(chunk_size=5000):
        local = created_at.astimezone(period.tz)
        grid[local.weekday()][local.hour] += 1

    for weekday, hours in enumerate(grid):
        for hour, count in enumerate(hours):
            if count > peak["count"]:
                peak = {"weekday": weekday, "hour": hour, "count": count}

    return {"matrix": grid, "peak": peak, "weekday_labels": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]}


# -----------------------------------------------------------------------------
# Conversaciones y contactos
# -----------------------------------------------------------------------------

def conversation_totals(*, company, period: Period) -> dict:
    """Mezcla dos lecturas distintas y las nombra como tales.

    `new` y `active` son del periodo; `by_status`, `by_assignment` y
    `unread_total` describen el estado **actual** del inbox, porque una
    conversación no guarda el historial de sus cambios de estado.
    """
    conversations = Conversation.objects.filter(company=company)

    period_row = conversations.aggregate(
        new=Count("id", filter=Q(created_at__gte=period.start, created_at__lt=period.end)),
        active=Count(
            "id", filter=Q(last_activity_at__gte=period.start, last_activity_at__lt=period.end)
        ),
    )
    now_row = conversations.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=Conversation.Status.OPEN)),
        pending=Count("id", filter=Q(status=Conversation.Status.PENDING)),
        resolved=Count("id", filter=Q(status=Conversation.Status.RESOLVED)),
        assigned_bot=Count("id", filter=Q(assignment=Conversation.Assignment.BOT)),
        assigned_agent=Count("id", filter=Q(assignment=Conversation.Assignment.ME)),
        unassigned=Count("id", filter=Q(assignment=Conversation.Assignment.UNASSIGNED)),
        unread_conversations=Count("id", filter=Q(unread_count__gt=0)),
    )

    return {
        "new": period_row["new"],
        "active": period_row["active"],
        "current": {
            "total": now_row["total"],
            "by_status": {
                "open": now_row["open"],
                "pending": now_row["pending"],
                "resolved": now_row["resolved"],
            },
            "by_assignment": {
                "bot": now_row["assigned_bot"],
                "agent": now_row["assigned_agent"],
                "unassigned": now_row["unassigned"],
            },
            "resolution_rate_pct": rate(now_row["resolved"], now_row["total"]),
            "unread_conversations": now_row["unread_conversations"],
        },
    }


def contact_totals(*, company, period: Period) -> dict:
    contacts = Contact.objects.filter(company=company)
    row = contacts.aggregate(
        total=Count("id"),
        new=Count("id", filter=Q(created_at__gte=period.start, created_at__lt=period.end)),
        chatbot_on=Count("id", filter=Q(chatbot_enabled=True)),
        linked_to_client=Count("id", filter=Q(client__isnull=False)),
    )
    active = (
        _messages(company, period)
        .filter(contact__isnull=False)
        .values("contact_id")
        .distinct()
        .count()
    )

    return {
        "total": row["total"],
        "new": row["new"],
        "active": active,
        "chatbot_enabled": row["chatbot_on"],
        "chatbot_disabled": row["total"] - row["chatbot_on"],
        "linked_to_client": row["linked_to_client"],
        "linked_rate_pct": rate(row["linked_to_client"], row["total"]),
    }


def top_contacts(*, company, period: Period, limit: int = TOP_CONTACTS_LIMIT) -> list[dict]:
    """Contactos con más mensajes en el periodo, con su desglose por dirección."""
    rows = (
        _messages(company, period)
        .filter(contact__isnull=False)
        .values("contact_id", "contact__name", "contact__phone_number", "contact__chatbot_enabled")
        .annotate(
            messages=Count("id"),
            inbound=Count("id", filter=Q(message_type=Message.Type.INBOUND)),
            outbound=Count("id", filter=Q(message_type=Message.Type.OUTBOUND)),
        )
        .order_by("-messages")[:limit]
    )

    return [
        {
            "contact_id": str(row["contact_id"]),
            "name": row["contact__name"],
            "phone_number": row["contact__phone_number"],
            "chatbot_enabled": row["contact__chatbot_enabled"],
            "messages": row["messages"],
            "inbound": row["inbound"],
            "outbound": row["outbound"],
        }
        for row in rows
    ]


# -----------------------------------------------------------------------------
# Tiempos de respuesta
# -----------------------------------------------------------------------------

def response_times(*, company, period: Period) -> dict:
    """Segundos entre un entrante sin contestar y la primera salida que lo contesta.

    Se recorre el hilo en orden: al llegar un entrante se abre un pendiente (si
    no había uno ya abierto, para no contar dos veces al cliente que manda tres
    mensajes seguidos) y se cierra con la primera salida posterior. Distingue
    bot de asesor porque son dos realidades opuestas: el bot responde en
    segundos y el asesor en minutos u horas, y promediarlos juntos oculta las
    dos cifras.

    Los pendientes que quedan sin respuesta al acabar el periodo se cuentan
    aparte (`unanswered`) en vez de descartarse: son, justamente, el problema.
    """
    rows = (
        _messages(company, period)
        .values_list("conversation_id", "message_type", "sender_type", "created_at")
        .order_by("conversation_id", "id")
    )

    pending: dict = {}
    by_sender: dict = {Message.Sender.BOT: [], Message.Sender.AGENT: []}
    every: list[float] = []
    first_response: list[float] = []
    seen_first: set = set()
    scanned = 0

    for conversation_id, message_type, sender_type, created_at in rows.iterator(chunk_size=5000):
        scanned += 1
        if scanned > RESPONSE_SCAN_LIMIT:
            break

        if message_type == Message.Type.INBOUND:
            pending.setdefault(conversation_id, created_at)
            continue

        opened_at = pending.pop(conversation_id, None)
        if opened_at is None:
            # Salida sin entrante previo: mensaje proactivo, no es una respuesta.
            continue

        seconds = (created_at - opened_at).total_seconds()
        every.append(seconds)
        if sender_type in by_sender:
            by_sender[sender_type].append(seconds)
        if conversation_id not in seen_first:
            seen_first.add(conversation_id)
            first_response.append(seconds)

    return {
        "unit": "seconds",
        "overall": distribution(every),
        "first_response": distribution(first_response),
        "by_sender": {
            "bot": distribution(by_sender[Message.Sender.BOT]),
            "agent": distribution(by_sender[Message.Sender.AGENT]),
        },
        "unanswered_conversations": len(pending),
        "truncated": scanned > RESPONSE_SCAN_LIMIT,
    }


# -----------------------------------------------------------------------------
# Automatización
# -----------------------------------------------------------------------------

def automation_summary(*, company, period: Period) -> dict:
    """Cuánto del trabajo lo hizo el bot y cuánto tuvo que hacer una persona.

    `handoff_conversations` son las conversaciones que el bot tocó y un asesor
    también: la señal de que la automatización se quedó corta.
    """
    rows = (
        _messages(company, period)
        .filter(message_type=Message.Type.OUTBOUND)
        .values("conversation_id")
        .annotate(
            bot=Count("id", filter=Q(sender_type=Message.Sender.BOT)),
            agent=Count("id", filter=Q(sender_type=Message.Sender.AGENT)),
        )
    )

    counter = Counter()
    for row in rows.iterator(chunk_size=2000):
        has_bot, has_agent = row["bot"] > 0, row["agent"] > 0
        counter["with_outbound"] += 1
        if has_bot and has_agent:
            counter["handoff"] += 1
        elif has_bot:
            counter["bot_only"] += 1
        elif has_agent:
            counter["agent_only"] += 1

    handled = counter["with_outbound"]
    return {
        "conversations_with_reply": handled,
        "bot_only": counter["bot_only"],
        "agent_only": counter["agent_only"],
        "handoff_conversations": counter["handoff"],
        "full_automation_rate_pct": rate(counter["bot_only"], handled),
        "handoff_rate_pct": rate(counter["handoff"], handled),
    }


def status_breakdown(*, company, period: Period) -> list[dict]:
    """Reparto de mensajes por estado de entrega, ordenado por volumen."""
    rows = _messages(company, period).values("status").annotate(total=Count("id"))
    counter = {row["status"]: row["total"] for row in rows}
    grand_total = sum(counter.values())
    return [
        {"status": status, "total": total, "share_pct": rate(total, grand_total)}
        for status, total in top_n(counter, len(counter))
    ]
