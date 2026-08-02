from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.users.permissions import IsCompanyAdmin

from .models import Advisor, AdvisorAvailability, AdvisorSupervision
from .serializers import (
    AdvisorAvailabilitySerializer,
    AdvisorSerializer,
    AdvisorSupervisionSerializer,
)


class AdvisorViewSet(viewsets.ModelViewSet):
    serializer_class=AdvisorSerializer; permission_classes=[IsCompanyAdmin]
    def get_queryset(self): return Advisor.objects.filter(company=self.request.user.company).select_related("user")
    @action(detail=True,methods=["post"])
    def activate(self,request,pk=None): obj=self.get_object(); obj.is_active=True; obj.save(update_fields=["is_active"]); return Response(self.get_serializer(obj).data)
    @action(detail=True,methods=["post"])
    def deactivate(self,request,pk=None): obj=self.get_object(); obj.is_active=False; obj.save(update_fields=["is_active"]); return Response(self.get_serializer(obj).data)
    @action(detail=True,methods=["post"],url_path="availability-status")
    def availability_status(self,request,pk=None): obj=self.get_object(); obj.is_available=bool(request.data.get("is_available",True)); obj.save(update_fields=["is_available"]); return Response(self.get_serializer(obj).data)
    @action(detail=True,methods=["get"])
    def availabilities(self,request,pk=None): return Response(AdvisorAvailabilitySerializer(self.get_object().availabilities.filter(company=request.user.company,is_active=True),many=True).data)
class AdvisorAvailabilityViewSet(viewsets.ModelViewSet):
    serializer_class=AdvisorAvailabilitySerializer; permission_classes=[IsCompanyAdmin]
    def get_queryset(self): return AdvisorAvailability.objects.filter(company=self.request.user.company)
    def perform_destroy(self,instance): instance.is_active=False; instance.save(update_fields=["is_active"])
class AdvisorSupervisionViewSet(viewsets.ModelViewSet):
    serializer_class=AdvisorSupervisionSerializer; permission_classes=[IsCompanyAdmin]
    def get_queryset(self): return AdvisorSupervision.objects.filter(company=self.request.user.company).select_related("advisor","supervisor_user")
    @action(detail=True,methods=["post"])
    def deactivate(self,request,pk=None): obj=self.get_object(); obj.is_active=False; obj.save(update_fields=["is_active"]); return Response(self.get_serializer(obj).data)
