import uuid

from django.db import models

from accounts.models import Organisation, User
from meetings.models import Meeting


class Transcript(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending",    "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed",  "Completed"
        FAILED = "failed",     "Failed"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    meeting = models.OneToOneField(
        Meeting,
        on_delete=models.CASCADE,
        related_name="transcript",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transcripts",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="transcripts",
    )

    # Transcription data
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    language = models.CharField(
        max_length=10,
        default="en",
        help_text="Language detected by Whisper e.g. en, hi, fr",
    )
    raw_text = models.TextField(
        blank=True,
        default="",
        help_text="Full transcript text joined from all segments",
    )
    word_count = models.PositiveIntegerField(
        default=0,
        help_text="Auto-computed from raw_text on save",
    )
    duration_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Total audio duration reported by Whisper",
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of transcription attempts",
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Stores failure reason if status=failed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["meeting"]),
            models.Index(fields=["organisation", "status"]),
            models.Index(fields=["created_by", "status"]),
        ]
        verbose_name = "Transcript"
        verbose_name_plural = "Transcripts"

    def __str__(self):
        return f"Transcript({self.meeting.title}, {self.get_status_display()})"

    def save(self, *args, **kwargs):
        # Auto-compute word count from raw text
        if self.raw_text:
            self.word_count = len(self.raw_text.split())
        super().save(*args, **kwargs)

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def can_retry(self) -> bool:
        """Allow retry only if failed and under 3 attempts."""
        return (
            self.status == self.Status.FAILED
            and self.retry_count < 3
        )


class TranscriptSegment(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.CASCADE,
        related_name="segments",
    )

    # Whisper returns speaker labels with diarization
    # We store null now — Phase 7 fills this in
    speaker_label = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Speaker label e.g. SPEAKER_01 — filled in Phase 7",
    )
    text = models.TextField(
        help_text="Transcript text for this segment",
    )
    start_seconds = models.FloatField(
        help_text="Segment start time in seconds",
    )
    end_seconds = models.FloatField(
        help_text="Segment end time in seconds",
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Whisper confidence score 0.0 to 1.0",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_seconds"]
        indexes = [
            models.Index(fields=["transcript", "start_seconds"]),
        ]
        verbose_name = "Transcript Segment"
        verbose_name_plural = "Transcript Segments"

    def __str__(self):
        return (
            f"Segment({self.start_seconds:.1f}s → "
            f"{self.end_seconds:.1f}s: {self.text[:50]})"
        )

    @property
    def duration_seconds(self) -> float:
        return round(self.end_seconds - self.start_seconds, 2)


class MeetingSummary(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending",    "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed",  "Completed"
        FAILED = "failed",     "Failed"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    meeting = models.OneToOneField(
        Meeting,
        on_delete=models.CASCADE,
        related_name="summary",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="summaries",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="summaries",
    )

    # Summary data
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    summary = models.TextField(
        blank=True,
        default="",
        help_text="3-5 sentence overview of the meeting",
    )
    key_points = models.JSONField(
        default=list,
        help_text="List of key discussion points",
    )
    action_items = models.JSONField(
        default=list,
        help_text="List of action items with owner and due date",
    )
    decisions = models.JSONField(
        default=list,
        help_text="List of decisions made in the meeting",
    )
    next_steps = models.JSONField(
        default=list,
        help_text="List of next steps",
    )
    retry_count = models.PositiveIntegerField(
        default=0,
    )
    error_message = models.TextField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["meeting"]),
            models.Index(fields=["organisation", "status"]),
        ]
        verbose_name = "Meeting Summary"
        verbose_name_plural = "Meeting Summaries"

    def __str__(self):
        return f"Summary({self.meeting.title}, {self.get_status_display()})"

    @property
    def can_retry(self) -> bool:
        return (
            self.status == self.Status.FAILED
            and self.retry_count < 3
        )
