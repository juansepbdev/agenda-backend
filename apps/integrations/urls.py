from django.urls import path

from .views import chatbot_availability, chatbot_cancel_event, chatbot_event

urlpatterns = [
    path("chatbot/availability/", chatbot_availability, name="chatbot-availability"),
    path("chatbot/events/", chatbot_event, name="chatbot-event"),
    path("chatbot/events/<uuid:event_id>/cancel/", chatbot_cancel_event, name="chatbot-event-cancel"),
]
