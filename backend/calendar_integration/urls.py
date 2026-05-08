from django.urls import path
from .views import (
    CalendarConnectView,
    CalendarCallbackView,
    CalendarDisconnectView,
    CalendarStatusView,
)

urlpatterns = [
    path(
        "calendar/connect/",
        CalendarConnectView.as_view(),
        name="calendar-connect",
    ),
    path(
        "calendar/callback/",
        CalendarCallbackView.as_view(),
        name="calendar-callback",
    ),
    path(
        "calendar/disconnect/",
        CalendarDisconnectView.as_view(),
        name="calendar-disconnect",
    ),
    path(
        "calendar/status/",
        CalendarStatusView.as_view(),
        name="calendar-status",
    ),
]
