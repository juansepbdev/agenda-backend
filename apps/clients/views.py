from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    search_fields = ["first_name", "last_name", "phone", "email"]
    ordering_fields = ["first_name", "last_name", "created_at"]

    def get_queryset(self):
        return Client.objects.filter(company=self.request.user.company).order_by("first_name", "last_name")

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        client = self.get_object()
        client.is_active = False
        client.save(update_fields=["is_active"])
        return Response(self.get_serializer(client).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        from apps.scheduling.selectors import get_events_visible_to_user
        from apps.scheduling.serializers import EventListSerializer

        return Response(
            EventListSerializer(
                get_events_visible_to_user(user=request.user).filter(client=self.get_object()), many=True
            ).data
        )
