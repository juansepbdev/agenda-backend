from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    status_code = 400

    def __init__(self, message, details=None):
        self.message, self.details = message, details or {}


class CompanyInactiveError(DomainError):
    code = "COMPANY_INACTIVE"
    status_code = 403


class CrossCompanyRelationError(DomainError):
    code = "CROSS_COMPANY_RELATION"


class InvalidEventTransitionError(DomainError):
    code = "INVALID_EVENT_TRANSITION"


class EventConflictError(DomainError):
    code = "EVENT_CONFLICT"


class AdvisorUnavailableError(DomainError):
    code = "ADVISOR_UNAVAILABLE"


class AdvisorDailyLimitExceededError(DomainError):
    code = "ADVISOR_DAILY_LIMIT"


class PermissionScopeError(DomainError):
    code = "PERMISSION_SCOPE"
    status_code = 403


class AvailabilityOverlapError(DomainError):
    code = "AVAILABILITY_OVERLAP"


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response(
            {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, status=exc.status_code
        )
    if isinstance(exc, DjangoValidationError):
        # Un UUID o una fecha inválidos en un query param llegan hasta aquí como
        # ValidationError de Django, que DRF no reconoce y convertiría en 500.
        return Response(
            {
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Alguno de los valores enviados es inválido.",
                    "details": {"errors": list(exc.messages)},
                }
            },
            status=400,
        )
    return exception_handler(exc, context)
