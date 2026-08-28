from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.scheduling.models import SchedulingConfiguration

from .models import User
from .permissions import IsCompanyAdmin
from .serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsCompanyAdmin]
    search_fields = ["email", "first_name", "last_name"]
    ordering_fields = ["email", "first_name", "created_at"]

    def get_permissions(self):
        if self.action == "my_permissions":
            return [IsAuthenticated()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        return User.objects.filter(company=self.request.user.company).order_by("email")

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.updated_by = request.user
        user.save(update_fields=["is_active", "updated_by"])
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        user.is_active = False
        user.updated_by = request.user
        user.save(update_fields=["is_active", "updated_by"])
        return Response(self.get_serializer(user).data)

    @action(detail=False, methods=["get"], url_path="me/permissions")
    def my_permissions(self, request):
        """Returns effective UI capabilities for the authenticated tenant user."""
        user = request.user
        configuration = (
            SchedulingConfiguration.objects.filter(
                company=user.company,
                is_default=True,
                is_active=True,
            ).first()
            if user.company_id
            else None
        )
        is_global_admin = user.is_superuser
        is_admin = is_global_admin or user.role == User.Role.ADMIN
        is_supervisor = user.role == User.Role.SUPERVISOR
        is_advisor = user.role == User.Role.ADVISOR
        permissions = {
            "manage_users": is_admin,
            "manage_advisors": is_admin,
            "manage_supervisions": is_admin,
            "manage_clients": is_admin or is_supervisor or is_advisor,
            "manage_scheduling_configuration": is_admin,
            "view_company_indicators": is_admin,
            "view_supervisor_indicators": is_admin or is_supervisor,
            "view_own_indicators": is_admin or is_supervisor or is_advisor,
            "view_all_company_events": is_admin,
            "view_supervised_advisor_events": is_admin or is_supervisor,
            "view_own_events": is_admin or is_supervisor or is_advisor,
            "create_events": is_admin
            or (is_supervisor and bool(configuration and configuration.allow_supervisor_create_events))
            or (is_advisor and bool(configuration and configuration.allow_advisor_create_events)),
            "reassign_events": is_admin
            or (is_supervisor and bool(configuration and configuration.allow_supervisor_reassign_events)),
            "edit_advisor_availability": is_admin
            or (is_supervisor and bool(configuration and configuration.allow_supervisor_edit_availability))
            or (is_advisor and bool(configuration and configuration.allow_advisor_edit_availability)),
            "cancel_events": is_admin or is_supervisor or is_advisor,
            "complete_events": is_admin or is_supervisor or is_advisor,
        }
        return Response(
            {
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "full_name": user.get_full_name(),
                    "role": "GLOBAL_ADMIN" if is_global_admin else user.role,
                    "company_id": str(user.company_id) if user.company_id else None,
                },
                "permissions": permissions,
            }
        )
