from django.urls import path

from meetings.upload_views import ConfirmUploadView, GetDownloadURLView, RequestUploadURLView
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
    path("", MeetingListCreateView.as_view(), name="meeting-list-create"),
    path("<uuid:meeting_id>/", MeetingDetailView.as_view(), name="meeting-detail"),

    #----------------- Bot -------------------------
    path("<uuid:meeting_id>/bot/dispatch/", BotDispatchView.as_view(), name="bot-dispatch"),

    #------------------ Participants ----------------
    path("<uuid:meeting_id>/participants/", MeetingParticipantListCreateView.as_view(), name="participant-list-create"),
    path("<uuid:meeting_id>/participants/<uuid:participant_id>/", MeetingParticipantDeleteView.as_view(), name="participant-delete"),

    #------------------ Upload ----------------
    path("<uuid:meeting_id>/upload/request-url/", RequestUploadURLView.as_view(), name="upload-request-url"),
    path("<uuid:meeting_id>/upload/confirm/", ConfirmUploadView.as_view(), name="upload-confirm"),
    path("<uuid:meeting_id>/upload/download-url/", GetDownloadURLView.as_view(), name="upload-download-url"),
]
