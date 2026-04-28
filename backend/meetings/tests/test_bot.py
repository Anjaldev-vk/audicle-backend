"""
Phase 9 — Bot Scheduler Tests
Tests for:
  - BotDispatchView  (POST /api/v1/meetings/<id>/bot/dispatch/)
  - BotStatusView    (POST /internal/bot/status/)
  - auto_dispatch_bots_task (Celery Beat task)
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from meetings.models import Meeting


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Bot Test Org", slug="bot-test-org")


@pytest.fixture
def user(db, org):
    return User.objects.create_user(
        email="bot@test.com",
        password="pass1234",
        first_name="Bot",
        last_name="Tester",
        organisation=org,
        org_role="owner",
        is_verified=True,
    )


@pytest.fixture
def other_user(db, org):
    return User.objects.create_user(
        email="other@test.com",
        password="pass1234",
        first_name="Other",
        last_name="User",
        organisation=org,
        org_role="member",
        is_verified=True,
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def zoom_meeting(db, user, org):
    return Meeting.objects.create(
        title       = "Zoom Bot Test",
        platform    = Meeting.Platform.ZOOM,
        status      = Meeting.Status.SCHEDULED,
        meeting_url = "https://zoom.us/j/123456789",
        created_by  = user,
        organisation = org,
        scheduled_at = timezone.now() + timedelta(minutes=2),
    )


@pytest.fixture
def upload_meeting(db, user, org):
    return Meeting.objects.create(
        title        = "Upload Meeting",
        platform     = Meeting.Platform.UPLOAD,
        status       = Meeting.Status.SCHEDULED,
        created_by   = user,
        organisation = org,
    )


@pytest.fixture
def internal_secret(settings):
    settings.INTERNAL_API_SECRET = "test-secret-xyz"
    return "test-secret-xyz"


def internal_client(secret):
    """Returns an APIClient with the X-Internal-Secret header set."""
    client = APIClient()
    client.credentials(HTTP_X_INTERNAL_SECRET=secret)
    return client


# ─── BotDispatchView Tests ────────────────────────────────────────────────────

class TestBotDispatchView:

    def _url(self, meeting_id):
        return f"/api/v1/meetings/{meeting_id}/bot/dispatch/"

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_dispatch_success(self, mock_send, auth_client, zoom_meeting):
        """Bot dispatch sends to bot_tasks topic and sets status to bot_joining."""
        url = self._url(zoom_meeting.id)
        resp = auth_client.post(url)

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["success"] is True

        # Verify Kafka was called with correct args
        mock_send.assert_called_once_with(
            meeting_id   = str(zoom_meeting.id),
            meeting_url  = zoom_meeting.meeting_url,
            platform     = zoom_meeting.platform,
            duration_cap = 3600,
        )

        # Meeting status must update
        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.BOT_JOINING

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_dispatch_rejects_upload_platform(self, mock_send, auth_client, upload_meeting):
        """Bot cannot be dispatched for manual upload meetings."""
        url = self._url(upload_meeting.id)
        resp = auth_client.post(url)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["code"] == "invalid_platform"
        mock_send.assert_not_called()

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_dispatch_rejects_non_scheduled_meeting(self, mock_send, auth_client, zoom_meeting):
        """Bot cannot be dispatched for meetings not in scheduled/failed state."""
        zoom_meeting.status = Meeting.Status.RECORDING
        zoom_meeting.save()

        url = self._url(zoom_meeting.id)
        resp = auth_client.post(url)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["code"] == "invalid_status"
        mock_send.assert_not_called()

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_dispatch_allowed_on_failed_meeting(self, mock_send, auth_client, zoom_meeting):
        """Failed meetings can be re-dispatched."""
        zoom_meeting.status = Meeting.Status.FAILED
        zoom_meeting.save()

        url = self._url(zoom_meeting.id)
        resp = auth_client.post(url)

        assert resp.status_code == status.HTTP_200_OK
        mock_send.assert_called_once()

    def test_dispatch_requires_authentication(self, zoom_meeting):
        """Unauthenticated requests are rejected."""
        client = APIClient()
        url = self._url(zoom_meeting.id)
        resp = client.post(url)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_dispatch_meeting_not_found(self, auth_client):
        """Returns 404 for non-existent meeting."""
        url = self._url(uuid.uuid4())
        resp = auth_client.post(url)
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @patch("utils.kafka_producer.send_bot_task", side_effect=Exception("Kafka down"))
    def test_dispatch_kafka_failure_returns_503(self, mock_send, auth_client, zoom_meeting):
        """Kafka failure returns 503 and does NOT update meeting status."""
        url = self._url(zoom_meeting.id)
        resp = auth_client.post(url)

        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert resp.data["code"] == "dispatch_failed"

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.SCHEDULED


# ─── BotStatusView Tests ──────────────────────────────────────────────────────

class TestBotStatusView:

    URL = "/internal/bot/status/"

    def test_missing_secret_rejected(self, db, zoom_meeting):
        """Missing X-Internal-Secret header returns 403."""
        client = APIClient()
        resp = client.post(self.URL, {
            "meeting_id": str(zoom_meeting.id),
            "status": "bot_joining",
        }, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_wrong_secret_rejected(self, db, zoom_meeting, internal_secret):
        """Wrong X-Internal-Secret value returns 403."""
        client = APIClient()
        client.credentials(HTTP_X_INTERNAL_SECRET="wrong-secret")
        resp = client.post(self.URL, {
            "meeting_id": str(zoom_meeting.id),
            "status": "bot_joining",
        }, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_bot_joining_updates_status(self, db, zoom_meeting, internal_secret):
        """bot_joining status updates meeting.status."""
        client = internal_client(internal_secret)
        resp = client.post(self.URL, {
            "meeting_id": str(zoom_meeting.id),
            "status": "bot_joining",
        }, format="json")
        assert resp.status_code == status.HTTP_200_OK

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.BOT_JOINING

    def test_recording_updates_status_and_started_at(self, db, zoom_meeting, internal_secret):
        """recording status sets meeting.status=recording and meeting.started_at."""
        client = internal_client(internal_secret)
        resp = client.post(self.URL, {
            "meeting_id": str(zoom_meeting.id),
            "status": "recording",
        }, format="json")
        assert resp.status_code == status.HTTP_200_OK

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.RECORDING
        assert zoom_meeting.started_at is not None

    @patch("utils.kafka_producer.send_transcription_task", return_value=True)
    def test_processing_stores_s3_key_and_triggers_transcription(
        self, mock_transcription, db, zoom_meeting, internal_secret
    ):
        """processing status stores audio_s3_key and fires transcription pipeline."""
        client = internal_client(internal_secret)
        s3_key = "meetings/abc123/audio_xyz.mp3"

        resp = client.post(self.URL, {
            "meeting_id":  str(zoom_meeting.id),
            "status":      "processing",
            "audio_s3_key": s3_key,
        }, format="json")
        assert resp.status_code == status.HTTP_200_OK

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.PROCESSING
        assert zoom_meeting.audio_s3_key == s3_key
        assert zoom_meeting.ended_at is not None

        mock_transcription.assert_called_once_with(
            meeting_id = str(zoom_meeting.id),
            file_path  = s3_key,
            user_id    = str(zoom_meeting.created_by_id),
        )

    def test_failed_status_updates_meeting(self, db, zoom_meeting, internal_secret):
        """failed status sets meeting.status=failed."""
        client = internal_client(internal_secret)
        resp = client.post(self.URL, {
            "meeting_id":    str(zoom_meeting.id),
            "status":        "failed",
            "error_message": "Playwright crash",
        }, format="json")
        assert resp.status_code == status.HTTP_200_OK

        zoom_meeting.refresh_from_db()
        assert zoom_meeting.status == Meeting.Status.FAILED

    def test_invalid_meeting_id_returns_404(self, db, internal_secret):
        """Non-existent meeting_id returns 404."""
        client = internal_client(internal_secret)
        resp = client.post(self.URL, {
            "meeting_id": str(uuid.uuid4()),
            "status": "bot_joining",
        }, format="json")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_invalid_status_returns_400(self, zoom_meeting, internal_secret):
        """Invalid status value is rejected by serializer."""
        client = internal_client(internal_secret)
        resp = client.post(self.URL, {
            "meeting_id": str(zoom_meeting.id),
            "status": "totally_invalid",
        }, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ─── auto_dispatch_bots_task Tests ────────────────────────────────────────────

class TestAutoDispatchBotsTask:

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_dispatches_meeting_in_window(self, mock_send, db, user, org):
        """Task dispatches meetings scheduled within the ±5-minute window."""
        from meetings.tasks import auto_dispatch_bots_task

        meeting = Meeting.objects.create(
            title        = "Auto Dispatch Test",
            platform     = Meeting.Platform.ZOOM,
            status       = Meeting.Status.SCHEDULED,
            meeting_url  = "https://zoom.us/j/999",
            created_by   = user,
            organisation = org,
            scheduled_at = timezone.now() + timedelta(minutes=2),
        )

        result = auto_dispatch_bots_task()

        assert result["dispatched"] == 1
        mock_send.assert_called_once()

        meeting.refresh_from_db()
        assert meeting.status == Meeting.Status.BOT_JOINING

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_skips_upload_meetings(self, mock_send, db, user, org):
        """Task does not dispatch bots for upload-platform meetings."""
        from meetings.tasks import auto_dispatch_bots_task

        Meeting.objects.create(
            title        = "Upload Skip",
            platform     = Meeting.Platform.UPLOAD,
            status       = Meeting.Status.SCHEDULED,
            created_by   = user,
            organisation = org,
            scheduled_at = timezone.now() + timedelta(minutes=1),
        )

        result = auto_dispatch_bots_task()
        assert result["dispatched"] == 0
        mock_send.assert_not_called()

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_skips_archived_meetings(self, mock_send, db, user, org):
        """Task does not dispatch bots for archived meetings."""
        from meetings.tasks import auto_dispatch_bots_task

        Meeting.objects.create(
            title        = "Archived Meeting",
            platform     = Meeting.Platform.ZOOM,
            status       = Meeting.Status.SCHEDULED,
            meeting_url  = "https://zoom.us/j/999",
            created_by   = user,
            organisation = org,
            scheduled_at = timezone.now() + timedelta(minutes=1),
            is_archived  = True,
        )

        result = auto_dispatch_bots_task()
        assert result["dispatched"] == 0
        mock_send.assert_not_called()

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_skips_meetings_outside_window(self, mock_send, db, user, org):
        """Task does not dispatch meetings scheduled far in the future."""
        from meetings.tasks import auto_dispatch_bots_task

        Meeting.objects.create(
            title        = "Future Meeting",
            platform     = Meeting.Platform.ZOOM,
            status       = Meeting.Status.SCHEDULED,
            meeting_url  = "https://zoom.us/j/999",
            created_by   = user,
            organisation = org,
            scheduled_at = timezone.now() + timedelta(hours=3),
        )

        result = auto_dispatch_bots_task()
        assert result["dispatched"] == 0
        mock_send.assert_not_called()

    @patch("utils.kafka_producer.send_bot_task", return_value=True)
    def test_skips_meetings_without_url(self, mock_send, db, user, org):
        """Task does not dispatch meetings that have no meeting_url."""
        from meetings.tasks import auto_dispatch_bots_task

        Meeting.objects.create(
            title        = "No URL Meeting",
            platform     = Meeting.Platform.ZOOM,
            status       = Meeting.Status.SCHEDULED,
            meeting_url  = None,
            created_by   = user,
            organisation = org,
            scheduled_at = timezone.now() + timedelta(minutes=1),
        )

        result = auto_dispatch_bots_task()
        assert result["dispatched"] == 0
        mock_send.assert_not_called()
