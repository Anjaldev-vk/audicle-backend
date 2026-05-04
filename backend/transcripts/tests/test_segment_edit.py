import pytest
from django.urls import reverse
from rest_framework import status


@pytest.mark.django_db
class TestTranscriptSegmentEditView:

    def _url(self, meeting_id, segment_id):
        return f"/api/v1/meetings/{meeting_id}/transcript/segments/{segment_id}/"

    # ── Helpers ──────────────────────────────────────────────────────────

    def _make_segment(self, transcript, text="Hello world", start=0.0, end=2.0):
        from transcripts.models import TranscriptSegment
        return TranscriptSegment.objects.create(
            transcript=transcript,
            text=text,
            start_seconds=start,
            end_seconds=end,
            confidence=0.95,
        )

    def _make_transcript(self, meeting, user, org):
        from transcripts.models import Transcript
        return Transcript.objects.create(
            meeting=meeting,
            organisation=org,
            created_by=user,
            status=Transcript.Status.COMPLETED,
            raw_text="Hello world",
        )

    # ── Auth ─────────────────────────────────────────────────────────────

    def test_unauthenticated_returns_401(self, api_client, meeting):
        url = self._url(meeting.id, "00000000-0000-0000-0000-000000000000")
        response = api_client.patch(url, {"text": "hi"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # ── Happy path ───────────────────────────────────────────────────────

    def test_edit_text_sets_is_edited(self, auth_client, meeting, user, organisation):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(url, {"text": "Updated text"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["data"]["text"] == "Updated text"
        assert data["data"]["is_edited"] is True

        segment.refresh_from_db()
        assert segment.text == "Updated text"
        assert segment.is_edited is True

    def test_edit_speaker_name(self, auth_client, meeting, user, organisation):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(
            url, {"speaker_name": "Alice"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["data"]["speaker_name"] == "Alice"
        assert data["data"]["is_edited"] is True

        segment.refresh_from_db()
        assert segment.speaker_name == "Alice"

    def test_edit_text_and_speaker_name_together(
        self, auth_client, meeting, user, organisation
    ):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript, text="Old text")

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(
            url,
            {"text": "New text", "speaker_name": "Bob"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["data"]["text"] == "New text"
        assert data["data"]["speaker_name"] == "Bob"
        assert data["data"]["is_edited"] is True

    def test_read_only_fields_are_ignored(
        self, auth_client, meeting, user, organisation
    ):
        """start_seconds, end_seconds, confidence must not be writable."""
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript, start=1.0, end=3.0)

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(
            url,
            {
                "text": "Fine",
                "start_seconds": 999.0,
                "end_seconds": 999.0,
                "confidence": 0.01,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        segment.refresh_from_db()
        assert segment.start_seconds == 1.0
        assert segment.end_seconds == 3.0
        assert segment.confidence == 0.95

    # ── Validation ───────────────────────────────────────────────────────

    def test_empty_text_returns_400(self, auth_client, meeting, user, organisation):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(url, {"text": "   "}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["success"] is False

    def test_blank_text_returns_400(self, auth_client, meeting, user, organisation):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        url = self._url(meeting.id, segment.id)
        response = auth_client.patch(url, {"text": ""}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # ── Isolation / 404 ──────────────────────────────────────────────────

    def test_wrong_meeting_returns_404(
        self, auth_client, meeting, user, organisation
    ):
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        fake_meeting_id = "00000000-0000-0000-0000-000000000001"
        url = self._url(fake_meeting_id, segment.id)
        response = auth_client.patch(url, {"text": "hi"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_wrong_segment_returns_404(
        self, auth_client, meeting, user, organisation
    ):
        self._make_transcript(meeting, user, organisation)

        fake_segment_id = "00000000-0000-0000-0000-000000000002"
        url = self._url(meeting.id, fake_segment_id)
        response = auth_client.patch(url, {"text": "hi"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_other_user_cannot_edit_segment(
        self,
        auth_client,
        meeting,
        user,
        organisation,
        create_user,
        api_client,
    ):
        """Segment from another user's meeting must return 404."""
        transcript = self._make_transcript(meeting, user, organisation)
        segment = self._make_segment(transcript)

        # Second user with no access to this meeting
        other_user = create_user(email="other@example.com")
        api_client.force_authenticate(user=other_user)

        url = self._url(meeting.id, segment.id)
        response = api_client.patch(url, {"text": "hacked"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
