import logging

from django.conf import settings
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework import status as http_status

from meetings.internal_serializers import BotStatusSerializer
from meetings.models import Meeting
from utils.response import error_response, success_response
from utils.permissions import IsInternalService
from notifications.tasks import notify_meeting_started, notify_bot_failed

logger = logging.getLogger("meetings")


class BotStatusView(APIView):
    """
    POST /internal/bot/status/

    Internal-only endpoint. Called by bot_service/bot_runner.py after every
    status change in the bot lifecycle.

    Auth:    X-Internal-Secret header (shared secret, not JWT)
    Payload: { meeting_id, status, error_message?, audio_s3_key? }

    Status transitions handled:
        bot_joining  → meeting.status = bot_joining
        recording    → meeting.status = recording, meeting.started_at = now
        processing   → meeting.status = processing,
                        meeting.audio_s3_key = audio_s3_key,
                        fire Kafka transcription task
        failed       → meeting.status = failed, logs error_message
    """

    authentication_classes = []
    permission_classes     = [IsInternalService]

    def post(self, request):
        # ── 1. Validate payload ───────────────────────────────────────────────
        serializer = BotStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        meeting_id    = data["meeting_id"]
        new_status    = data["status"]
        error_message = data.get("error_message", "")
        audio_s3_key  = data.get("audio_s3_key", "")

        # ── 3. Fetch meeting ──────────────────────────────────────────────────
        try:
            meeting = Meeting.objects.get(id=meeting_id)
        except Meeting.DoesNotExist:
            logger.error(
                "BotStatusView: meeting %s not found", meeting_id
            )
            return error_response(
                message    = "Meeting not found.",
                code       = "not_found",
                status_code = http_status.HTTP_404_NOT_FOUND,
            )

        # ── 4. Handle each status ─────────────────────────────────────────────
        update_fields = ["status", "updated_at"]

        if new_status == Meeting.Status.BOT_JOINING:
            meeting.status = Meeting.Status.BOT_JOINING
            logger.info(
                "BotStatusView: bot joining meeting %s", meeting_id
            )

        elif new_status == Meeting.Status.RECORDING:
            meeting.status = Meeting.Status.RECORDING
            if not meeting.started_at:
                meeting.started_at = timezone.now()
                update_fields.append("started_at")
            logger.info(
                "BotStatusView: bot recording meeting %s", meeting_id
            )

            # Notify the user that the bot has joined and recording has started
            notify_meeting_started.delay(
                user_id=str(meeting.created_by.id),
                meeting_id=str(meeting.id),
                meeting_title=meeting.title,
                workspace_id=str(meeting.organisation.id) if meeting.organisation else None,
            )

        elif new_status == Meeting.Status.PROCESSING:
            meeting.status = Meeting.Status.PROCESSING
            if audio_s3_key:
                meeting.audio_s3_key = audio_s3_key
                update_fields.append("audio_s3_key")

            # Set ended_at so duration is computed on save()
            meeting.ended_at = timezone.now()
            update_fields.append("ended_at")

            meeting.save(update_fields=update_fields)

            # Fire Whisper transcription pipeline
            _trigger_transcription(meeting)

            logger.info(
                "BotStatusView: meeting %s processing — transcription triggered",
                meeting_id,
            )
            return success_response(
                message    = "Bot status updated. Transcription triggered.",
                data       = {"meeting_id": str(meeting_id), "status": new_status},
                status_code = http_status.HTTP_200_OK,
            )

        elif new_status == Meeting.Status.FAILED:
            meeting.status = Meeting.Status.FAILED
            logger.error(
                "BotStatusView: bot failed for meeting %s — %s",
                meeting_id,
                error_message,
            )

            # Notify the user that the bot failed to join
            notify_bot_failed.delay(
                user_id=str(meeting.created_by.id),
                meeting_id=str(meeting.id),
                meeting_title=meeting.title,
                workspace_id=str(meeting.organisation.id) if meeting.organisation else None,
            )

        # ── 5. Save + respond ─────────────────────────────────────────────────
        meeting.save(update_fields=update_fields)

        return success_response(
            message    = "Bot status updated.",
            data       = {"meeting_id": str(meeting_id), "status": new_status},
            status_code = http_status.HTTP_200_OK,
        )


def _trigger_transcription(meeting: Meeting) -> None:
    """
    Fire a Kafka transcription task for the meeting's audio file.
    Called after the bot finishes recording and uploads audio to S3.
    """
    if not meeting.audio_s3_key:
        logger.warning(
            "_trigger_transcription: no audio_s3_key for meeting %s — skipping",
            meeting.id,
        )
        return

    try:
        from utils.kafka_producer import send_transcription_task

        send_transcription_task(
            meeting_id = str(meeting.id),
            file_path  = meeting.audio_s3_key,
            user_id    = str(meeting.created_by_id),
        )
        logger.info(
            "_trigger_transcription: transcription task sent for meeting %s",
            meeting.id,
        )
    except Exception as exc:
        logger.error(
            "_trigger_transcription: failed for meeting %s: %s",
            meeting.id,
            exc,
        )
