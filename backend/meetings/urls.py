from django.urls import path, include

from meetings.upload_views import ConfirmUploadView, GetDownloadURLView, RequestUploadURLView
from meetings.views import (
    BotDispatchView,
    MeetingDetailView,
    MeetingListCreateView,
    MeetingParticipantDeleteView,
    MeetingParticipantListCreateView,
    MeetingTemplateListCreateView,
    MeetingTemplateDeleteView,
    recall_webhook,
)
from transcripts.views import (
    SummaryDetailView,
    SummaryRetryView,
    SummaryTranslateView,
    TranscriptDetailView,
    TranscriptRetryView,
    TranscriptSegmentListView,
    TranscriptSegmentEditView,
)
from action_items.views import MeetingActionItemListCreateView

app_name = "meetings"

urlpatterns = [
    #------------------ Templates ----------------
    path("templates/", MeetingTemplateListCreateView.as_view(), name="template-list-create"),
    path("templates/<uuid:template_id>/", MeetingTemplateDeleteView.as_view(), name="template-delete"),

    #------------------ Action Items ----------------
    path("<uuid:meeting_id>/action-items/", MeetingActionItemListCreateView.as_view(), name="meeting-action-item-list-create"),

    #------------------ Participants ----------------
    path("<uuid:meeting_id>/participants/", MeetingParticipantListCreateView.as_view(), name="participant-list-create"),
    path("<uuid:meeting_id>/participants/<uuid:participant_id>/", MeetingParticipantDeleteView.as_view(), name="participant-delete"),

    #------------------ Bot -------------------------
    path("<uuid:meeting_id>/bot/dispatch/", BotDispatchView.as_view(), name="bot-dispatch"),

    #---------------- Meeting CRUD ----------------
    path("", MeetingListCreateView.as_view(), name="meeting-list-create"),
    path("<uuid:meeting_id>/", MeetingDetailView.as_view(), name="meeting-detail"),

    #------------------ Upload ----------------
    path("<uuid:meeting_id>/upload/request-url/", RequestUploadURLView.as_view(), name="upload-request-url"),
    path("<uuid:meeting_id>/upload/confirm/", ConfirmUploadView.as_view(), name="upload-confirm"),
    path("<uuid:meeting_id>/upload/download-url/", GetDownloadURLView.as_view(), name="upload-download-url"),

    #------------------ Recall.ai Webhook ---------
    path("webhooks/recall/", recall_webhook, name="recall-webhook"),

    #------------------ Transcript ----------------
    path("<uuid:meeting_id>/transcript/", TranscriptDetailView.as_view(), name="transcript-detail"),
    path("<uuid:meeting_id>/transcript/segments/", TranscriptSegmentListView.as_view(), name="transcript-segments"),
    path("<uuid:meeting_id>/transcript/segments/<uuid:segment_id>/", TranscriptSegmentEditView.as_view(), name="transcript-segment-edit"),
    path("<uuid:meeting_id>/transcript/retry/", TranscriptRetryView.as_view(), name="transcript-retry"),

    #------------------ Summary ----------------
    path("<uuid:meeting_id>/summary/", SummaryDetailView.as_view(), name="summary-detail"),
    path("<uuid:meeting_id>/summary/retry/", SummaryRetryView.as_view(), name="summary-retry"),
    path("<uuid:meeting_id>/summary/translate/", SummaryTranslateView.as_view(), name="summary-translate"),
]
