import json
import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import User
from meetings.models import Meeting, MeetingParticipant
from meetings.permissions import IsMeetingOwnerOrOrgAdmin
from meetings.serializers import (
    CreateMeetingSerializer,
    MeetingParticipantSerializer,
    CreateMeetingParticipantSerializer,
    MeetingSerializer,
    UpdateMeetingSerializer,
)
from meetings.utils import get_meeting_or_404, TenantQuerysetMixin
from utils.response import success_response, error_response
from utils.pagination import StandardPagination

logger = logging.getLogger("meetings")


# ------------ Meeting List + Create -----------------------------------------------

class MeetingListCreateView(APIView, TenantQuerysetMixin):
    """
    GET /api/v1/meetings/
    POST /api/v1/meetings/

    GET: Returns a list of meetings scoped to the user's organization or individual account (paginated).
    POST: Creates a new meeting for the authenticated user.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        meetings = self.get_meeting_queryset(request.user, request.organisation)
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(meetings, request)
        serializer = MeetingSerializer(page, many=True)
        
        return paginator.get_paginated_response(serializer.data)

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

        return success_response(
            message="Meeting created successfully.",
            data=MeetingSerializer(meeting).data,
            status_code=status.HTTP_201_CREATED,
        )


# ------------ Meeting Retrieve, Update, Delete -----------------------------------

class MeetingDetailView(APIView, TenantQuerysetMixin):
    """
    GET /api/v1/meetings/<meeting_id>/
    PATCH /api/v1/meetings/<meeting_id>/
    DELETE /api/v1/meetings/<meeting_id>/

    GET: Retrieves details of a specific meeting.
    PATCH: Updates specific fields of a meeting (only if editable).
    DELETE: Archives a meeting.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, meeting_id, request):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        if not meeting:
            return None, error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return meeting, None

    def get(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request)
        if err:
            return err
        return success_response(
            message="Meeting retrieved successfully.",
            data=MeetingSerializer(meeting).data,
            status_code=status.HTTP_200_OK,
        )

    def patch(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request)
        if err:
            return err

        # Check object-level permission
        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return error_response(
                message="You do not have permission to edit this meeting.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdateMeetingSerializer(
            meeting,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        logger.info("Meeting updated: %s by %s", meeting.id, request.user.email)

        return success_response(
            message="Meeting updated successfully.",
            data=MeetingSerializer(updated).data,
            status_code=status.HTTP_200_OK,
        )

    def delete(self, request, meeting_id):
        meeting, err = self.get_object(meeting_id, request)
        if err:
            return err

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return error_response(
                message="You do not have permission to delete this meeting.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        meeting.is_archived = True
        meeting.save(update_fields=["is_archived"])

        logger.info("Meeting archived: %s by %s", meeting.id, request.user.email)

        return success_response(
            message="Meeting deleted successfully.",
            data={},
            status_code=status.HTTP_200_OK,
        )


# ------------ Bot Dispatch -----------------------------------

class BotDispatchView(APIView, TenantQuerysetMixin):
    """
    POST /api/v1/meetings/<meeting_id>/bot/dispatch/

    Fires a Kafka stub message to the transcription_tasks topic.
    Sets meeting status to bot_joining.
    Playwright bot will consume this message.
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

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return error_response(
                message="You do not have permission to dispatch the bot.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if meeting.platform == Meeting.Platform.UPLOAD:
            return error_response(
                message="Bot dispatch is not available for manual upload meetings.",
                code="invalid_platform",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if meeting.status not in (
            Meeting.Status.SCHEDULED,
            Meeting.Status.FAILED,
        ):
            return error_response(
                message="Cannot dispatch bot — meeting is currently '%s'." % meeting.get_status_display(),
                code="invalid_status",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Fire bot task to Kafka — consumed by bot_service/worker.py
        try:
            from utils.kafka_producer import send_bot_task

            send_bot_task(
                meeting_id   = str(meeting.id),
                meeting_url  = meeting.meeting_url,
                platform     = meeting.platform,
                duration_cap = 3600,
            )
            logger.info(
                "Bot dispatch message sent to Kafka for meeting %s", meeting.id
            )
        except Exception as exc:
            logger.error(
                "Kafka bot dispatch failed for meeting %s: %s", meeting.id, exc
            )
            return error_response(
                message="Failed to dispatch bot. Please try again.",
                code="dispatch_failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Update status
        meeting.status = Meeting.Status.BOT_JOINING
        meeting.save(update_fields=["status"])

        return success_response(
            message="Bot dispatched successfully. It will join the meeting shortly.",
            data={
                "meeting_id": str(meeting.id),
                "status":     meeting.status,
            },
            status_code=status.HTTP_200_OK,
        )


# ------------ Participants -----------------------------------

class MeetingParticipantListCreateView(APIView, TenantQuerysetMixin):
    """
    GET /api/v1/meetings/<meeting_id>/participants/
    POST /api/v1/meetings/<meeting_id>/participants/

    GET: Returns a list of participants for a specific meeting.
    POST: Adds a new participant to a meeting.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_meeting(self, meeting_id, request):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        if not meeting:
            return None, error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return meeting, None

    def get(self, request, meeting_id):
        meeting, err = self.get_meeting(meeting_id, request)
        if err:
            return err

        participants = meeting.participants.select_related("user").order_by("joined_at", "id")
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(participants, request)
        serializer = MeetingParticipantSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)

    def post(self, request, meeting_id):
        meeting, err = self.get_meeting(meeting_id, request)
        if err:
            return err

        serializer = CreateMeetingParticipantSerializer(
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

        return success_response(
            message="Participant added successfully.",
            data=MeetingParticipantSerializer(participant).data,
            status_code=status.HTTP_201_CREATED,
        )


class MeetingParticipantDeleteView(APIView, TenantQuerysetMixin):
    """
    DELETE /api/v1/meetings/<meeting_id>/participants/<participant_id>/

    Removes a participant from a meeting.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, meeting_id, participant_id):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        if not meeting:
            return error_response(
                message="Meeting not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        permission = IsMeetingOwnerOrOrgAdmin()
        if not permission.has_object_permission(request, self, meeting):
            return error_response(
                message="You do not have permission to remove participants.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        try:
            participant = meeting.participants.get(id=participant_id)
        except MeetingParticipant.DoesNotExist:
            return error_response(
                message="Participant not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        participant.delete()
        logger.info(
            "Participant %s removed from meeting %s by %s",
            participant_id,
            meeting.id,
            request.user.email,
        )

        return success_response(
            message="Participant removed successfully.",
            data={},
            status_code=status.HTTP_200_OK,
        )
