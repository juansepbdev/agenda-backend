from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ClientViewSet
from .views_follow_ups import follow_up_cron, follow_up_decide, follow_up_list, follow_up_send

router = DefaultRouter()
router.register("clients", ClientViewSet, basename="client")

urlpatterns = [
    *router.urls,
    path("follow-ups/", follow_up_list, name="follow-up-list"),
    path("follow-ups/decide/", follow_up_decide, name="follow-up-decide"),
    path("follow-ups/send/", follow_up_send, name="follow-up-send"),
    # La barra final es obligatoria: el cron de Vercel no sigue redirecciones,
    # así que sin ella `APPEND_SLASH` devolvería 301 y no se enviaría nada.
    path("cron/follow-ups/", follow_up_cron, name="follow-up-cron"),
]
