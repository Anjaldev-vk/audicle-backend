import pytest
from unittest.mock import MagicMock, patch
from django.utils import timezone
from datetime import timedelta
from rest_framework import status

from meetings.models import Meeting
from calendar_integration.models import GoogleCalendarToken


# ── Status endpoint ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCalendarStatusView:

    URL = "/api/v1/calendar/status/"

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(self.URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_connected_returns_false(self, org_admin_client):
        response = org_admin_client.get(self.URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["connected"] is False
        assert data["google_email"] is None
        assert data["last_synced_at"] is None

    def test_connected_returns_true(self, org_admin_client, calendar_token):
        response = org_admin_client.get(self.URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["connected"] is True
        assert data["google_email"] == "testuser@gmail.com"

    def test_inactive_token_returns_false(
        self, org_admin_client, calendar_token
    ):
        calendar_token.is_active = False
        calendar_token.save(update_fields=["is_active"])

        response = org_admin_client.get(self.URL)
        data = response.json()["data"]
        assert data["connected"] is False


# ── Connect endpoint ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCalendarConnectView:

    URL = "/api/v1/calendar/connect/"

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(self.URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_auth_url(self, org_admin_client):
        response = org_admin_client.get(self.URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert "auth_url" in data
        assert "accounts.google.com" in data["auth_url"]
        assert "calendar" in data["auth_url"]

    def test_auth_url_contains_state(self, org_admin_client):
        response = org_admin_client.get(self.URL)
        auth_url = response.json()["data"]["auth_url"]
        assert "state=" in auth_url

    def test_auth_url_requests_offline_access(self, org_admin_client):
        response = org_admin_client.get(self.URL)
        auth_url = response.json()["data"]["auth_url"]
        assert "offline" in auth_url


# ── Disconnect endpoint ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCalendarDisconnectView:

    URL = "/api/v1/calendar/disconnect/"

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(self.URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_connected_returns_404(self, org_admin_client):
        response = org_admin_client.post(self.URL)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "not_connected"

    @patch("calendar_integration.views.http_requests.post")
    def test_disconnect_deletes_token(
        self, mock_post, org_admin_client, calendar_token
    ):
        mock_post.return_value = MagicMock(status_code=200)

        response = org_admin_client.post(self.URL)
        assert response.status_code == status.HTTP_200_OK
        assert not GoogleCalendarToken.objects.filter(
            id=calendar_token.id
        ).exists()

    @patch("calendar_integration.views.http_requests.post")
    def test_disconnect_succeeds_even_if_revoke_fails(
        self, mock_post, org_admin_client, calendar_token
    ):
        """Token should be deleted locally even if Google revoke call fails."""
        mock_post.side_effect = Exception("Network error")

        response = org_admin_client.post(self.URL)
        assert response.status_code == status.HTTP_200_OK
        assert not GoogleCalendarToken.objects.filter(
            id=calendar_token.id
        ).exists()


# ── URL extraction ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestExtractMeetingUrl:

    def test_extracts_google_meet_hangout_link(self, mock_google_event):
        from calendar_integration.tasks import _extract_meeting_url
        url = _extract_meeting_url(mock_google_event)
        assert url == "https://meet.google.com/abc-defg-hij"

    def test_extracts_zoom_from_description(self, mock_zoom_event):
        from calendar_integration.tasks import _extract_meeting_url
        url = _extract_meeting_url(mock_zoom_event)
        assert "zoom.us" in url

    def test_returns_none_when_no_url(self, mock_event_no_url):
        from calendar_integration.tasks import _extract_meeting_url
        url = _extract_meeting_url(mock_event_no_url)
        assert url is None

    def test_extracts_from_location_field(self):
        from calendar_integration.tasks import _extract_meeting_url
        event = {
            "id": "evt1",
            "summary": "Test",
            "location": "https://zoom.us/j/987654321",
            "start": {"dateTime": timezone.now().isoformat()},
            "end": {"dateTime": timezone.now().isoformat()},
        }
        url = _extract_meeting_url(event)
        assert "zoom.us" in url

    def test_extracts_teams_url(self):
        from calendar_integration.tasks import _extract_meeting_url
        event = {
            "id": "evt2",
            "summary": "Teams meeting",
            "description": "Join: https://teams.microsoft.com/l/meetup-join/abc",
            "start": {"dateTime": timezone.now().isoformat()},
            "end": {"dateTime": timezone.now().isoformat()},
        }
        url = _extract_meeting_url(event)
        assert "teams.microsoft.com" in url


# ── Platform detection ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDetectPlatform:

    def test_detects_zoom(self):
        from calendar_integration.tasks import _detect_platform
        assert _detect_platform("https://zoom.us/j/123") == Meeting.Platform.ZOOM

    def test_detects_google_meet(self):
        from calendar_integration.tasks import _detect_platform
        assert _detect_platform("https://meet.google.com/abc") == Meeting.Platform.GOOGLE_MEET

    def test_detects_teams(self):
        from calendar_integration.tasks import _detect_platform
        assert _detect_platform("https://teams.microsoft.com/l/meet") == Meeting.Platform.TEAMS


# ── Event time parsing ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestParseEventTime:

    def test_parses_datetime_event(self, mock_google_event):
        from calendar_integration.tasks import _parse_event_time
        start, end = _parse_event_time(mock_google_event)
        assert start is not None
        assert end is not None
        assert end > start

    def test_skips_all_day_event(self, mock_all_day_event):
        from calendar_integration.tasks import _parse_event_time
        start, end = _parse_event_time(mock_all_day_event)
        assert start is None
        assert end is None


# ── Sync logic ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSyncCalendarForToken:

    def _mock_service(self, events):
        """Build a mock Google Calendar service returning given events."""
        mock_service = MagicMock()
        mock_service.events().list().execute.return_value = {
            "items": events
        }
        return mock_service

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_creates_meeting_from_event(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_google_event,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([mock_google_event])

        from calendar_integration.tasks import sync_calendar_for_token
        results = sync_calendar_for_token(calendar_token)

        assert results["created"] == 1
        assert results["errors"] == 0

        meeting = Meeting.objects.get(
            google_event_id="google_event_abc123"
        )
        assert meeting.title == "Team Standup"
        assert meeting.platform == Meeting.Platform.GOOGLE_MEET
        assert "meet.google.com" in meeting.meeting_url

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_skips_event_without_url(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_event_no_url,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([mock_event_no_url])

        from calendar_integration.tasks import sync_calendar_for_token
        results = sync_calendar_for_token(calendar_token)

        assert results["created"] == 0
        assert results["skipped"] == 1

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_skips_all_day_event(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_all_day_event,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([mock_all_day_event])

        from calendar_integration.tasks import sync_calendar_for_token
        results = sync_calendar_for_token(calendar_token)

        assert results["created"] == 0
        assert results["skipped"] == 1

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_upsert_updates_existing_meeting(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_google_event,
        user,
        organisation,
    ):
        """Running sync twice should update not duplicate."""
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([mock_google_event])

        from calendar_integration.tasks import sync_calendar_for_token

        # First sync — creates
        sync_calendar_for_token(calendar_token)
        assert Meeting.objects.filter(
            google_event_id="google_event_abc123"
        ).count() == 1

        # Update event title
        mock_google_event["summary"] = "Updated Standup Title"
        mock_build.return_value = self._mock_service([mock_google_event])

        # Second sync — updates
        results = sync_calendar_for_token(calendar_token)
        assert results["updated"] == 1
        assert Meeting.objects.filter(
            google_event_id="google_event_abc123"
        ).count() == 1

        meeting = Meeting.objects.get(
            google_event_id="google_event_abc123"
        )
        assert meeting.title == "Updated Standup Title"

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_updates_last_synced_at(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_google_event,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([mock_google_event])

        from calendar_integration.tasks import sync_calendar_for_token
        sync_calendar_for_token(calendar_token)

        calendar_token.refresh_from_db()
        assert calendar_token.last_synced_at is not None

    @patch("calendar_integration.tasks._get_valid_credentials")
    def test_invalid_credentials_marks_token_inactive(
        self,
        mock_creds,
        calendar_token,
    ):
        mock_creds.return_value = None  # Simulate refresh failure

        from calendar_integration.tasks import sync_calendar_for_token
        sync_calendar_for_token(calendar_token)

        calendar_token.refresh_from_db()
        assert calendar_token.is_active is False
        assert calendar_token.sync_error is not None

    @patch("calendar_integration.tasks._get_valid_credentials")
    @patch("calendar_integration.tasks.build")
    def test_processes_multiple_events(
        self,
        mock_build,
        mock_creds,
        calendar_token,
        mock_google_event,
        mock_zoom_event,
        mock_event_no_url,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = self._mock_service([
            mock_google_event,
            mock_zoom_event,
            mock_event_no_url,
        ])

        from calendar_integration.tasks import sync_calendar_for_token
        results = sync_calendar_for_token(calendar_token)

        # 2 created (meet + zoom), 1 skipped (no url)
        assert results["created"] == 2
        assert results["skipped"] == 1
        assert Meeting.objects.filter(
            created_by=calendar_token.user
        ).count() == 2
