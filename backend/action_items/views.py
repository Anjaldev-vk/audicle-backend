from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from utils.response import success_response, error_response
from utils.pagination import StandardPagination
from meetings.utils import get_meeting_or_404
from .models import ActionItem
from .serializers import (
    ActionItemSerializer,
    ActionItemCreateSerializer,
    ActionItemUpdateSerializer,
)
import logging

logger = logging.getLogger(__name__)


class MeetingActionItemListCreateView(APIView):
    """GET + POST /api/v1/meetings/<meeting_id>/action-items/"""
    permission_classes = [IsAuthenticated]

    def _get_meeting(self, meeting_id, request):
        meeting = get_meeting_or_404(meeting_id, request.user, request.organisation)
        return meeting

    def get(self, request, meeting_id):
        meeting = self._get_meeting(meeting_id, request)
        if meeting is None:
            return error_response(code='not_found', message='Meeting not found', status_code=404)

        qs = (
            ActionItem.objects
            .filter(meeting=meeting)
            .select_related('assigned_to', 'created_by', 'meeting')
            .order_by('created_at')
        )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ActionItemSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request, meeting_id):
        meeting = self._get_meeting(meeting_id, request)
        if meeting is None:
            return error_response(code='not_found', message='Meeting not found', status_code=404)

        serializer = ActionItemCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid data',
                errors=serializer.errors,
                status_code=400,
            )

        with transaction.atomic():
            item = serializer.save(
                meeting=meeting,
                organisation=request.organisation,
                created_by=request.user,
                source=ActionItem.Source.MANUAL,
            )

        logger.info('Action item %s created by user %s', item.id, request.user.id)
        return success_response(
            data=ActionItemSerializer(item).data,
            message='Action item created',
            status_code=201,
        )


class ActionItemDetailView(APIView):
    """PATCH + DELETE /api/v1/action-items/<item_id>/"""
    permission_classes = [IsAuthenticated]

    def _get_item(self, item_id, request):
        try:
            if request.organisation:
                return ActionItem.objects.get(
                    id=item_id,
                    organisation=request.organisation,
                )
            return ActionItem.objects.get(
                id=item_id,
                organisation=None,
                created_by=request.user,
            )
        except ActionItem.DoesNotExist:
            return None

    def patch(self, request, item_id):
        item = self._get_item(item_id, request)
        if item is None:
            return error_response(code='not_found', message='Action item not found', status_code=404)

        serializer = ActionItemUpdateSerializer(
            item, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return error_response(
                code='validation_error',
                message='Invalid data',
                errors=serializer.errors,
                status_code=400,
            )

        serializer.save()
        logger.info('Action item %s updated by user %s', item_id, request.user.id)
        return success_response(
            data=ActionItemSerializer(item).data,
            message='Action item updated',
        )

    def delete(self, request, item_id):
        item = self._get_item(item_id, request)
        if item is None:
            return error_response(code='not_found', message='Action item not found', status_code=404)

        item.delete()
        logger.info('Action item %s deleted by user %s', item_id, request.user.id)
        return success_response(message='Action item deleted')


class ActionItemCrossView(APIView):
    """GET /api/v1/action-items/ — cross-meeting, workspace scoped"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status_filter = request.query_params.get('status')
        assigned_to_me = request.query_params.get('assigned_to_me')

        if request.organisation:
            qs = ActionItem.objects.filter(organisation=request.organisation)
        else:
            qs = ActionItem.objects.filter(
                organisation=None,
                created_by=request.user,
            )

        qs = qs.select_related('meeting', 'assigned_to', 'created_by')

        if status_filter in ActionItem.Status.values:
            qs = qs.filter(status=status_filter)

        if assigned_to_me == 'true':
            qs = qs.filter(assigned_to=request.user)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ActionItemSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
