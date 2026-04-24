from django.urls import path

from transcripts.views import (
    InternalTranscriptCompleteView,
    TranscriptDetailView,
    TranscriptRetryView,
    TranscriptSegmentListView,
)

app_name = "transcripts"

urlpatterns = [
    # Public endpoints — accessed by frontend
    path("meetings/<uuid:meeting_id>/transcript/",TranscriptDetailView.as_view(),name="transcript-detail",),
    path("meetings/<uuid:meeting_id>/transcript/segments/",TranscriptSegmentListView.as_view(),name="transcript-segments",),
    path("meetings/<uuid:meeting_id>/transcript/retry/",TranscriptRetryView.as_view(),name="transcript-retry",),

    # Internal endpoint — accessed by ai_worker only
    path("internal/transcript/complete/",InternalTranscriptCompleteView.as_view(),name="transcript-complete-internal",),
]
