from django.urls import path

from .views import CurrentCompanyView

urlpatterns = [path("current/", CurrentCompanyView.as_view(), name="company-current")]
