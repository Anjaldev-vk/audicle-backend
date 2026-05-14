from django.urls import path

from transcripts.views import (
    InternalSummaryCompleteView,
    InternalTranscriptCompleteView,
)



urlpatterns = [
    # ---------------Internal endpoints (ai_worker only) ----------------
    path("internal/transcript/complete/", InternalTranscriptCompleteView.as_view(), name="transcript-complete-internal"),
    path("internal/summary/complete/", InternalSummaryCompleteView.as_view(), name="summary-complete-internal"),
]
