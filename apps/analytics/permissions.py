"""Alcance de lectura del dashboard.

Dos niveles, no uno:

* La **agenda** la ve todo el mundo, pero recortada: `get_events_visible_to_user`
  ya decide si un usuario ve la empresa entera, su equipo o solo lo suyo.
* El **inbox** no tiene esa noción de propiedad — una conversación no pertenece
  a un asesor —, así que sus métricas son necesariamente de toda la empresa y
  se reservan a quien tiene alcance de empresa: administración y supervisión.
"""

from rest_framework.permissions import BasePermission

INBOX_METRICS_ROLES = ("ADMIN", "SUPERVISOR")


def can_view_inbox_metrics(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.role in INBOX_METRICS_ROLES)


class CanViewInboxMetrics(BasePermission):
    message = "Se requiere rol de administrador o supervisor para las métricas del inbox."

    def has_permission(self, request, view):
        return can_view_inbox_metrics(request.user)
