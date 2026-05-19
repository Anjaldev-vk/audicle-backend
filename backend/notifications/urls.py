from django.urls import path
from .views import (
    NotificationListView,
    NotificationReadView,
    NotificationReadAllView,
    NotificationDeleteView,
    InternalNotificationPushView,
)

urlpatterns = [
    path('', NotificationListView.as_view()),
    path('read-all/', NotificationReadAllView.as_view()),
    path('<str:notification_id>/read/', NotificationReadView.as_view()),
    path('<str:notification_id>/', NotificationDeleteView.as_view()),
]

# Internal endpoints (called by Lambda)
internal_urlpatterns = [
    path('notifications/push/', InternalNotificationPushView.as_view()),
]
