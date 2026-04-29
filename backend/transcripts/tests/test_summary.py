import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch

from meetings.models import Meeting
from transcripts.models import MeetingSummary, Transcript


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def org_owner(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(
        name="Summary Org",
        slug="summary-org",
    )
    return User.objects.create_user(
        email="owner@summary.com",
        password="StrongPass123!",
        first_name="Summary",
        last_name="Owner",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def other_org_user(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(
        name="Other Org",
        slug="other-org-summary",
    )
    return User.objects.create_user(
        email="other@summary.com",
        password="StrongPass123!",
        first_name="Other",
        last_name="User",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def meeting(db, org_owner):
    return Meeting.objects.create(
        title="Summary Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=org_owner,
        organisation=org_owner.organisation,
        audio_s3_key="meetings/uuid/audio/test.mp3",
        status=Meeting.Status.COMPLETED,
    )


@pytest.fixture
def transcript(db, meeting, org_owner):
    return Transcript.objects.create(
        meeting=meeting,
        organisation=org_owner.organisation,
        created_by=org_owner,
        status=Transcript.Status.COMPLETED,
        raw_text="Hello everyone. Let us discuss the project timeline.",
    )


@pytest.fixture
def completed_summary(db, meeting, org_owner):
    return MeetingSummary.objects.create(
        meeting=meeting,
        organisation=org_owner.organisation,
        created_by=org_owner,
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
def failed_summary(db, meeting, org_owner):
    return MeetingSummary.objects.create(
        meeting=meeting,
        organisation=org_owner.organisation,
        created_by=org_owner,
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
        self, client, org_owner, completed_summary
    ):
        client.force_authenticate(user=org_owner)
        response = client.get(
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

    def test_response_has_standard_format(
        self, client, org_owner, completed_summary
    ):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        body = response.json()
        assert body["success"] is True
        assert "message" in body
        assert "data" in body

    def test_no_summary_returns_404(self, client, org_owner, meeting):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("transcripts:summary-detail", args=[meeting.id])
        )
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_other_org_cannot_get_summary(
        self, client, other_org_user, completed_summary
    ):
        client.force_authenticate(user=other_org_user)
        response = client.get(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, client, completed_summary):
        response = client.get(
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
        self, client, org_owner, completed_summary
    ):
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse(
                "transcripts:summary-detail",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 200
        assert not MeetingSummary.objects.filter(
            id=completed_summary.id
        ).exists()

    def test_delete_nonexistent_returns_404(
        self, client, org_owner, meeting
    ):
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse("transcripts:summary-detail", args=[meeting.id])
        )
        assert response.status_code == 404


# ── Retry summary ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRetrySummary:

    @patch("transcripts.views.send_summarization_task")
    def test_retry_failed_summary_increments_count(
        self, mock_kafka, client, org_owner, failed_summary, transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
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
        self, mock_kafka, client, org_owner, failed_summary, transcript
    ):
        client.force_authenticate(user=org_owner)
        client.post(
            reverse(
                "transcripts:summary-retry",
                args=[failed_summary.meeting.id],
            )
        )
        mock_kafka.assert_called_once_with(
            meeting_id=str(failed_summary.meeting.id),
            transcript_text=transcript.raw_text,
        )

    def test_cannot_retry_completed_summary(
        self, client, org_owner, completed_summary
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:summary-retry",
                args=[completed_summary.meeting.id],
            )
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_status"

    def test_cannot_retry_after_max_attempts(
        self, client, org_owner, failed_summary, transcript
    ):
        failed_summary.retry_count = 3
        failed_summary.save()
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:summary-retry",
                args=[failed_summary.meeting.id],
            )
        )
        assert response.status_code == 400
        assert response.json()["code"] == "max_retries_exceeded"

    def test_retry_response_has_standard_format(
        self, client, org_owner, failed_summary, transcript
    ):
        with patch("transcripts.views.send_summarization_task"):
            client.force_authenticate(user=org_owner)
            response = client.post(
                reverse(
                    "transcripts:summary-retry",
                    args=[failed_summary.meeting.id],
                )
            )
        body = response.json()
        assert body["success"] is True
        assert "data" in body
        assert "retry_count" in body["data"]


# ── Translate summary ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTranslateSummary:

    @patch("transcripts.views.SummaryTranslateView._translate")
    def test_translate_returns_translated_text(
        self, mock_translate, client, org_owner, completed_summary
    ):
        mock_translate.return_value = "യോഗം പദ്ധതി സമയക്രമം ചർച്ച ചെയ്തു."
        client.force_authenticate(user=org_owner)
        response = client.post(
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
        assert len(data["translated_summary"]) > 0

    @patch("transcripts.views.SummaryTranslateView._translate")
    def test_translate_normalizes_language_name(
        self, mock_translate, client, org_owner, completed_summary
    ):
        mock_translate.return_value = "नमस्ते"
        client.force_authenticate(user=org_owner)
        response = client.post(
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

    def test_translate_no_completed_summary_returns_404(
        self, client, org_owner, meeting
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:summary-translate",
                args=[meeting.id],
            ),
            {"target_language": "Hindi"},
            format="json",
        )
        assert response.status_code == 404

    def test_translate_missing_language_returns_400(
        self, client, org_owner, completed_summary
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:summary-translate",
                args=[completed_summary.meeting.id],
            ),
            {},   # missing target_language
            format="json",
        )
        assert response.status_code == 400

    @patch("transcripts.views.SummaryTranslateView._translate")
    def test_translation_failure_returns_503(
        self, mock_translate, client, org_owner, completed_summary
    ):
        mock_translate.return_value = None   # simulate AI failure
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:summary-translate",
                args=[completed_summary.meeting.id],
            ),
            {"target_language": "Klingon"},
            format="json",
        )
        assert response.status_code == 503
        assert response.json()["code"] == "translation_failed"

    def test_unauthenticated_returns_401(self, client, completed_summary):
        response = client.post(
            reverse(
                "transcripts:summary-translate",
                args=[completed_summary.meeting.id],
            ),
            {"target_language": "Hindi"},
            format="json",
        )
        assert response.status_code == 401


# ── Internal summary endpoint ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestInternalSummaryComplete:

    def test_valid_secret_saves_completed_summary(self, client, meeting):
        from django.conf import settings
        response = client.post(
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

        summary = MeetingSummary.objects.get(meeting=meeting)
        assert summary.status == MeetingSummary.Status.COMPLETED
        assert summary.summary == "The team discussed the project."
        assert len(summary.key_points) == 2
        assert len(summary.action_items) == 1
        assert len(summary.decisions) == 1
        assert len(summary.next_steps) == 1
        assert summary.error_message is None

    def test_response_has_correct_counts(self, client, meeting):
        from django.conf import settings
        response = client.post(
            reverse("transcripts:summary-complete-internal"),
            {
                "meeting_id":   str(meeting.id),
                "status":       "completed",
                "summary":      "Test summary.",
                "key_points":   ["P1", "P2", "P3"],
                "action_items": [{"task": "T1"}, {"task": "T2"}],
                "decisions":    [],
                "next_steps":   [],
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["action_item_count"] == 2
        assert data["key_point_count"] == 3

    def test_failed_status_saves_error_message(self, client, meeting):
        from django.conf import settings
        response = client.post(
            reverse("transcripts:summary-complete-internal"),
            {
                "meeting_id":    str(meeting.id),
                "status":        "failed",
                "error_message": "Gemini rate limit exceeded.",
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 200
        summary = MeetingSummary.objects.get(meeting=meeting)
        assert summary.status == MeetingSummary.Status.FAILED
        assert summary.error_message == "Gemini rate limit exceeded."

    def test_invalid_secret_returns_403(self, client, meeting):
        response = client.post(
            reverse("transcripts:summary-complete-internal"),
            {"meeting_id": str(meeting.id), "status": "completed"},
            format="json",
            HTTP_X_INTERNAL_SECRET="wrong-secret",
        )
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"

    def test_missing_secret_returns_403(self, client, meeting):
        response = client.post(
            reverse("transcripts:summary-complete-internal"),
            {"meeting_id": str(meeting.id), "status": "completed"},
            format="json",
        )
        assert response.status_code == 403

    def test_nonexistent_meeting_returns_404(self, client):
        import uuid
        from django.conf import settings
        response = client.post(
            reverse("transcripts:summary-complete-internal"),
            {
                "meeting_id": str(uuid.uuid4()),
                "status":     "completed",
                "summary":    "Test.",
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 404

    def test_duplicate_call_updates_existing_summary(self, client, meeting):
        """
        If summary already exists, calling internal endpoint again
        should update it not create a duplicate.
        """
        from django.conf import settings
        payload = {
            "meeting_id": str(meeting.id),
            "status":     "completed",
            "summary":    "First summary.",
            "key_points": ["Point 1"],
        }
        # First call
        client.post(
            reverse("transcripts:summary-complete-internal"),
            payload,
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        # Second call with updated data
        payload["summary"] = "Updated summary."
        client.post(
            reverse("transcripts:summary-complete-internal"),
            payload,
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        # Should still be only one summary
        assert MeetingSummary.objects.filter(meeting=meeting).count() == 1
        summary = MeetingSummary.objects.get(meeting=meeting)
        assert summary.summary == "Updated summary."
