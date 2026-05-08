import uuid

from django.db import models

from accounts.models import User, Organisation


class GoogleCalendarToken(models.Model):
    """
    Stores Google OAuth tokens for calendar integration per user.
    One token per user per workspace.
    Tokens are refreshed automatically when expired.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="calendar_tokens",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="calendar_tokens",
    )

    # OAuth tokens
    access_token = models.TextField(
        help_text="Google OAuth access token",
    )
    refresh_token = models.TextField(
        help_text="Google OAuth refresh token — used to get new access tokens",
    )
    token_expiry = models.DateTimeField(
        help_text="When the access token expires",
    )

    # Google account info
    google_email = models.EmailField(
        help_text="Google account email this token belongs to",
    )

    # Sync state
    is_active = models.BooleanField(
        default=True,
        help_text="False if user disconnected or token was revoked",
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time we successfully fetched events from Google",
    )
    sync_error = models.TextField(
        null=True,
        blank=True,
        help_text="Last sync error message if any",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One active calendar connection per user per workspace
        unique_together = [["user", "organisation"]]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["organisation", "is_active"]),
        ]
        verbose_name = "Google Calendar Token"
        verbose_name_plural = "Google Calendar Tokens"

    def __str__(self):
        return f"CalendarToken({self.google_email}, user={self.user.email})"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone
        return timezone.now() >= self.token_expiry
