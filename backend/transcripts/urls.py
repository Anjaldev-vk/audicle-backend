from django.urls import path

from transcripts.views import (
    InternalSummaryCompleteView,
    InternalTranscriptCompleteView,
    SummaryDetailView,
    SummaryRetryView,
    SummaryTranslateView,
    TranscriptDetailView,
    TranscriptRetryView,
    TranscriptSegmentListView,
    TranscriptSegmentEditView,
)

app_name = "transcripts"

urlpatterns = [
    # ---------------Transcript endpoints -----------------------------
    path("meetings/<uuid:meeting_id>/transcript/", TranscriptDetailView.as_view(), name="transcript-detail"),
    path("meetings/<uuid:meeting_id>/transcript/segments/", TranscriptSegmentListView.as_view(), name="transcript-segments"),
    path("meetings/<uuid:meeting_id>/transcript/segments/<uuid:segment_id>/", TranscriptSegmentEditView.as_view(), name="transcript-segment-edit"),
    path("meetings/<uuid:meeting_id>/transcript/retry/", TranscriptRetryView.as_view(), name="transcript-retry"),

    # ---------------Summary endpoints -------------------------------
    path("meetings/<uuid:meeting_id>/summary/", SummaryDetailView.as_view(), name="summary-detail"),
    path("meetings/<uuid:meeting_id>/summary/retry/", SummaryRetryView.as_view(), name="summary-retry"),
    path("meetings/<uuid:meeting_id>/summary/translate/", SummaryTranslateView.as_view(), name="summary-translate"),

    # ---------------Internal endpoints (ai_worker only) ----------------
    path("internal/transcript/complete/", InternalTranscriptCompleteView.as_view(), name="transcript-complete-internal"),
    path("internal/summary/complete/", InternalSummaryCompleteView.as_view(), name="summary-complete-internal"),
]
