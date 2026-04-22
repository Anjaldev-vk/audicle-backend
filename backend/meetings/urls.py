from django.urls import path

from meetings.views import (
    BotDispatchView,
    MeetingDetailView,
    MeetingListCreateView,
    MeetingParticipantDeleteView,
    MeetingParticipantListCreateView,
)

app_name = "meetings"

urlpatterns = [
    #---------------- Meeting CRUD ----------------
    path("",MeetingListCreateView.as_view(),name="meeting-list-create"),
    path("<uuid:meeting_id>/",MeetingDetailView.as_view(),name="meeting-detail"),

    #----------------- Bot -------------------------
    path("<uuid:meeting_id>/bot/dispatch/",BotDispatchView.as_view(),name="bot-dispatch"),

    #------------------ Participants ----------------
    path("<uuid:meeting_id>/participants/",MeetingParticipantListCreateView.as_view(), name="participant-list-create"),
    path("<uuid:meeting_id>/participants/<uuid:participant_id>/", MeetingParticipantDeleteView.as_view(), name="participant-delete"),
]
