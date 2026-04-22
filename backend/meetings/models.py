from django.db import models
from django.utils import timezone

from accounts.models import Organisation, User


class Meeting(models.Model):

    class Platform(models.TextChoices):
        ZOOM = "zoom",        "Zoom"
        GOOGLE_MEET = "google_meet", "Google Meet"
        TEAMS = "teams",       "Microsoft Teams"
        UPLOAD = "upload",      "Manual Upload"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled",   "Scheduled"
        BOT_JOINING = "bot_joining", "Bot Joining"
        RECORDING = "recording",   "Recording"
        PROCESSING = "processing",  "Processing"
        COMPLETED = "completed",   "Completed"
        FAILED = "failed",      "Failed"

    # Identity
    id = models.UUIDField(
        primary_key=True,
        default=__import__("uuid").uuid4,
        editable=False,
    )

    # Tenant scoping
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="meetings",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="meetings",
    )

    # Core fields
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
        default=Platform.ZOOM,
    )
    meeting_url = models.URLField(null=True, blank=True)

    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    # Timestamps
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    # Storage keys — Phase 5 / future
    audio_s3_key = models.CharField(max_length=500, null=True, blank=True)
    video_s3_key = models.CharField(max_length=500, null=True, blank=True)

    # Soft delete
    is_archived = models.BooleanField(default=False)

    # Auto timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_at", "-created_at"]
        indexes = [
            models.Index(fields=["organisation", "is_archived"]),
            models.Index(fields=["created_by",   "is_archived"]),
            models.Index(fields=["status"]),
            models.Index(fields=["scheduled_at"]),
        ]
        verbose_name = "Meeting"
        verbose_name_plural = "Meetings"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        # Auto-compute duration when both timestamps are present
        if self.started_at and self.ended_at:
            self.duration_seconds = int(
                (self.ended_at - self.started_at).total_seconds()
            )
        super().save(*args, **kwargs)

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.RECORDING

    @property
    def is_editable(self) -> bool:
        """Only scheduled meetings can be edited."""
        return self.status == self.Status.SCHEDULED


class MeetingParticipant(models.Model):

    class Role(models.TextChoices):
        HOST = "host",        "Host"
        PARTICIPANT = "participant", "Participant"

    id = models.UUIDField(
        primary_key=True,
        default=__import__("uuid").uuid4,
        editable=False,
    )
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meeting_participations",
    )

    # External participants may not have accounts
    email = models.EmailField()
    name = models.CharField(max_length=255)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PARTICIPANT,
    )

    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One participant entry per user per meeting
        unique_together = [["meeting", "email"]]
        indexes = [
            models.Index(fields=["meeting"]),
            models.Index(fields=["user"]),
        ]
        verbose_name = "Meeting Participant"
        verbose_name_plural = "Meeting Participants"

    def __str__(self):
        return f"{self.name} — {self.meeting.title} ({self.get_role_display()})"
