from rest_framework.routers import DefaultRouter

from .views import AdvisorAvailabilityViewSet, AdvisorSupervisionViewSet, AdvisorViewSet

router=DefaultRouter(); router.register("advisors",AdvisorViewSet,basename="advisor"); router.register("advisor-availabilities",AdvisorAvailabilityViewSet,basename="advisor-availability"); router.register("supervisions",AdvisorSupervisionViewSet,basename="supervision")
urlpatterns=router.urls
