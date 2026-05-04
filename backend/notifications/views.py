from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from utils.response import success_response, error_response
from . import repository
import logging

logger = logging.getLogger(__name__)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = int(request.query_params.get('limit', 20))
        last_key = request.query_params.get('last_key')

        result = repository.get_notifications(
            user_id=request.user.id,
            limit=limit,
            last_key=last_key,
        )
        unread_count = repository.get_unread_count(request.user.id)

        return success_response(
            data={
                'results':      result['items'],
                'last_key':     result['last_key'],
                'unread_count': unread_count,
            },
            message='Notifications fetched',
        )


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, notification_id):
        sk = request.data.get('sk')
        if not sk:
            return error_response(code='missing_sk', message='sk is required')
        repository.mark_as_read(
            user_id=request.user.id,
            notification_id=notification_id,
            sk=sk,
        )
        return success_response(message='Marked as read')


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        count = repository.mark_all_as_read(request.user.id)
        return success_response(
            message='%s notifications marked as read' % count
        )


class NotificationDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, notification_id):
        sk = request.data.get('sk')
        if not sk:
            return error_response(code='missing_sk', message='sk is required')
        repository.delete_notification(
            user_id=request.user.id,
            sk=sk,
        )
        return success_response(message='Notification deleted')
