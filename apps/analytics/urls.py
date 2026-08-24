from django.urls import path

from .views import (
    advisors_metrics,
    conversations_metrics,
    events_metrics,
    funnel,
    messages_heatmap,
    messages_metrics,
    overview,
)

urlpatterns = [
    path("overview/", overview, name="dashboard-overview"),
    path("messages/", messages_metrics, name="dashboard-messages"),
    path("messages/heatmap/", messages_heatmap, name="dashboard-messages-heatmap"),
    path("conversations/", conversations_metrics, name="dashboard-conversations"),
    path("events/", events_metrics, name="dashboard-events"),
    path("advisors/", advisors_metrics, name="dashboard-advisors"),
    path("funnel/", funnel, name="dashboard-funnel"),
]
