from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .repository import create_notification
from .constants import NotificationType, NOTIFICATION_TITLES
import logging

logger = logging.getLogger(__name__)


def _push_via_websocket(user_id, notification):
    """Push notification to user's WebSocket channel group."""
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'notifications_{user_id}',
            {
                'type':         'notification.push',
                'notification': notification,
            }
        )
    except Exception as e:
        logger.error('WebSocket push failed for user %s: %s', user_id, e)


def _create_and_push(user_id, notification_type, message,
                     metadata=None, workspace_id=None):
    """Create notification in DynamoDB then push via WebSocket."""
    title = NOTIFICATION_TITLES.get(notification_type, 'Notification')
    notification = create_notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        message=message,
        metadata=metadata,
        workspace_id=workspace_id,
    )
    _push_via_websocket(user_id, notification)
    return notification


@shared_task(bind=True, max_retries=3)
def notify_meeting_started(self, user_id, meeting_id,
                           meeting_title, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.MEETING_STARTED,
            message='Bot has joined "%s"' % meeting_title,
            metadata={'meeting_id': meeting_id},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_meeting_started failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)


@shared_task(bind=True, max_retries=3)
def notify_transcription_done(self, user_id, meeting_id,
                              meeting_title, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.TRANSCRIPTION_DONE,
            message='Transcript is ready for "%s"' % meeting_title,
            metadata={'meeting_id': meeting_id},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_transcription_done failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)


@shared_task(bind=True, max_retries=3)
def notify_summary_done(self, user_id, meeting_id,
                        meeting_title, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.SUMMARY_DONE,
            message='Summary is ready for "%s"' % meeting_title,
            metadata={'meeting_id': meeting_id},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_summary_done failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)


@shared_task(bind=True, max_retries=3)
def notify_bot_failed(self, user_id, meeting_id,
                      meeting_title, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.BOT_FAILED,
            message='Bot could not join "%s". You can upload the recording manually.' % meeting_title,
            metadata={'meeting_id': meeting_id},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_bot_failed failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)


@shared_task(bind=True, max_retries=3)
def notify_member_joined(self, user_id, org_name,
                         member_name, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.MEMBER_JOINED,
            message='%s joined %s' % (member_name, org_name),
            metadata={'org_name': org_name},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_member_joined failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)


@shared_task(bind=True, max_retries=3)
def notify_invite_accepted(self, user_id, invitee_email,
                           org_name, workspace_id=None):
    try:
        _create_and_push(
            user_id=user_id,
            notification_type=NotificationType.INVITE_ACCEPTED,
            message='%s accepted your invite to %s' % (invitee_email, org_name),
            metadata={'invitee_email': invitee_email},
            workspace_id=workspace_id,
        )
    except Exception as exc:
        logger.error('notify_invite_accepted failed: %s', exc)
        raise self.retry(exc=exc, countdown=5)
