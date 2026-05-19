from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
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

        try:
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
        except Exception as e:
            logger.error('NotificationListView error: %s', e)
            return error_response(
                code='notification_error',
                message='Failed to fetch notifications. Please check IAM permissions.',
                status_code=502,
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


@method_decorator(csrf_exempt, name='dispatch')
class InternalNotificationPushView(APIView):
    permission_classes = []  # No auth — uses internal secret

    def post(self, request):
        # Verify internal secret
        secret = request.headers.get('X-Internal-Secret')
        if secret != settings.INTERNAL_API_SECRET:
            return Response({'error': 'Unauthorized'}, status=401)

        user_id = request.data.get('user_id')
        notification = request.data.get('notification', {})

        if not user_id:
            return Response({'error': 'user_id required'}, status=400)

        try:
            # Push via WebSocket
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'notifications_{user_id}',
                {
                    'type': 'notification.push',
                    'notification': notification,
                }
            )
            logger.info('Lambda WebSocket push successful for user %s', user_id)
            return Response({'status': 'pushed'})
        except Exception as e:
            logger.error('Lambda WebSocket push failed: %s', e)
            return Response({'error': str(e)}, status=500)
