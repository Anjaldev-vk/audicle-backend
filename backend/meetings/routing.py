from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r"^ws/v1/meetings/(?P<meeting_id>[0-9a-f-]+)/$",
        consumers.MeetingConsumer.as_asgi(),
    ),
]
