import json
import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from meetings.models import Meeting, MeetingParticipant
from meetings.permissions import IsMeetingOwnerOrOrgAdmin
from meetings.serializers import (
    CreateMeetingSerializer,
    MeetingParticipantSerializer,
    MeetingSerializer,
    UpdateMeetingSerializer,
)
from meetings.utils import get_meeting_or_404, get_meeting_queryset

logger = logging.getLogger("meetings")


# ------------ Meeting List + Create -----------------------------------------------

class MeetingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        meetings = get_meeting_queryset(request.user)
        serializer = MeetingSerializer(meetings, many=True)
        return Response(
            {
                "success": True,
                "message": "Meetings retrieved successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = CreateMeetingSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        meeting = serializer.save()

        logger.info(
            "Meeting created: %s by user %s",
            meeting.id,
            request.user.email,
        )

        return Response(
            {
                "success": True,
                "message": "Meeting created successfully.",
                "data": MeetingSerializer(meeting).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ------------ Meeting Retrieve, Update, Delete -----------------------------------

class MeetingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, meeting_id, user):
        meeting = get_meeting_or_404(meeting_id, user)
        if not meeting:
            return None, Response(
                {
                    "status": "error",
                    "code": "not_found",
                    "message": "Meeting not found.",
                    "errors": {},
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return meeting, None

    def get(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request.user)
        if err:
            return err
        return Response(
            {
                "success": True,
                "message": "Meeting retrieved successfully.",
                "data": MeetingSerializer(meeting).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request.user)
        if err:
            return err

        # Check object-level permission
        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return Response(
                {
                    "status": "error",
                    "code": "permission_denied",
                    "message": "You do not have permission to edit this meeting.",
                    "errors": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdateMeetingSerializer(
            meeting,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        logger.info("Meeting updated: %s by %s", meeting.id, request.user.email)

        return Response(
            {
                "success": True,
                "message": "Meeting updated successfully.",
                "data": MeetingSerializer(updated).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request.user)
        if err:
            return err

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return Response(
                {
                    "status": "error",
                    "code": "permission_denied",
                    "message": "You do not have permission to delete this meeting.",
                    "errors": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        meeting.is_archived = True
        meeting.save(update_fields=["is_archived"])

        logger.info("Meeting archived: %s by %s", meeting.id, request.user.email)

        return Response(
            {
                "success": True,
                "message": "Meeting deleted successfully.",
                "data": {},
            },
            status=status.HTTP_200_OK,
        )


# ------------ Bot Dispatch -----------------------------------

class BotDispatchView(APIView):
    """
    POST /api/v1/meetings/<id>/bot/dispatch/

    Fires a Kafka stub message to the transcription_tasks topic.
    Sets meeting status to bot_joining.
    Playwright bot (Phase 9) will consume this message.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        meeting = get_meeting_or_404(meeting_id, request.user)
        if not meeting:
            return Response(
                {
                    "status": "error",
                    "code": "not_found",
                    "message": "Meeting not found.",
                    "errors": {},
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return Response(
                {
                    "status": "error",
                    "code": "permission_denied",
                    "message": "You do not have permission to dispatch the bot.",
                    "errors": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if meeting.platform == Meeting.Platform.UPLOAD:
            return Response(
                {
                    "status": "error",
                    "code": "invalid_platform",
                    "message": "Bot dispatch is not available for manual upload meetings.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if meeting.status not in (
            Meeting.Status.SCHEDULED,
            Meeting.Status.FAILED,
        ):
            return Response(
                {
                    "status": "error",
                    "code": "invalid_status",
                    "message": (
                        f"Cannot dispatch bot — meeting is currently "
                        f"'{meeting.get_status_display()}'."
                    ),
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fire Kafka stub message
        try:
            from utils.kafka_producer import send_transcription_task

            payload = {
                "meeting_id":   str(meeting.id),
                "action":       "join",
                "platform":     meeting.platform,
                "meeting_url":  meeting.meeting_url,
                "scheduled_at": (
                    meeting.scheduled_at.isoformat()
                    if meeting.scheduled_at else None
                ),
                "user_id":      str(request.user.id),
            }
            send_transcription_task(
                meeting_id=str(meeting.id),
                file_path=None,           # no file yet — bot will produce this
                user_id=str(request.user.id),
            )
            logger.info(
                "Kafka bot dispatch message sent for meeting %s", meeting.id
            )
        except Exception as exc:
            logger.error(
                "Kafka dispatch failed for meeting %s: %s", meeting.id, exc
            )
            return Response(
                {
                    "status": "error",
                    "code": "dispatch_failed",
                    "message": "Failed to dispatch bot. Please try again.",
                    "errors": {},
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Update status
        meeting.status = Meeting.Status.BOT_JOINING
        meeting.save(update_fields=["status"])

        return Response(
            {
                "success": True,
                "message": "Bot dispatched successfully. It will join the meeting shortly.",
                "data": {
                    "meeting_id": str(meeting.id),
                    "status":     meeting.status,
                },
            },
            status=status.HTTP_200_OK,
        )


# ------------ Participants -----------------------------------

class MeetingParticipantListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_meeting(self, meeting_id, user):
        meeting = get_meeting_or_404(meeting_id, user)
        if not meeting:
            return None, Response(
                {
                    "status": "error",
                    "code": "not_found",
                    "message": "Meeting not found.",
                    "errors": {},
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return meeting, None

    def get(self, request, meeting_id):
        meeting, err = self.get_meeting(meeting_id, request.user)
        if err:
            return err

        participants = meeting.participants.select_related("user").all()
        serializer   = MeetingParticipantSerializer(participants, many=True)

        return Response(
            {
                "success": True,
                "message": "Participants retrieved successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, meeting_id):
        meeting, err = self.get_meeting(meeting_id, request.user)
        if err:
            return err

        serializer = MeetingParticipantSerializer(
            data=request.data,
            context={"meeting": meeting},
        )
        serializer.is_valid(raise_exception=True)
        participant = serializer.save(meeting=meeting)

        logger.info(
            "Participant %s added to meeting %s",
            participant.email,
            meeting.id,
        )

        return Response(
            {
                "success": True,
                "message": "Participant added successfully.",
                "data": MeetingParticipantSerializer(participant).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MeetingParticipantDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, meeting_id, participant_id):
        meeting = get_meeting_or_404(meeting_id, request.user)
        if not meeting:
            return Response(
                {
                    "status": "error",
                    "code": "not_found",
                    "message": "Meeting not found.",
                    "errors": {},
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return Response(
                {
                    "status": "error",
                    "code": "permission_denied",
                    "message": "You do not have permission to remove participants.",
                    "errors": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            participant = meeting.participants.get(id=participant_id)
        except MeetingParticipant.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "not_found",
                    "message": "Participant not found.",
                    "errors": {},
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        participant.delete()
        logger.info(
            "Participant %s removed from meeting %s by %s",
            participant_id,
            meeting.id,
            request.user.email,
        )

        return Response(
            {
                "success": True,
                "message": "Participant removed successfully.",
                "data": {},
            },
            status=status.HTTP_200_OK,
        )
