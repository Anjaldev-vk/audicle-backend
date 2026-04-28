from rest_framework import serializers

from meetings.models import Meeting


class BotStatusSerializer(serializers.Serializer):
    """
    Validates POST /internal/bot/status/ payloads sent by bot_service/bot_runner.py.

    Possible statuses:
        bot_joining — bot is attempting to enter the meeting
        recording   — bot is inside and recording
        processing  — bot finished; audio uploaded to S3; triggers transcription
        failed      — something went wrong
    """

    VALID_STATUSES = [
        Meeting.Status.BOT_JOINING,
        Meeting.Status.RECORDING,
        Meeting.Status.PROCESSING,
        Meeting.Status.FAILED,
    ]

    meeting_id    = serializers.UUIDField()
    status        = serializers.ChoiceField(choices=VALID_STATUSES)
    error_message = serializers.CharField(required=False, allow_blank=True, default="")
    audio_s3_key  = serializers.CharField(required=False, allow_blank=True, default="")
