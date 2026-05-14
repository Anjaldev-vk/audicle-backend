import json
import logging
import hashlib
import hmac
import os
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from accounts.models import User
from meetings.models import Meeting, MeetingParticipant, MeetingTemplate
from meetings.permissions import IsMeetingOwnerOrOrgAdmin
from meetings.serializers import (
    CreateMeetingSerializer,
    MeetingParticipantSerializer,
    CreateMeetingParticipantSerializer,
    MeetingSerializer,
    UpdateMeetingSerializer,
    MeetingTemplateSerializer,
)
from meetings.utils import get_meeting_or_404, TenantQuerysetMixin
from utils.response import success_response, error_response
from utils.pagination import StandardPagination
from analytics.tasks import track_meeting_created, track_meeting_completed, track_bot_joined
from utils.plan_limits import check_bot_access, check_meeting_limit
from notifications.service import send_notification

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
        # ── Plan limit check ──────────────────────────────────
        limit_error = check_meeting_limit(request)
        if limit_error:
            return limit_error
        # ── End limit check ───────────────────────────────────

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

        track_meeting_created.delay(str(meeting.id))

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
        # ── Plan limit check ──────────────────────────────────
        limit_error = check_bot_access(request)
        if limit_error:
            return limit_error
        # ── End limit check ───────────────────────────────────

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
        meeting.save(update_fields=["status", "updated_at"])

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


class MeetingTemplateListCreateView(APIView):
    """GET + POST /api/v1/meetings/templates/"""
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        if request.organisation:
            qs = MeetingTemplate.objects.filter(
                organisation=request.organisation
            )
        else:
            qs = MeetingTemplate.objects.filter(
                organisation=None,
                created_by=request.user,
            )
        qs = qs.select_related('created_by').order_by('name')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = MeetingTemplateSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


    def post(self, request):
        serializer = MeetingTemplateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid data',
                errors=serializer.errors,
                status_code=400,
            )
        template = serializer.save(
            created_by=request.user,
            organisation=request.organisation,
        )
        logger.info(
            'Meeting template %s created by user %s', template.id, request.user.id
        )
        return success_response(
            data=MeetingTemplateSerializer(template).data,
            message='Template created',
            status_code=201,
        )


class MeetingTemplateDeleteView(APIView):
    """DELETE /api/v1/meetings/templates/<id>/"""
    permission_classes = [IsAuthenticated]

    def delete(self, request, template_id):
        try:
            if request.organisation:
                template = MeetingTemplate.objects.get(
                    id=template_id,
                    organisation=request.organisation,
                )
            else:
                template = MeetingTemplate.objects.get(
                    id=template_id,
                    organisation=None,
                    created_by=request.user,
                )
        except MeetingTemplate.DoesNotExist:
            return error_response(code='not_found', message='Template not found', status_code=404)

        template.delete()
        logger.info(
            'Meeting template %s deleted by user %s', template_id, request.user.id
        )
        return success_response(message='Template deleted')


# ------------ Recall.ai Webhook ----------------------------------------------------

@csrf_exempt
@require_POST
def recall_webhook(request):
    """
    Endpoint for Recall.ai to push bot status updates.
    """
    RECALL_WEBHOOK_SECRET = os.environ.get('RECALL_WEBHOOK_SECRET')
    
    # 1. Verify it's really from Recall.ai
    if RECALL_WEBHOOK_SECRET:
        signature = request.headers.get('X-Recall-Signature', '')
        expected = hmac.new(
            RECALL_WEBHOOK_SECRET.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning("Recall Webhook: Invalid signature")
            return JsonResponse({'error': 'Invalid signature'}, status=401)

    # 2. Parse event
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
    event = data.get('event')
    bot_data = data.get('data', {})
    bot_id = bot_data.get('bot_id')

    if not bot_id:
        return JsonResponse({'error': 'No bot_id provided'}, status=400)

    # 3. Handle status changes
    if event == 'bot.status_change':
        status = bot_data.get('status', {}).get('code')
        _handle_status_change(bot_id, status)

    return JsonResponse({'ok': True})


def _handle_status_change(bot_id: str, status: str):
    meeting = Meeting.objects.filter(recall_bot_id=bot_id).first()
    if not meeting:
        logger.warning("_handle_status_change: No meeting found for recall_bot_id %s", bot_id)
        return

    # Map Recall status → your Meeting status
    status_map = {
        'in_call_recording': Meeting.Status.RECORDING,
        'call_ended':        Meeting.Status.PROCESSING,
        'done':              Meeting.Status.COMPLETED,
        'fatal':             Meeting.Status.FAILED,
        'kicked':            Meeting.Status.FAILED,
        'waiting_room_timeout': Meeting.Status.FAILED,
    }

    mapped = status_map.get(status)
    if not mapped:
        return

    meeting.status = mapped
    meeting.save()
    
    logger.info("Recall Webhook: Meeting %s status updated to %s (bot %s)", meeting.id, mapped, bot_id)

    # Trigger audio download when call ends
    if mapped == Meeting.Status.PROCESSING:
        from meetings.tasks import download_and_upload_audio
        download_and_upload_audio.delay(bot_id, str(meeting.id))
        
        send_notification(
            user_id=str(meeting.created_by.id),
            notification_type='MEETING_COMPLETED',
            payload={
                'meeting_id': str(meeting.id),
                'title': meeting.title,
                'message': f'Your meeting "{meeting.title}" has ended. Transcription is starting.',
            }
        )
