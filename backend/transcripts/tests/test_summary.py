import pytest
from django.urls import reverse
from unittest.mock import patch

from meetings.models import Meeting
from transcripts.models import MeetingSummary, Transcript


@pytest.fixture
def meeting(db, org_admin, organisation):
    return Meeting.objects.create(
        title="Summary Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=org_admin,
        organisation=organisation,
        audio_s3_key="meetings/uuid/audio/test.mp3",
        status=Meeting.Status.COMPLETED,
    )


@pytest.fixture
def transcript(db, meeting, org_admin, organisation):
    return Transcript.objects.create(
        meeting=meeting,
        organisation=organisation,
        created_by=org_admin,
        status=Transcript.Status.COMPLETED,
        raw_text="Hello everyone. Let us discuss the project timeline.",
    )


@pytest.fixture
def completed_summary(db, meeting, org_admin, organisation):
    return MeetingSummary.objects.create(
        meeting=meeting,
        organisation=organisation,
        created_by=org_admin,
        status=MeetingSummary.Status.COMPLETED,
        summary="The team discussed the project timeline and agreed on deadlines.",
        key_points=["Timeline discussed",
                    "Resources allocated", "Budget approved"],
        action_items=[
            {
                "task":        "Send Q2 report",
                "assigned_to": "John",
                "due_date":    "2026-05-01",
                "priority":    "high",
            }
        ],
        decisions=["Deadline extended to May", "Budget increased by 10%"],
        next_steps=["Schedule follow-up", "Send meeting notes"],
    )


@pytest.fixture
def failed_summary(db, meeting, org_admin, organisation):
    return MeetingSummary.objects.create(
        meeting=meeting,
        organisation=organisation,
        created_by=org_admin,
        status=MeetingSummary.Status.FAILED,
        error_message="Gemini API error.",
        retry_count=0,
    )


# ── Model tests ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMeetingSummaryModel:

    def test_can_retry_when_failed_and_under_limit(self, failed_summary):
        assert failed_summary.can_retry is True

    def test_cannot_retry_when_max_retries_reached(self, failed_summary):
        failed_summary.retry_count = 3
        failed_summary.save()
        assert failed_summary.can_retry is False

    def test_cannot_retry_when_completed(self, completed_summary):
        assert completed_summary.can_retry is False

    def test_str_representation(self, completed_summary):
        assert "Summary Meeting" in str(completed_summary)
        assert "Completed" in str(completed_summary)


# ── GET summary ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetSummary:

    def test_owner_can_get_summary(
        self, org_admin_client, completed_summary
    ):
        response = org_admin_client.get(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"
        assert len(data["action_items"]) == 1
        assert len(data["key_points"]) == 3
        assert len(data["decisions"]) == 2
        assert data["can_retry"] is False

    def test_no_summary_returns_404(self, org_admin_client, meeting):
        response = org_admin_client.get(
            reverse("transcripts:summary-detail", args=[meeting.id])
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_other_org_cannot_get_summary(
        self, auth_client, completed_summary
    ):
        # auth_client is individual user
        response = auth_client.get(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client, completed_summary):
        response = api_client.get(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 401


# ── DELETE summary ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteSummary:

    def test_owner_can_delete_summary(
        self, org_admin_client, completed_summary
    ):
        response = org_admin_client.delete(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 200
        assert not MeetingSummary.objects.filter(
            id=completed_summary.id
        ).exists()


# ── Retry summary ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRetrySummary:

    @patch("transcripts.views.send_summarization_task")
    def test_retry_failed_summary_increments_count(
        self, mock_kafka, org_admin_client, failed_summary, transcript
    ):
        response = org_admin_client.post(
            reverse(
                "transcripts:summary-retry",
                args=[failed_summary.meeting.id],
            )
        )
        assert response.status_code == 200
        failed_summary.refresh_from_db()
        assert failed_summary.retry_count == 1
        assert failed_summary.status == MeetingSummary.Status.PENDING
        assert failed_summary.error_message is None

    @patch("transcripts.views.send_summarization_task")
    def test_retry_fires_kafka_message(
        self, mock_kafka, org_admin_client, failed_summary, transcript
    ):
        org_admin_client.post(
            reverse(
                "transcripts:summary-retry",
                args=[failed_summary.meeting.id],
            )
        )
        mock_kafka.assert_called_once_with(
            meeting_id=str(failed_summary.meeting.id),
            transcript_text=transcript.raw_text,
        )


# ── Translate summary ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTranslateSummary:

    @patch("transcripts.views.SummaryTranslateView._translate")
    def test_translate_returns_translated_text(
        self, mock_translate, org_admin_client, completed_summary
    ):
        mock_translate.return_value = "യോഗം പദ്ധതി സമയക്രമം ചർച്ച ചെയ്തു."
        response = org_admin_client.post(
            reverse(
                "transcripts:summary-translate",
                args=[completed_summary.meeting.id],
            ),
            {"target_language": "Malayalam"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["target_language"] == "Malayalam"
        assert data["original_language"] == "English"
        assert "translated_summary" in data

    @patch("transcripts.views.SummaryTranslateView._translate")
    def test_translate_normalizes_language_name(
        self, mock_translate, org_admin_client, completed_summary
    ):
        mock_translate.return_value = "नमस्ते"
        response = org_admin_client.post(
            reverse(
                "transcripts:summary-translate",
                args=[completed_summary.meeting.id],
            ),
            {"target_language": "hindi"},   # lowercase input
            format="json",
        )
        assert response.status_code == 200
        # Should be normalized to "Hindi"
        assert response.json()["data"]["target_language"] == "Hindi"


# ── Internal summary endpoint ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestInternalSummaryComplete:

    def test_valid_secret_saves_completed_summary(self, api_client, meeting):
        from django.conf import settings
        response = api_client.post(
            reverse("transcripts:summary-complete-internal"),
            {
                "meeting_id":   str(meeting.id),
                "status":       "completed",
                "summary":      "The team discussed the project.",
                "key_points":   ["Point 1", "Point 2"],
                "action_items": [
                    {
                        "task":        "Follow up with client",
                        "assigned_to": "John",
                        "due_date":    "2026-05-01",
                        "priority":    "high",
                    }
                ],
                "decisions":  ["Use React for frontend"],
                "next_steps": ["Schedule next meeting"],
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 200
        assert MeetingSummary.objects.filter(meeting=meeting).exists()
