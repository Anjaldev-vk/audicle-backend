import pytest
from django.urls import reverse
from unittest.mock import patch

from meetings.models import Meeting
from transcripts.models import Transcript, TranscriptSegment


@pytest.fixture
def meeting(db, org_admin, organisation):
    return Meeting.objects.create(
        title="Test Meeting",
        platform=Meeting.Platform.UPLOAD,
        created_by=org_admin,
        organisation=organisation,
        audio_s3_key="meetings/uuid/audio/test.mp3",
        status=Meeting.Status.PROCESSING,
    )


@pytest.fixture
def completed_transcript(db, meeting, org_admin, organisation):
    transcript = Transcript.objects.create(
        meeting=meeting,
        organisation=organisation,
        created_by=org_admin,
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
def failed_transcript(db, meeting, org_admin, organisation):
    return Transcript.objects.create(
        meeting=meeting,
        organisation=organisation,
        created_by=org_admin,
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
        self, org_admin_client, completed_transcript
    ):
        response = org_admin_client.get(
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
        self, api_client, individual_user, completed_transcript
    ):
        # We need a client for another org or individual to test 404
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(individual_user)
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
        
        response = api_client.get(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client, completed_transcript):
        response = api_client.get(
            reverse(
                "transcripts:transcript-detail",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 401


# ── GET segments ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetSegments:

    def test_returns_segments_in_order(
        self, org_admin_client, completed_transcript
    ):
        response = org_admin_client.get(
            reverse(
                "transcripts:transcript-segments",
                args=[completed_transcript.meeting.id],
            )
        )
        assert response.status_code == 200
        results = response.json()["data"].get("results", response.json()["data"])
        times = [s['start_seconds'] for s in results]
        assert times == sorted(times)


# ── DELETE transcript ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDeleteTranscript:

    def test_owner_can_delete_transcript(
        self, org_admin_client, completed_transcript
    ):
        response = org_admin_client.delete(
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
        self, mock_kafka, org_admin_client, failed_transcript
    ):
        response = org_admin_client.post(
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


# ── Internal endpoint ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestInternalTranscriptComplete:

    def test_valid_secret_saves_transcript(self, api_client, meeting):
        from django.conf import settings
        response = api_client.post(
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
