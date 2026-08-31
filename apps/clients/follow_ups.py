"""Enviar y registrar seguimientos de leads.

El envío automático corre a diario y Vercel admite explícitamente invocar un
cron dos veces, así que la idempotencia no puede depender de que nadie se
equivoque: se **reclama** la fila con un `UPDATE` condicional *antes* de enviar.
Si el `UPDATE` no toca ninguna fila, otro ya se llevó ese lead y no se envía.

El precio es que un fallo entre reclamar y enviar pierde ese mensaje hasta el
siguiente enfriamiento. Es el lado correcto del trato: un WhatsApp perdido se
nota menos que uno duplicado.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.inbox.models import WhatsAppChannel
from apps.inbox.services import messaging, ycloud

from .models import FollowUp
from .selectors import follow_ups_due, get_configuration

logger = logging.getLogger(__name__)

#: Tope por ejecución y empresa. `maxDuration` en Vercel es de 60 s y cada
#: llamada a YCloud puede tardar; lo que no sale hoy sale mañana, porque la
#: cola se deriva y no se pierde.
MAX_SENDS_PER_RUN = 50

MESSAGE_TEMPLATE = "Seguimiento automático enviado a {name}."


def record_decision(*, company, candidate=None, phone, status, actor=None, **fields) -> FollowUp:
    """Guarda la decisión vigente sobre un lead. Una fila por lead."""
    defaults = {
        "status": status,
        "updated_by": actor,
        **fields,
    }
    if candidate is not None:
        defaults.update(
            reason=candidate.reason,
            client=candidate.client,
            contact=candidate.contact,
            advisor=candidate.advisor,
        )

    follow_up, created = FollowUp.objects.update_or_create(
        company=company, normalized_phone=phone, defaults=defaults
    )
    if created and actor is not None:
        FollowUp.objects.filter(pk=follow_up.pk).update(created_by=actor)
    return follow_up


def send_follow_up(*, company, candidate, configuration=None, actor=None) -> FollowUp | None:
    """Envía el seguimiento de un lead. `None` si otro ya se lo llevó.

    Nunca lanza: un canal mal configurado o un fallo de YCloud dejan escrito el
    motivo en `message_status` y el lead sigue en la lista del asesor.
    """
    configuration = configuration or get_configuration(company)
    now = timezone.now()

    if not _claim(company=company, candidate=candidate, configuration=configuration, now=now, actor=actor):
        return None

    channel = WhatsAppChannel.objects.filter(company=company, is_active=True).first()
    template = configuration.follow_up_template

    if not template:
        return _finish(company, candidate.phone, "skipped:sin-plantilla")
    if channel is None:
        return _finish(company, candidate.phone, "skipped:sin-canal")

    result = ycloud.send_whatsapp_template(
        channel=channel,
        to=candidate.phone,
        template=template,
        language=configuration.follow_up_template_language,
        variables=(candidate.name,),
    )

    # El rastro va al hilo del inbox como anotación de sistema. `content_type`
    # es lo que impide que el mensaje le robe la conversación al asesor:
    # `store_message` reasigna según el remitente salvo en los de sistema.
    try:
        messaging.store_message(
            company=company,
            phone=candidate.phone,
            name=candidate.name,
            content=MESSAGE_TEMPLATE.format(name=candidate.name),
            sender_type="agent",
            content_type="system",
            status="sent" if result["ok"] else "failed",
            wa_message_id=result.get("wamid"),
            channel=channel,
            mark_unread=False,
        )
    except Exception:  # noqa: BLE001 - registrar el rastro nunca debe tumbar el envío
        logger.exception("No se pudo registrar el seguimiento de %s en el hilo", candidate.phone)

    return _finish(company, candidate.phone, "sent" if result["ok"] else f"failed:{result['error']}"[:64])


def dispatch_company(*, company, limit=MAX_SENDS_PER_RUN, dry_run=False) -> dict:
    """Envía los seguimientos vencidos de una empresa. Nunca lanza."""
    configuration = get_configuration(company)
    if not configuration.follow_up_enabled:
        return {"company": company.slug, "sent": 0, "pending": 0, "skipped": "deshabilitado"}

    actor = _dispatcher(company)
    if actor is None:
        return {"company": company.slug, "sent": 0, "pending": 0, "skipped": "sin-administrador"}

    candidates = follow_ups_due(user=actor)
    batch, remaining = candidates[:limit], max(0, len(candidates) - limit)

    if dry_run:
        return {"company": company.slug, "sent": 0, "pending": len(candidates), "dry_run": True}

    sent = skipped = 0
    for candidate in batch:
        try:
            follow_up = send_follow_up(company=company, candidate=candidate, configuration=configuration)
        except Exception:  # noqa: BLE001 - un lead no puede tumbar el resto del lote
            logger.exception("Fallo enviando el seguimiento de %s", candidate.phone)
            continue
        # Se cuenta lo que salió de verdad: un lead sin plantilla o sin canal se
        # marca y se queda, y decir que "se envió" sería mentir en el informe.
        if follow_up is not None and follow_up.message_status == "sent":
            sent += 1
        elif follow_up is not None:
            skipped += 1

    return {"company": company.slug, "sent": sent, "skipped_sends": skipped, "pending": remaining}


# -----------------------------------------------------------------------------
# Piezas internas
# -----------------------------------------------------------------------------

def _claim(*, company, candidate, configuration, now, actor) -> bool:
    """Reclama el lead para este envío. `False` si ya estaba reclamado."""
    cooldown_start = now - timedelta(days=configuration.follow_up_cooldown_days)

    with transaction.atomic():
        follow_up, created = FollowUp.objects.get_or_create(
            company=company,
            normalized_phone=candidate.phone,
            defaults={
                "status": FollowUp.Status.SENT,
                "reason": candidate.reason,
                "client": candidate.client,
                "contact": candidate.contact,
                "advisor": candidate.advisor,
                "sent_at": now,
                "created_by": actor,
            },
        )
        if created:
            return True

        # El `UPDATE` condicional es el candado: si devuelve 0 filas, otra
        # ejecución se adelantó dentro del enfriamiento.
        stale = Q(sent_at__isnull=True) | Q(sent_at__lte=cooldown_start)
        claimed = FollowUp.objects.filter(pk=follow_up.pk).filter(stale).update(
            status=FollowUp.Status.SENT,
            reason=candidate.reason,
            client=candidate.client,
            contact=candidate.contact,
            advisor=candidate.advisor,
            sent_at=now,
            updated_at=now,
        )
        return bool(claimed)


def _finish(company, phone, message_status) -> FollowUp:
    FollowUp.objects.filter(company=company, normalized_phone=phone).update(
        message_status=message_status, updated_at=timezone.now()
    )
    return FollowUp.objects.get(company=company, normalized_phone=phone)


def _dispatcher(company):
    """Usuario en cuyo nombre se deriva la cola: hace falta uno que lo vea todo."""
    from apps.users.models import User

    return (
        User.objects.filter(company=company, role=User.Role.ADMIN, is_active=True)
        .order_by("created_at")
        .first()
    )
