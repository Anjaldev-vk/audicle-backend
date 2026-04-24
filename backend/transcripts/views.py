import logging

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from meetings.models import Meeting
from meetings.utils import get_meeting_or_404
from transcripts.models import Transcript, TranscriptSegment
from transcripts.serializers import (
    InternalTranscriptCompleteSerializer,
    TranscriptSegmentListSerializer,
    TranscriptSerializer,
)
from transcripts.utils import get_transcript_for_meeting
from utils.kafka_producer import send_transcription_task
from utils.response import error_response, success_response

logger = logging.getLogger("transcripts")


# ----------------- Public API views for frontend users -----------------

class TranscriptDetailView(APIView):
    """
    GET    /api/v1/meetings/<meeting_id>/transcript/
    DELETE /api/v1/meetings/<meeting_id>/transcript/

    GET: Returns the full transcript with all segments.
    DELETE: Deletes the transcript and all segments.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user)
        if not transcript:
            return error_response(
                message="Transcript not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return success_response(
            message="Transcript retrieved successfully.",
            data=TranscriptSerializer(transcript).data,
            status_code=status.HTTP_200_OK,
        )

    def delete(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user)
        if not transcript:
            return error_response(
                message="Transcript not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        transcript.delete()
        logger.info(
            "Transcript deleted for meeting %s by %s",
            meeting_id,
            request.user.email,
        )
        return success_response(
            message="Transcript deleted successfully.",
            status_code=status.HTTP_200_OK,
        )


class TranscriptSegmentListView(APIView):
    """
    GET /api/v1/meetings/<meeting_id>/transcript/segments/

    Returns all transcript segments ordered by start_seconds.
    Segments are the individual timestamped lines of the transcript.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user)
        if not transcript:
            return error_response(
                message="Transcript not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        segments = transcript.segments.all()
        serializer = TranscriptSegmentListSerializer(segments, many=True)

        return success_response(
            message="Segments retrieved successfully.",
            data={
                "transcript_id": str(transcript.id),
                "total_segments": segments.count(),
                "segments": serializer.data,
            },
            status_code=status.HTTP_200_OK,
        )


class TranscriptRetryView(APIView):
    """
    POST /api/v1/meetings/<meeting_id>/transcript/retry/

    Retries a failed transcription.
    Maximum 3 retry attempts tracked on the Transcript model.
    Re-fires the Kafka message to the Whisper worker.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user)
        if not transcript:
            return error_response(
                message="Transcript not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not transcript.can_retry:
            if transcript.retry_count >= 3:
                return error_response(
                    message="Maximum retry attempts (3) reached. Please contact support.",
                    code="max_retries_exceeded",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            return error_response(
                message=f"Cannot retry — transcript is currently '{transcript.get_status_display()}'.",
                code="invalid_status",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Check meeting still has audio
        if not transcript.meeting.audio_s3_key:
            return error_response(
                message="No audio file found. Please upload audio first.",
                code="no_audio",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Re-fire Kafka message
        try:
            send_transcription_task(
                meeting_id=str(transcript.meeting.id),
                file_path=transcript.meeting.audio_s3_key,
                user_id=str(request.user.id),
            )
        except Exception as exc:
            logger.error(
                "Kafka retry failed for meeting %s: %s",
                meeting_id,
                exc,
            )
            return error_response(
                message="Could not queue retry. Please try again.",
                code="kafka_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Update transcript status
        transcript.status = Transcript.Status.PENDING
        transcript.error_message = None
        transcript.retry_count += 1
        transcript.save(
            update_fields=["status", "error_message", "retry_count"])

        logger.info(
            "Transcript retry queued for meeting %s attempt %d by %s",
            meeting_id,
            transcript.retry_count,
            request.user.email,
        )

        return success_response(
            message=f"Retry queued. Attempt {transcript.retry_count} of 3.",
            data={
                "transcript_id": str(transcript.id),
                "retry_count":   transcript.retry_count,
                "status":        transcript.status,
            },
            status_code=status.HTTP_200_OK,
        )


# ----------------- Internal API views for ai_service/worker.py -----------------

class InternalTranscriptCompleteView(APIView):
    """
    POST /api/v1/internal/transcript/complete/

    INTERNAL ONLY — called by ai_service/worker.py after Whisper finishes.
    Not exposed to frontend users.

    Security: X-Internal-Secret header must match INTERNAL_API_SECRET setting.

    On success:
    - Creates or updates Transcript record
    - Creates TranscriptSegment records
    - Updates Meeting status to completed or failed
    """
    permission_classes = []   # No JWT — uses shared secret instead

    def post(self, request):

        # 1. Verify internal secret
        secret = request.headers.get("X-Internal-Secret")
        if not secret or secret != settings.INTERNAL_API_SECRET:
            logger.warning(
                "Internal transcript endpoint called with invalid secret"
            )
            return error_response(
                message="Unauthorized.",
                code="unauthorized",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # 2. Validate payload
        serializer = InternalTranscriptCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 3. Find the meeting
        try:
            meeting = Meeting.objects.select_related(
                "organisation",
                "created_by",
            ).get(id=data["meeting_id"])
        except Meeting.DoesNotExist:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # 4. Save transcript and segments atomically
        try:
            with transaction.atomic():
                transcript, created = Transcript.objects.get_or_create(
                    meeting=meeting,
                    defaults={
                        "organisation": meeting.organisation,
                        "created_by":   meeting.created_by,
                        "status":       Transcript.Status.PROCESSING,
                    },
                )

                if data["status"] == "completed":
                    # Save transcript data
                    transcript.status = Transcript.Status.COMPLETED
                    transcript.language = data.get("language", "en")
                    transcript.raw_text = data.get("raw_text", "")
                    transcript.duration_seconds = data.get("duration_seconds")
                    transcript.error_message = None
                    transcript.save()

                    # Delete old segments if retrying
                    transcript.segments.all().delete()

                    # Save all segments
                    segments = [
                        TranscriptSegment(
                            transcript=transcript,
                            text=seg["text"],
                            start_seconds=seg["start_seconds"],
                            end_seconds=seg["end_seconds"],
                            confidence=seg.get("confidence"),
                        )
                        for seg in data.get("segments", [])
                    ]
                    TranscriptSegment.objects.bulk_create(segments)

                    # Update meeting status
                    meeting.status = Meeting.Status.COMPLETED
                    meeting.save(update_fields=["status"])

                    logger.info(
                        "Transcript completed for meeting %s — %d segments, %d words",
                        meeting.id,
                        len(segments),
                        transcript.word_count,
                    )

                else:
                    # Handle failure
                    transcript.status = Transcript.Status.FAILED
                    transcript.error_message = data.get(
                        "error_message", "Unknown error")
                    transcript.save(update_fields=["status", "error_message"])

                    meeting.status = Meeting.Status.FAILED
                    meeting.save(update_fields=["status"])

                    logger.error(
                        "Transcript failed for meeting %s: %s",
                        meeting.id,
                        transcript.error_message,
                    )

        except Exception as exc:
            logger.error(
                "Failed to save transcript for meeting %s: %s",
                data["meeting_id"],
                exc,
            )
            return error_response(
                message="Failed to save transcript.",
                code="save_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return success_response(
            message="Transcript saved successfully.",
            data={
                "transcript_id":  str(transcript.id),
                "status":         transcript.status,
                "word_count":     transcript.word_count,
                "segment_count":  transcript.segments.count(),
            },
            status_code=status.HTTP_200_OK,
        )
