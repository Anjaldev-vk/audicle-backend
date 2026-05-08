import pytest
from unittest.mock import MagicMock
from django.utils import timezone
from datetime import timedelta

@pytest.fixture
def user(org_admin):
    return org_admin


@pytest.fixture
def calendar_token(user, organisation):
    from calendar_integration.models import GoogleCalendarToken
    return GoogleCalendarToken.objects.create(
        user=user,
        organisation=organisation,
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        token_expiry=timezone.now() + timedelta(hours=1),
        google_email="testuser@gmail.com",
        is_active=True,
    )


@pytest.fixture
def expired_calendar_token(user, organisation):
    from calendar_integration.models import GoogleCalendarToken
    return GoogleCalendarToken.objects.create(
        user=user,
        organisation=organisation,
        access_token="expired_access_token",
        refresh_token="test_refresh_token",
        token_expiry=timezone.now() - timedelta(hours=1),
        google_email="testuser@gmail.com",
        is_active=True,
    )


@pytest.fixture
def mock_google_event():
    """A standard Google Calendar event with a Google Meet link."""
    return {
        "id": "google_event_abc123",
        "summary": "Team Standup",
        "description": "Daily standup meeting",
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "start": {
            "dateTime": (
                timezone.now() + timedelta(hours=2)
            ).isoformat(),
        },
        "end": {
            "dateTime": (
                timezone.now() + timedelta(hours=3)
            ).isoformat(),
        },
    }


@pytest.fixture
def mock_zoom_event():
    """A Google Calendar event with a Zoom link in description."""
    return {
        "id": "google_event_zoom123",
        "summary": "Product Review",
        "description": "Join: https://zoom.us/j/123456789",
        "start": {
            "dateTime": (
                timezone.now() + timedelta(hours=4)
            ).isoformat(),
        },
        "end": {
            "dateTime": (
                timezone.now() + timedelta(hours=5)
            ).isoformat(),
        },
    }


@pytest.fixture
def mock_event_no_url():
    """A Google Calendar event with no meeting URL."""
    return {
        "id": "google_event_nourl",
        "summary": "Lunch Break",
        "description": "Just a lunch break",
        "start": {
            "dateTime": (
                timezone.now() + timedelta(hours=1)
            ).isoformat(),
        },
        "end": {
            "dateTime": (
                timezone.now() + timedelta(hours=2)
            ).isoformat(),
        },
    }


@pytest.fixture
def mock_all_day_event():
    """An all-day event — should be skipped."""
    return {
        "id": "google_event_allday",
        "summary": "Company Holiday",
        "start": {"date": "2026-05-10"},
        "end": {"date": "2026-05-11"},
    }
