from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    EventViewSet,
    SchedulingConfigurationViewSet,
    calendar_day,
    calendar_month,
    calendar_week,
)

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("scheduling-configurations", SchedulingConfigurationViewSet, basename="scheduling-configuration")
urlpatterns = router.urls + [
    path("calendar/day/", calendar_day),
    path("calendar/week/", calendar_week),
    path("calendar/month/", calendar_month),
]
