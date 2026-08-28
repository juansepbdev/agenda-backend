from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.scheduling.selectors import get_supervisor_advisor_ids, get_user_company
from apps.users.permissions import IsCompanyAdmin

from .models import Advisor, AdvisorAvailability, AdvisorSupervision
from .serializers import (
    AdvisorAvailabilitySerializer,
    AdvisorSerializer,
    AdvisorSupervisionSerializer,
)

READ_ACTIONS = ("list", "retrieve", "availabilities")


def advisors_visible_to_user(user):
    """Asesores que el usuario puede *leer*, recortados por rol.

    Mismo criterio de alcance que `get_events_visible_to_user`: administración
    ve la empresa, supervisión su equipo (más el suyo si además es asesor), y un
    asesor sólo su propio perfil.

    El listado lo necesitan los tres roles: el frontend lo consulta en cada
    inicio de sesión para descubrir el `advisor_id` propio, sin el cual "mi
    agenda" y la comprobación de propiedad de un evento no funcionan.
    """
    # Orden explícito: sin él la paginación de DRF puede devolver la misma fila
    # en dos páginas distintas (UnorderedObjectListWarning).
    qs = Advisor.objects.filter(company=get_user_company(user)).select_related("user").order_by("code")
    if user.is_superuser or user.role == user.Role.ADMIN:
        return qs
    if user.role == user.Role.SUPERVISOR:
        ids = list(get_supervisor_advisor_ids(user=user))
        own = getattr(user, "advisor", None)
        if own:
            ids.append(own.id)
        return qs.filter(id__in=ids)
    if user.role == user.Role.ADVISOR:
        return qs.filter(user=user)
    return qs.none()


class ScopedReadAdminWriteMixin:
    """Lectura recortada por rol; escritura reservada a administración."""

    def get_permissions(self):
        if self.action in READ_ACTIONS:
            return [IsAuthenticated()]
        return [IsCompanyAdmin()]


class AdvisorViewSet(ScopedReadAdminWriteMixin, viewsets.ModelViewSet):
    serializer_class = AdvisorSerializer
    search_fields = ["code", "user__email", "user__first_name", "user__last_name"]
    ordering_fields = ["code", "assignment_priority", "created_at"]

    def get_queryset(self):
        if self.action in READ_ACTIONS:
            return advisors_visible_to_user(self.request.user)
        return (
            Advisor.objects.filter(company=get_user_company(self.request.user)).select_related("user").order_by("code")
        )

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        obj = self.get_object()
        obj.is_active = True
        obj.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="availability-status")
    def availability_status(self, request, pk=None):
        obj = self.get_object()
        obj.is_available = bool(request.data.get("is_available", True))
        obj.save(update_fields=["is_available", "updated_at"])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["get"])
    def availabilities(self, request, pk=None):
        blocks = self.get_object().availabilities.filter(company=get_user_company(request.user), is_active=True)
        return Response(AdvisorAvailabilitySerializer(blocks, many=True).data)


class AdvisorAvailabilityViewSet(ScopedReadAdminWriteMixin, viewsets.ModelViewSet):
    serializer_class = AdvisorAvailabilitySerializer
    ordering_fields = ["day_of_week", "start_time"]

    def get_queryset(self):
        qs = AdvisorAvailability.objects.filter(company=get_user_company(self.request.user))
        if self.action in READ_ACTIONS:
            # El listado es de lectura para todos, pero sólo de los asesores
            # que el usuario alcanza.
            qs = qs.filter(advisor__in=advisors_visible_to_user(self.request.user))
        # `?advisor=<id>` lo envía el frontend desde siempre; sin este filtro el
        # panel mostraba la disponibilidad de toda la empresa como si fuera la
        # del asesor seleccionado.
        advisor_id = self.request.query_params.get("advisor")
        if advisor_id:
            qs = qs.filter(advisor_id=advisor_id)
        return qs.order_by("day_of_week", "start_time")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])


class AdvisorSupervisionViewSet(viewsets.ModelViewSet):
    serializer_class = AdvisorSupervisionSerializer
    permission_classes = [IsCompanyAdmin]
    ordering_fields = ["valid_from", "created_at"]

    def get_queryset(self):
        return (
            AdvisorSupervision.objects.filter(company=get_user_company(self.request.user))
            .select_related("advisor", "supervisor_user")
            .order_by("-valid_from")
        )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(obj).data)
