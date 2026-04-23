import logging

from rest_framework import serializers

from accounts.models import User
from meetings.models import Meeting, MeetingParticipant

logger = logging.getLogger("meetings")


class MeetingParticipantSerializer(serializers.ModelSerializer):

    class Meta:
        model  = MeetingParticipant
        fields = [
            "id", "user", "email", "name",
            "role", "joined_at", "left_at",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_email(self, value):
        """Normalize email and ensure no duplicate participant email per meeting."""
        if value:
            value = value.lower().strip()
            
        meeting = self.context.get("meeting")
        if meeting:
            qs = MeetingParticipant.objects.filter(
                meeting=meeting,
                email=value,
            )
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError(
                    "A participant with this email already exists in this meeting."
                )
        return value


class CreateMeetingParticipantSerializer(MeetingParticipantSerializer):
    """
    Serializer for adding a participant to a meeting.
    """
    class Meta(MeetingParticipantSerializer.Meta):
        fields = ["email", "name", "role"]


class MeetingSerializer(serializers.ModelSerializer):
    participants = MeetingParticipantSerializer(many=True, read_only=True)
    created_by   = serializers.SerializerMethodField()
    status       = serializers.CharField(read_only=True)

    class Meta:
        model  = Meeting
        fields = [
            "id", "organisation", "created_by",
            "title", "description", "platform",
            "meeting_url", "status",
            "scheduled_at", "started_at", "ended_at",
            "duration_seconds",
            "audio_s3_key", "video_s3_key",
            "is_archived", "is_live", "is_editable",
            "participants",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organisation", "created_by",
            "status", "started_at", "ended_at",
            "duration_seconds", "audio_s3_key", "video_s3_key",
            "is_archived", "is_live", "is_editable",
            "created_at", "updated_at",
        ]

    def get_created_by(self, obj):
        return {
            "id":    str(obj.created_by.id),
            "email": obj.created_by.email,
            "name":  obj.created_by.full_name,
        }


class CreateMeetingSerializer(serializers.ModelSerializer):

    class Meta:
        model  = Meeting
        fields = [
            "title", "description", "platform",
            "meeting_url", "scheduled_at",
        ]

    def validate(self, attrs):
        platform    = attrs.get("platform")
        meeting_url = attrs.get("meeting_url")

        # URL required for bot-joinable platforms
        if platform in (
            Meeting.Platform.ZOOM,
            Meeting.Platform.GOOGLE_MEET,
            Meeting.Platform.TEAMS,
        ) and not meeting_url:
            raise serializers.ValidationError(
                {"meeting_url": "A meeting URL is required for this platform."}
            )

        # Upload platform must not have a URL
        if platform == Meeting.Platform.UPLOAD and meeting_url:
            raise serializers.ValidationError(
                {"meeting_url": "Manual upload meetings do not require a URL."}
            )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        user    = request.user

        return Meeting.objects.create(
            **validated_data,
            created_by=user,
            organisation=user.organisation,   # None for individual users
        )


class UpdateMeetingSerializer(serializers.ModelSerializer):
    """
    Only allows updating fields that make sense post-creation.
    Platform is locked. Status is controlled by actions only.
    """

    class Meta:
        model  = Meeting
        fields = ["title", "description", "meeting_url", "scheduled_at"]

    def validate(self, attrs):
        if not self.instance.is_editable:
            raise serializers.ValidationError(
                "Only scheduled meetings can be edited."
            )
        return attrs
