import logging
from datetime import timedelta

from django.utils import timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.celery import app
from meetings.models import Meeting

logger = logging.getLogger("calendar_integration")

# How far ahead to look for meetings
SYNC_WINDOW_HOURS = 24


def _get_valid_credentials(token) -> Credentials | None:
    """
    Build Google credentials from stored token.
    Refreshes automatically if expired.
    Returns None if refresh fails.
    """
    creds = Credentials(
        token=token.access_token,
        refresh_token=token.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=__import__("django.conf", fromlist=["settings"]).settings.GOOGLE_CLIENT_ID,
        client_secret=__import__("django.conf", fromlist=["settings"]).settings.GOOGLE_CLIENT_SECRET,
    )

    if token.is_expired:
        try:
            creds.refresh(Request())

            # Save refreshed token back to DB
            from django.utils import timezone as tz
            token.access_token = creds.token
            if creds.expiry:
                expiry = creds.expiry
                if timezone.is_naive(expiry):
                    expiry = timezone.make_aware(expiry)
                token.token_expiry = expiry
            token.save(update_fields=["access_token", "token_expiry"])

            logger.info(
                "Access token refreshed for user %s",
                token.user.email,
            )
        except Exception as exc:
            logger.error(
                "Failed to refresh token for user %s: %s",
                token.user.email,
                exc,
            )
            return None

    return creds


def _extract_meeting_url(event: dict) -> str | None:
    """
    Extract a meeting URL from a Google Calendar event.
    Checks in order:
    1. Google Meet link (hangoutLink)
    2. Conference data entry points
    3. Location field (sometimes contains Zoom/Teams URL)
    4. Description (scan for known URL patterns)
    """
    import re

    # 1. Google Meet — most common
    hangout = event.get("hangoutLink")
    if hangout:
        return hangout

    # 2. Conference data entry points
    conference = event.get("conferenceData", {})
    for entry in conference.get("entryPoints", []):
        if entry.get("entryPointType") == "video":
            uri = entry.get("uri", "")
            if uri:
                return uri

    # 3. Location field
    location = event.get("location", "")
    url_pattern = re.compile(
        r"https?://"
        r"(?:(?:[\w.-]+\.)?zoom\.us/j/\S+|"
        r"meet\.google\.com/\S+|"
        r"teams\.microsoft\.com/\S+)",
        re.IGNORECASE,
    )
    if location:
        match = url_pattern.search(location)
        if match:
            return match.group(0)

    # 4. Description field
    description = event.get("description", "") or ""
    match = url_pattern.search(description)
    if match:
        return match.group(0)

    return None


def _detect_platform(url: str) -> str:
    """Detect meeting platform from URL."""
    url_lower = url.lower()
    if "zoom.us" in url_lower:
        return Meeting.Platform.ZOOM
    if "meet.google.com" in url_lower:
        return Meeting.Platform.GOOGLE_MEET
    if "teams.microsoft.com" in url_lower:
        return Meeting.Platform.TEAMS
    return Meeting.Platform.ZOOM  # default fallback


def _parse_event_time(event: dict) -> tuple:
    """
    Parse start and end times from a calendar event.
    Returns (start_datetime, end_datetime) as aware datetimes.
    All-day events return None, None — we skip those.
    """
    from datetime import datetime

    start = event.get("start", {})
    end = event.get("end", {})

    # All-day events have 'date' not 'dateTime' — skip them
    if "date" in start and "dateTime" not in start:
        return None, None

    try:
        start_str = start.get("dateTime", "")
        end_str = end.get("dateTime", "")

        # Parse ISO format
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)

        # Make timezone-aware if naive
        if timezone.is_naive(start_dt):
            start_dt = timezone.make_aware(start_dt)
        if timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt)

        return start_dt, end_dt
    except Exception:
        return None, None


def sync_calendar_for_token(token) -> dict:
    """
    Core sync logic for a single GoogleCalendarToken.
    Fetches events from Google, creates/updates Meeting records.
    Returns a dict with sync results.
    """
    results = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Get valid credentials
    creds = _get_valid_credentials(token)
    if not creds:
        token.sync_error = "Failed to refresh access token. Please reconnect."
        token.is_active = False
        token.save(update_fields=["sync_error", "is_active"])
        return results

    # Build Google Calendar service
    try:
        service = build("calendar", "v3", credentials=creds)
    except Exception as exc:
        logger.error(
            "Failed to build calendar service for user %s: %s",
            token.user.email,
            exc,
        )
        return results

    # Fetch events in the sync window
    now = timezone.now()
    time_min = now.isoformat()
    time_max = (now + timedelta(hours=SYNC_WINDOW_HOURS)).isoformat()

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
        events = events_result.get("items", [])
    except HttpError as exc:
        logger.error(
            "Google Calendar API error for user %s: %s",
            token.user.email,
            exc,
        )
        token.sync_error = f"Calendar API error: {exc}"
        token.save(update_fields=["sync_error"])
        return results

    logger.info(
        "Fetched %d events for user %s",
        len(events),
        token.user.email,
    )

    # Process each event
    for event in events:
        try:
            google_event_id = event.get("id")
            if not google_event_id:
                results["skipped"] += 1
                continue

            # Extract meeting URL — skip if none
            meeting_url = _extract_meeting_url(event)
            if not meeting_url:
                results["skipped"] += 1
                continue

            # Parse times — skip all-day events
            start_dt, end_dt = _parse_event_time(event)
            if not start_dt:
                results["skipped"] += 1
                continue

            # Build meeting data
            title = event.get("summary", "Untitled Meeting")
            description = event.get("description", "") or ""
            platform = _detect_platform(meeting_url)

            # Upsert — update if exists, create if not
            meeting, created = Meeting.objects.update_or_create(
                google_event_id=google_event_id,
                created_by=token.user,
                defaults={
                    "organisation": token.organisation,
                    "title": title,
                    "description": description[:500],
                    "platform": platform,
                    "meeting_url": meeting_url,
                    "scheduled_at": start_dt,
                    "status": Meeting.Status.SCHEDULED,
                    "is_archived": False,
                },
            )

            if created:
                results["created"] += 1
                logger.info(
                    "Created meeting '%s' from calendar event %s for user %s",
                    title,
                    google_event_id,
                    token.user.email,
                )
            else:
                results["updated"] += 1

        except Exception as exc:
            logger.error(
                "Error processing event %s for user %s: %s",
                event.get("id", "unknown"),
                token.user.email,
                exc,
            )
            results["errors"] += 1
            continue

    # Update sync state
    token.last_synced_at = timezone.now()
    token.sync_error = None
    token.save(update_fields=["last_synced_at", "sync_error"])

    return results


@app.task(name="calendar_integration.sync_all_calendars")
def sync_all_calendars():
    """
    Celery Beat task — runs every 15 minutes.
    Syncs Google Calendar for all active connected users.
    """
    from .models import GoogleCalendarToken

    tokens = GoogleCalendarToken.objects.filter(
        is_active=True,
    ).select_related("user", "organisation")

    total = tokens.count()
    logger.info("Starting calendar sync for %d active connections", total)

    total_created = 0
    total_updated = 0
    total_errors = 0

    for token in tokens:
        try:
            results = sync_calendar_for_token(token)
            total_created += results["created"]
            total_updated += results["updated"]
            total_errors += results["errors"]
        except Exception as exc:
            logger.error(
                "Calendar sync failed for user %s: %s",
                token.user.email,
                exc,
            )
            total_errors += 1

    logger.info(
        "Calendar sync complete — %d created, %d updated, %d errors",
        total_created,
        total_updated,
        total_errors,
    )


@app.task(name="calendar_integration.sync_calendar_for_user")
def sync_calendar_for_user(user_id: str, organisation_id: str = None):
    """
    On-demand sync for a single user.
    Called immediately after a user connects their calendar.
    """
    from .models import GoogleCalendarToken

    try:
        token = GoogleCalendarToken.objects.select_related(
            "user", "organisation"
        ).get(
            user_id=user_id,
            organisation_id=organisation_id,
            is_active=True,
        )
    except GoogleCalendarToken.DoesNotExist:
        logger.error(
            "No active calendar token found for user %s", user_id
        )
        return

    results = sync_calendar_for_token(token)
    logger.info(
        "On-demand sync for user %s — %d created, %d updated",
        user_id,
        results["created"],
        results["updated"],
    )
