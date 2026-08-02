from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .serializers import CompanySerializer


class CurrentCompanyView(generics.RetrieveUpdateAPIView):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user.company
