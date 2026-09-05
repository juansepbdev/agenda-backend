"""Endpoints de seguimiento de leads.

Vistas funcionales y no un `ModelViewSet` a propósito: la cola es **derivada**,
así que `retrieve`, `update` y `destroy` sobre ella no significarían nada. Lo
único que se escribe es la decisión, y esa se dirige por la URL del lead.
"""

from django.conf import settings
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_current_timezone, is_naive, make_aware
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.companies.models import Company
from apps.scheduling.exceptions import DomainError
from apps.scheduling.selectors import get_user_company

from . import follow_ups as service
from .models import FollowUp
from .selectors import follow_ups_due


class FollowUpNotFoundError(DomainError):
    code = "FOLLOW_UP_NOT_FOUND"
    status_code = 404


class FollowUpValidationError(DomainError):
    code = "FOLLOW_UP_VALIDATION_ERROR"
    status_code = 400


def _parse_due_at(raw):
    """El cuerpo trae texto; el modelo quiere un datetime con zona."""
    if not raw:
        return None
    parsed = parse_datetime(raw) if isinstance(raw, str) else raw
    if parsed is None:
        raise FollowUpValidationError("`due_at` no es una fecha ISO-8601 válida.")
    return make_aware(parsed, get_current_timezone()) if is_naive(parsed) else parsed


def _serialize(candidate) -> dict:
    return {
        "phone": candidate.phone,
        "name": candidate.name,
        "reason": candidate.reason,
        "since": candidate.since.isoformat() if candidate.since else None,
        "client_id": str(candidate.client_id) if candidate.client_id else None,
        "contact_id": str(candidate.contact.id) if candidate.contact else None,
        "advisor": (
            {"id": str(candidate.advisor.id), "full_name": candidate.advisor.user.get_full_name()}
            if candidate.advisor
            else None
        ),
        "source_event_id": str(candidate.source_event.id) if candidate.source_event else None,
    }


@extend_schema(
    summary="Leads que toca contactar, recortados al alcance del usuario.",
    parameters=[
        OpenApiParameter("advisor", str, description="UUID del asesor dueño del lead."),
        OpenApiParameter("reason", str, description="CANCELLED | NO_SHOW | COMPLETED | INACTIVE"),
    ],
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
def follow_up_list(request):
    candidates = follow_ups_due(user=request.user)

    advisor = request.query_params.get("advisor")
    reason = request.query_params.get("reason")
    if advisor:
        candidates = [c for c in candidates if str(c.advisor_id) == advisor]
    if reason:
        candidates = [c for c in candidates if c.reason == reason]

    return Response({"results": [_serialize(c) for c in candidates], "count": len(candidates)})


@extend_schema(
    summary="Registra la decisión sobre un lead: gestionado, pospuesto o descartado.",
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
)
@api_view(["POST"])
def follow_up_decide(request):
    """El teléfono identifica al lead: es lo único que comparten cliente y contacto.

    Va en el cuerpo y no en la ruta porque el proxy del frontend valida cada
    segmento contra `^[A-Za-z0-9_-]+$` y el `+` de un teléfono lo haría fallar
    antes de llegar aquí.
    """
    company = get_user_company(request.user)
    data = request.data if isinstance(request.data, dict) else {}

    phone = (data.get("phone") or "").strip()
    if not phone:
        raise FollowUpValidationError("`phone` es obligatorio.")

    status = data.get("status")
    if status not in FollowUp.Status.values:
        raise FollowUpValidationError(f"`status` debe ser uno de {', '.join(FollowUp.Status.values)}.")

    due_at = _parse_due_at(data.get("due_at"))
    if status == FollowUp.Status.SNOOZED and due_at is None:
        raise FollowUpValidationError("Posponer un seguimiento exige `due_at`.")

    candidate = next((c for c in follow_ups_due(user=request.user) if c.phone == phone), None)
    if candidate is None and not FollowUp.objects.filter(company=company, normalized_phone=phone).exists():
        raise FollowUpNotFoundError("Ese lead no está en tu cola de seguimiento.")

    follow_up = service.record_decision(
        company=company,
        candidate=candidate,
        phone=phone,
        status=status,
        actor=request.user,
        due_at=due_at,
        notes=data.get("notes") or "",
    )
    return Response(
        {
            "phone": follow_up.normalized_phone,
            "status": follow_up.status,
            "due_at": follow_up.due_at.isoformat() if follow_up.due_at else None,
            "notes": follow_up.notes,
        }
    )


@extend_schema(summary="Envía ya el seguimiento de un lead concreto.", responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
def follow_up_send(request):
    company = get_user_company(request.user)
    phone = (request.data.get("phone") or "").strip() if isinstance(request.data, dict) else ""
    if not phone:
        raise FollowUpValidationError("`phone` es obligatorio.")
    candidate = next((c for c in follow_ups_due(user=request.user) if c.phone == phone), None)
    if candidate is None:
        raise FollowUpNotFoundError("Ese lead no está en tu cola de seguimiento.")

    follow_up = service.send_follow_up(company=company, candidate=candidate, actor=request.user)
    if follow_up is None:
        return Response({"phone": phone, "message_status": "skipped:ya-enviado"})
    return Response({"phone": phone, "message_status": follow_up.message_status})


@extend_schema(
    summary="Despacho automático de seguimientos. Solo para el cron.",
    responses=OpenApiTypes.OBJECT,
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def follow_up_cron(request):
    """Lo llama el cron de Vercel, que **siempre usa GET** y no sigue redirecciones.

    De ahí la barra final obligatoria en la ruta: sin ella `APPEND_SLASH`
    devolvería un 301 y el cron lo daría por terminado sin enviar nada.
    """
    expected = getattr(settings, "CRON_SECRET", "")
    provided = request.headers.get("Authorization", "")
    # Sin secreto configurado se deniega. Nunca "sin secreto, pasa".
    if not expected or not constant_time_compare(provided, f"Bearer {expected}"):
        # 401 explícito: sin clases de autenticación, DRF degradaría
        # `NotAuthenticated` a 403 y el cron no diría lo que pasa.
        return Response({"error": {"code": "CRON_UNAUTHORIZED", "message": "Credencial de cron inválida."}}, status=401)

    results = [
        service.dispatch_company(company=company)
        for company in Company.objects.filter(is_active=True).order_by("created_at")
        if company.can_operate
    ]
    return Response({"companies": results, "sent": sum(row["sent"] for row in results)})
