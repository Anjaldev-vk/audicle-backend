from django.contrib import admin

from transcripts.models import MeetingSummary, Transcript, TranscriptSegment


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display    = (
        "meeting", "status", "language",
        "word_count", "retry_count", "created_at",
    )
    list_filter     = ("status", "language")
    search_fields   = ("meeting__title", "created_by__email")
    readonly_fields = ("id", "word_count", "created_at", "updated_at")
    ordering        = ("-created_at",)


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display    = (
        "transcript", "speaker_label",
        "start_seconds", "end_seconds", "text",
    )
    search_fields   = ("transcript__meeting__title", "text")
    readonly_fields = ("id", "created_at")
    ordering        = ("transcript", "start_seconds")


@admin.register(MeetingSummary)
class MeetingSummaryAdmin(admin.ModelAdmin):
    list_display    = (
        "meeting", "status",
        "retry_count", "created_at",
    )
    list_filter     = ("status",)
    search_fields   = ("meeting__title",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering        = ("-created_at",)