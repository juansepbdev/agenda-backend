"""Errores de la capa de analítica.

Heredan de `DomainError` para reutilizar el envoltorio
`{"error": {"code", "message", "details"}}` del resto de la API.
"""

from apps.scheduling.exceptions import DomainError


class AnalyticsValidationError(DomainError):
    code = "ANALYTICS_VALIDATION_ERROR"
    status_code = 400
