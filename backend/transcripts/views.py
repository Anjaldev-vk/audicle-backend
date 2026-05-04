import logging

from django.conf import settings
from django.db import transaction
from rest_framework import status, generics, permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from meetings.models import Meeting
from meetings.utils import get_meeting_or_404
from transcripts.models import Transcript, TranscriptSegment, MeetingSummary
from transcripts.serializers import (
    InternalTranscriptCompleteSerializer,
    InternalSummaryCompleteSerializer,
    TranscriptSegmentEditSerializer,
    MeetingSummarySerializer,
    TranscriptSegmentListSerializer,
    TranscriptSerializer,
    TranslateSummarySerializer,
)
from transcripts.utils import get_transcript_for_meeting
from utils.kafka_producer import send_transcription_task, send_summarization_task, send_embedding_task
from utils.response import error_response, success_response
from utils.permissions import IsInternalService
from utils.pagination import StandardPagination
from notifications.tasks import notify_transcription_done, notify_summary_done

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
        transcript = get_transcript_for_meeting(meeting_id, request.user, request.organisation)
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
        transcript = get_transcript_for_meeting(meeting_id, request.user, request.organisation)
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

    Returns all transcript segments ordered by start_seconds (paginated).
    Segments are the individual timestamped lines of the transcript.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user, request.organisation)
        if not transcript:
            return error_response(
                message="Transcript not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        segments = transcript.segments.all()
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(segments, request)
        serializer = TranscriptSegmentListSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)


class TranscriptRetryView(APIView):
    """
    POST /api/v1/meetings/<meeting_id>/transcript/retry/

    Retries a failed transcription.
    Maximum 3 retry attempts tracked on the Transcript model.
    Re-fires the Kafka message to the Whisper worker.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        transcript = get_transcript_for_meeting(meeting_id, request.user, request.organisation)
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
    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request):

        # 1. Validate payload
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

                    # Notify the user that transcription is done
                    notify_transcription_done.delay(
                        user_id=str(meeting.created_by.id),
                        meeting_id=str(meeting.id),
                        meeting_title=meeting.title,
                        workspace_id=str(meeting.organisation.id) if meeting.organisation else None,
                    )

                    logger.info(
                        "Transcript completed for meeting %s — %d segments, %d words",
                        meeting.id,
                        len(segments),
                        transcript.word_count,
                    )

                    # Auto-trigger summarization
                    try:
                        send_summarization_task(
                            meeting_id      = str(meeting.id),
                            transcript_text = transcript.raw_text,
                        )
                        logger.info(
                            "Summarization task fired for meeting %s",
                            meeting.id,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to fire summarization task for meeting %s: %s",
                            meeting.id,
                            exc,
                        )

                    # Auto-trigger embedding for RAG
                    try:
                        send_embedding_task(
                            transcript_id = str(transcript.id),
                            raw_text      = transcript.raw_text,
                            segments      = [
                                {
                                    "text":          seg.text,
                                    "start_seconds": seg.start_seconds,
                                    "end_seconds":   seg.end_seconds,
                                }
                                for seg in transcript.segments.all()
                            ],
                        )
                        logger.info(
                            "Embedding task fired for transcript %s",
                            transcript.id,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to fire embedding task for transcript %s: %s",
                            transcript.id,
                            exc,
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


# ── Summary Views ─────────────────────────────────────────────────────────────

class SummaryDetailView(APIView):
    """
    GET    /api/v1/meetings/<meeting_id>/summary/
    DELETE /api/v1/meetings/<meeting_id>/summary/

    GET: Returns the AI-generated meeting summary with
         action items, key points, decisions and next steps.

    DELETE: Permanently deletes the summary.
            User can regenerate via retry endpoint.
    """
    permission_classes = [IsAuthenticated]

    def _get_meeting(self, meeting_id, user):
        """Helper — get meeting scoped to user tenant."""
        meeting = get_meeting_or_404(meeting_id, user, getattr(self.request, 'organisation', None))
        if not meeting:
            return None, error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return meeting, None

    def _get_summary(self, meeting):
        """Helper — get summary for a meeting."""
        try:
            return MeetingSummary.objects.get(meeting=meeting), None
        except MeetingSummary.DoesNotExist:
            return None, error_response(
                message="Summary not found. It may still be generating.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request, meeting_id):
        meeting, err = self._get_meeting(meeting_id, request.user)
        if err:
            return err

        summary, err = self._get_summary(meeting)
        if err:
            return err

        return success_response(
            message="Summary retrieved successfully.",
            data=MeetingSummarySerializer(summary).data,
            status_code=status.HTTP_200_OK,
        )

    def delete(self, request, meeting_id):
        meeting, err = self._get_meeting(meeting_id, request.user)
        if err:
            return err

        summary, err = self._get_summary(meeting)
        if err:
            return err

        summary.delete()
        logger.info(
            "Summary deleted for meeting %s by %s",
            meeting_id,
            request.user.email,
        )
        return success_response(
            message="Summary deleted successfully.",
            status_code=status.HTTP_200_OK,
        )


class SummaryRetryView(APIView):
    """
    POST /api/v1/meetings/<meeting_id>/summary/retry/

    Retries a failed summary generation.
    Maximum 3 retry attempts tracked on the model.
    Re-fires the Kafka summarization message.

    Requirements:
    - Summary must exist and be in failed status
    - retry_count must be under 3
    - Meeting must have a completed transcript
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        if not meeting:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            summary = MeetingSummary.objects.get(meeting=meeting)
        except MeetingSummary.DoesNotExist:
            return error_response(
                message="Summary not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Check retry eligibility
        if summary.retry_count >= 3:
            return error_response(
                message="Maximum retry attempts (3) reached. Please contact support.",
                code="max_retries_exceeded",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if not summary.can_retry:
            return error_response(
                message=f"Cannot retry — summary is currently '{summary.get_status_display()}'.",
                code="invalid_status",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Get transcript text for re-summarization
        try:
            transcript = meeting.transcript
            if not transcript.raw_text:
                raise ValueError("Empty transcript")
        except Exception:
            return error_response(
                message="No completed transcript found. Please transcribe the meeting first.",
                code="no_transcript",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Re-fire Kafka message
        try:
            send_summarization_task(
                meeting_id=str(meeting.id),
                transcript_text=transcript.raw_text,
            )
        except Exception as exc:
            logger.error(
                "Kafka summarization retry failed for meeting %s: %s",
                meeting_id,
                exc,
            )
            return error_response(
                message="Could not queue retry. Please try again.",
                code="kafka_error",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Update summary status
        summary.status = MeetingSummary.Status.PENDING
        summary.error_message = None
        summary.retry_count += 1
        summary.save(update_fields=["status", "error_message", "retry_count"])

        logger.info(
            "Summary retry queued for meeting %s attempt %d by %s",
            meeting_id,
            summary.retry_count,
            request.user.email,
        )

        return success_response(
            message=f"Retry queued. Attempt {summary.retry_count} of 3.",
            data={
                "summary_id":  str(summary.id),
                "retry_count": summary.retry_count,
                "status":      summary.status,
            },
            status_code=status.HTTP_200_OK,
        )


class SummaryTranslateView(APIView):
    """
    POST /api/v1/meetings/<meeting_id>/summary/translate/

    Translates the meeting summary to any language on demand.
    Translation is NOT cached — each request calls the AI.

    Only works if summary status is completed.

    Request body:
    {
        "target_language": "Malayalam"
    }

    Response:
    {
        "target_language":    "Malayalam",
        "translated_summary": "...",
        "original_language":  "English"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        if not meeting:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Only translate completed summaries
        try:
            summary = MeetingSummary.objects.get(
                meeting=meeting,
                status=MeetingSummary.Status.COMPLETED,
            )
        except MeetingSummary.DoesNotExist:
            return error_response(
                message="No completed summary found. Summary may still be generating.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Validate request
        serializer = TranslateSummarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_language = serializer.validated_data["target_language"]

        # Build full summary text for translation
        key_points_text = "\n".join(f"- {p}" for p in summary.key_points)
        decisions_text = "\n".join(f"- {d}" for d in summary.decisions)
        next_steps_text = "\n".join(f"- {s}" for s in summary.next_steps)

        summary_text = (
            f"Summary:\n{summary.summary}\n\n"
            f"Key Points:\n{key_points_text}\n\n"
            f"Decisions:\n{decisions_text}\n\n"
            f"Next Steps:\n{next_steps_text}"
        ).strip()

        # Translate using configured AI provider
        translated = self._translate(summary_text, target_language)

        if not translated:
            return error_response(
                message=f"Translation to {target_language} failed. Please try again.",
                code="translation_failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        logger.info(
            "Summary translated to %s for meeting %s by %s",
            target_language,
            meeting_id,
            request.user.email,
        )

        return success_response(
            message=f"Summary translated to {target_language} successfully.",
            data={
                "target_language":    target_language,
                "translated_summary": translated,
                "original_language":  "English",
            },
            status_code=status.HTTP_200_OK,
        )

    def _translate(self, text: str, target_language: str) -> str | None:
        """
        Translate text using the configured AI provider.
        Works with Gemini, OpenAI, or Ollama — no code changes needed.
        Just change AI_BACKEND in .env to switch providers.
        """
        try:
            from utils.ai_client import get_ai_provider

            provider = get_ai_provider()
            return provider.generate(
                system_prompt=(
                    "You are a professional translator. "
                    f"Translate the following text to {target_language}. "
                    "Preserve meaning, tone and formatting exactly. "
                    "Return only the translated text with no explanation."
                ),
                user_prompt=text,
                temperature=0.1,
                max_tokens=2000,
            )
        except Exception as exc:
            logger.error(
                "Translation to %s failed: %s",
                target_language,
                exc,
            )
            return None


# ── Internal Summary Endpoint ─────────────────────────────────────────────────

class InternalSummaryCompleteView(APIView):
    """
    POST /api/v1/internal/summary/complete/

    INTERNAL ONLY — called by ai_service/worker.py
    after Gemini/GPT-4o finishes summarization.

    NOT exposed to frontend users.

    Security:
        X-Internal-Secret header must match
        INTERNAL_API_SECRET in settings.

    On success:
        Creates or updates MeetingSummary record
        with all structured summary data.
    """
    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request):

        # 1. Validate payload
        serializer = InternalSummaryCompleteSerializer(data=request.data)
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

        # 4. Save summary atomically
        try:
            with transaction.atomic():
                summary, created = MeetingSummary.objects.get_or_create(
                    meeting=meeting,
                    defaults={
                        "organisation": meeting.organisation,
                        "created_by":   meeting.created_by,
                        "status":       MeetingSummary.Status.PROCESSING,
                    },
                )

                if data["status"] == "completed":
                    summary.status = MeetingSummary.Status.COMPLETED
                    summary.summary = data.get("summary", "")
                    summary.key_points = data.get("key_points", [])
                    summary.action_items = data.get("action_items", [])
                    summary.decisions = data.get("decisions", [])
                    summary.next_steps = data.get("next_steps", [])
                    summary.error_message = None
                    summary.save()

                    # Notify the user that summary is done
                    notify_summary_done.delay(
                        user_id=str(meeting.created_by.id),
                        meeting_id=str(meeting.id),
                        meeting_title=meeting.title,
                        workspace_id=str(meeting.organisation.id) if meeting.organisation else None,
                    )

                    logger.info(
                        "Summary saved for meeting %s — %d action items %d decisions",
                        meeting.id,
                        len(summary.action_items),
                        len(summary.decisions),
                    )

                else:
                    summary.status = MeetingSummary.Status.FAILED
                    summary.error_message = data.get(
                        "error_message",
                        "Unknown error",
                    )
                    summary.save(
                        update_fields=["status", "error_message"]
                    )

                    logger.error(
                        "Summary failed for meeting %s: %s",
                        meeting.id,
                        summary.error_message,
                    )

        except Exception as exc:
            logger.error(
                "Failed to save summary for meeting %s: %s",
                data["meeting_id"],
                exc,
            )
            return error_response(
                message="Failed to save summary.",
                code="save_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return success_response(
            message="Summary saved successfully.",
            data={
                "summary_id":        str(summary.id),
                "status":            summary.status,
                "action_item_count": len(summary.action_items),
                "key_point_count":   len(summary.key_points),
            },
            status_code=status.HTTP_200_OK,
        )


class TranscriptSegmentEditView(generics.GenericAPIView):
    """
    PATCH /api/v1/meetings/<meeting_id>/transcript/segments/<segment_id>/
    Allows editing text and speaker_name of a single segment.
    Sets is_edited=True automatically.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = TranscriptSegmentEditSerializer

    def get_object(self, meeting_id, segment_id, user, organisation):
        # Scope meeting to this user's workspace
        meeting = get_meeting_or_404(meeting_id, user, organisation)
        if not meeting:
            return None
            
        # Scope segment to this meeting's transcript
        try:
            return TranscriptSegment.objects.get(
                id=segment_id,
                transcript__meeting=meeting,
            )
        except TranscriptSegment.DoesNotExist:
            return None

    def patch(self, request, meeting_id, segment_id):
        segment = self.get_object(meeting_id, segment_id, request.user, getattr(request, 'organisation', None))
        if not segment:
            return error_response(
                message="Segment or meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
            
        serializer = self.get_serializer(
            segment,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error_response(
                message="Validation failed",
                code="validation_error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Auto-set is_edited=True on any successful edit
        serializer.save(is_edited=True)

        logger.info(
            "Segment %s edited by user %s",
            segment_id,
            request.user.email,
        )
        return success_response(
            message="Segment updated successfully",
            data=serializer.data,
            status_code=status.HTTP_200_OK,
        )

