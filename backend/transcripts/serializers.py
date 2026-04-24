from rest_framework import serializers

from transcripts.models import Transcript, TranscriptSegment


class TranscriptSegmentSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True)

    class Meta:
        model = TranscriptSegment
        fields = [
            "id",
            "speaker_label",
            "text",
            "start_seconds",
            "end_seconds",
            "confidence",
            "duration_seconds",
            "created_at",
        ]
        read_only_fields = ["id", "duration_seconds", "created_at"]


class TranscriptSerializer(serializers.ModelSerializer):
    segments = TranscriptSegmentSerializer(many=True, read_only=True)
    meeting_id = serializers.UUIDField(source="meeting.id", read_only=True)
    meeting_title = serializers.CharField(
        source="meeting.title", read_only=True)

    class Meta:
        model = Transcript
        fields = [
            "id",
            "meeting_id",
            "meeting_title",
            "status",
            "language",
            "raw_text",
            "word_count",
            "duration_seconds",
            "retry_count",
            "error_message",
            "is_completed",
            "can_retry",
            "segments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "meeting_id", "meeting_title",
            "status", "language", "raw_text",
            "word_count", "duration_seconds",
            "retry_count", "error_message",
            "is_completed", "can_retry",
            "created_at", "updated_at",
        ]


class TranscriptSegmentListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for segment list endpoint.
    Excludes nested data for performance.
    """
    duration_seconds = serializers.FloatField(read_only=True)

    class Meta:
        model = TranscriptSegment
        fields = [
            "id",
            "speaker_label",
            "text",
            "start_seconds",
            "end_seconds",
            "confidence",
            "duration_seconds",
        ]


# ----------------- Internal serializers (not exposed via public API) -----------------

class InternalSegmentSerializer(serializers.Serializer):
    """
    Validates a single segment from Whisper worker.
    """
    text = serializers.CharField()
    start_seconds = serializers.FloatField()
    end_seconds = serializers.FloatField()
    confidence = serializers.FloatField(required=False, allow_null=True)


class InternalTranscriptCompleteSerializer(serializers.Serializer):
    """
    Validates the payload sent by ai_worker after transcription.

    Called by:
        POST /api/v1/internal/transcript/complete/
        Header: X-Internal-Secret: <secret>

    Sent by:
        ai_service/worker.py after Whisper finishes
    """
    meeting_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=["completed", "failed"]
    )
    language = serializers.CharField(
        max_length=10,
        default="en",
        required=False,
    )
    raw_text = serializers.CharField(
        allow_blank=True,
        default="",
        required=False,
    )
    duration_seconds = serializers.FloatField(
        required=False,
        allow_null=True,
    )
    segments = InternalSegmentSerializer(
        many=True,
        required=False,
        default=list,
    )
    error_message = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
    )
