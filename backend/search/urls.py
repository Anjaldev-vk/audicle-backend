from django.urls import path
from .views import MeetingSearchView

urlpatterns = [
    path("search/", MeetingSearchView.as_view(), name="meeting-search"),
]
