from django.urls import re_path

from core.consumers import SeatMapConsumer

websocket_urlpatterns = [
    re_path(r"^ws/events/(?P<event_id>\d+)/seats/$", SeatMapConsumer.as_asgi()),
]
