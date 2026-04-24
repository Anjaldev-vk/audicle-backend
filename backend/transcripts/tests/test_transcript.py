import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch

from meetings.models import Meeting
from transcripts.models import Transcript, TranscriptSegment


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def org_owner(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(
        name="Transcript Org",
        slug="transcript-org",
    )
    return User.objects.create_user(
        email="owner@transcript.com",
        password="StrongPass123!",
        first_name="Transcript",
        last_name="Owner",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def other_org_user(db):
    from accounts.models import Organisation, User
    org = Organisation.objects.create(
        name="Other Org",
        slug="other-org",
    )
    return User.objects.create_user(
        email="other@org.com",
        password="StrongPass123!",
        first_name="Other",
        last_name="User",
        organisation=org,
        org_role="owner",
    )


@pytest.fixture
def meeting(db, org_owner):
    return Meeting.objects.create(
        title="Test Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=org_owner,
        organisation=org_owner.organisation,
        audio_s3_key="meetings/uuid/audio/test.mp3",
        status=Meeting.Status.PROCESSING,
    )


@pytest.fixture
def completed_transcript(db, meeting, org_owner):
    transcript = Transcript.objects.create(
        meeting=meeting,
        organisation=org_owner.organisation,
        created_by=org_owner,
        status=Transcript.Status.COMPLETED,
        language="en",
        raw_text="Hello everyone. Let us start the meeting.",
        duration_seconds=120.0,
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        text="Hello everyone.",
        start_seconds=0.0,
        end_seconds=2.5,
        confidence=0.95,
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        text="Let us start the meeting.",
        start_seconds=2.5,
        end_seconds=5.0,
        confidence=0.92,
    )
    return transcript


@pytest.fixture
def failed_transcript(db, meeting, org_owner):
    return Transcript.objects.create(
        meeting=meeting,
        organisation=org_owner.organisation,
        created_by=org_owner,
        status=Transcript.Status.FAILED,
        error_message="Whisper transcription failed.",
        retry_count=0,
    )


# ── Transcript model tests ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTranscriptModel:

    def test_word_count_auto_computed(self, completed_transcript):
        completed_transcript.refresh_from_db()
        assert completed_transcript.word_count == 7

    def test_is_completed_property(self, completed_transcript):
        assert completed_transcript.is_completed is True

    def test_can_retry_when_failed_and_under_limit(self, failed_transcript):
        assert failed_transcript.can_retry is True

    def test_cannot_retry_when_max_retries_reached(self, failed_transcript):
        failed_transcript.retry_count = 3
        failed_transcript.save()
        assert failed_transcript.can_retry is False

    def test_cannot_retry_when_completed(self, completed_transcript):
        assert completed_transcript.can_retry is False

    def test_segment_duration_property(self, completed_transcript):
        segment = completed_transcript.segments.first()
        assert segment.duration_seconds == 2.5


# ── GET transcript ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetTranscript:

    def test_owner_can_get_transcript(
        self, client, org_owner, completed_transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"
        assert data["word_count"] == 7
        assert len(data["segments"]) == 2

    def test_other_org_cannot_get_transcript(
        self, client, other_org_user, completed_transcript
    ):
        client.force_authenticate(user=other_org_user)
        response = client.get(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, client, completed_transcript):
        response = client.get(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 401

    def test_nonexistent_transcript_returns_404(self, client, org_owner, meeting):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse("transcripts:transcript-detail", args=[meeting.id])
        )
        assert response.status_code == 404


# ── GET segments ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetSegments:

    def test_returns_segments_in_order(
        self, client, org_owner, completed_transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.get(
            reverse(
                "transcripts:transcript-segments",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_segments"] == 2
        segments = data["segments"]
        assert segments[0]["start_seconds"] == 0.0
        assert segments[1]["start_seconds"] == 2.5


# ── DELETE transcript ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteTranscript:

    def test_owner_can_delete_transcript(
        self, client, org_owner, completed_transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.delete(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 200
        assert not Transcript.objects.filter(
            id=completed_transcript.id
        ).exists()


# ── Retry transcript ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRetryTranscript:

    @patch("transcripts.views.send_transcription_task")
    def test_retry_failed_transcript(
        self, mock_kafka, client, org_owner, failed_transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:transcript-retry",
                args=[failed_transcript.meeting.id],
            )
        )
        assert response.status_code == 200
        failed_transcript.refresh_from_db()
        assert failed_transcript.status == Transcript.Status.PENDING
        assert failed_transcript.retry_count == 1
        mock_kafka.assert_called_once()

    def test_cannot_retry_completed_transcript(
        self, client, org_owner, completed_transcript
    ):
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:transcript-retry",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_status"

    def test_cannot_retry_after_max_attempts(
        self, client, org_owner, failed_transcript
    ):
        failed_transcript.retry_count = 3
        failed_transcript.save()
        client.force_authenticate(user=org_owner)
        response = client.post(
            reverse(
                "transcripts:transcript-retry",
                args=[failed_transcript.meeting.id],
            )
        )
        assert response.status_code == 400
        assert response.json()["code"] == "max_retries_exceeded"


# ── Internal endpoint ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestInternalTranscriptComplete:

    def test_valid_secret_saves_transcript(self, client, meeting):
        from django.conf import settings
        response = client.post(
            reverse("transcripts:transcript-complete-internal"),
            {
                "meeting_id":       str(meeting.id),
                "status":           "completed",
                "language":         "en",
                "raw_text":         "Hello everyone.",
                "duration_seconds": 120.0,
                "segments": [
                    {
                        "text":          "Hello everyone.",
                        "start_seconds": 0.0,
                        "end_seconds":   2.5,
                        "confidence":    0.95,
                    }
                ],
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 200
        assert Transcript.objects.filter(meeting=meeting).exists()
        transcript = Transcript.objects.get(meeting=meeting)
        assert transcript.status == Transcript.Status.COMPLETED
        assert transcript.word_count == 2
        assert transcript.segments.count() == 1

    def test_invalid_secret_returns_403(self, client, meeting):
        response = client.post(
            reverse("transcripts:transcript-complete-internal"),
            {"meeting_id": str(meeting.id), "status": "completed"},
            format="json",
            HTTP_X_INTERNAL_SECRET="wrong-secret",
        )
        assert response.status_code == 403

    def test_missing_secret_returns_403(self, client, meeting):
        response = client.post(
            reverse("transcripts:transcript-complete-internal"),
            {"meeting_id": str(meeting.id), "status": "completed"},
            format="json",
        )
        assert response.status_code == 403

    def test_failed_status_saves_error_message(self, client, meeting):
        from django.conf import settings
        response = client.post(
            reverse("transcripts:transcript-complete-internal"),
            {
                "meeting_id":    str(meeting.id),
                "status":        "failed",
                "error_message": "Whisper ran out of memory.",
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        assert response.status_code == 200
        transcript = Transcript.objects.get(meeting=meeting)
        assert transcript.status == Transcript.Status.FAILED
        assert transcript.error_message == "Whisper ran out of memory."

    def test_meeting_status_updated_to_completed(self, client, meeting):
        from django.conf import settings
        client.post(
            reverse("transcripts:transcript-complete-internal"),
            {
                "meeting_id": str(meeting.id),
                "status":     "completed",
                "raw_text":   "Test transcript.",
                "segments":   [],
            },
            format="json",
            HTTP_X_INTERNAL_SECRET=settings.INTERNAL_API_SECRET,
        )
        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.COMPLETED
