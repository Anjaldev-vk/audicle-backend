import boto3
import uuid
from datetime import datetime, timezone
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def _get_table():
    dynamodb = boto3.resource(
        'dynamodb',
        region_name=settings.DYNAMODB_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    return dynamodb.Table(settings.DYNAMODB_NOTIFICATIONS_TABLE)


def create_notification(user_id, notification_type, title, message,
                        metadata=None, workspace_id=None):
    """Write a new notification to DynamoDB."""
    table = _get_table()
    now = datetime.now(timezone.utc)
    notification_id = str(uuid.uuid4())
    sk = f"{now.isoformat()}#{notification_id}"

    item = {
        'user_id':   str(user_id),
        'sk':        sk,
        'id':        notification_id,
        'type':      notification_type,
        'title':     title,
        'message':   message,
        'is_read':   'false',           # stored as string for GSI
        'created_at': now.isoformat(),
        'metadata':  metadata or {},
        # TTL — auto-delete after 90 days
        'ttl': int(now.timestamp()) + (90 * 24 * 60 * 60),
    }

    if workspace_id:
        item['workspace_id'] = str(workspace_id)

    try:
        table.put_item(Item=item)
        logger.info('Notification created: %s for user %s', notification_type, user_id)
        return item
    except Exception as e:
        logger.error('Failed to create notification for user %s: %s', user_id, e)
        raise


def get_notifications(user_id, limit=20, last_key=None):
    """Fetch notifications for a user, newest first."""
    table = _get_table()

    kwargs = {
        'KeyConditionExpression': boto3.dynamodb.conditions.Key('user_id').eq(str(user_id)),
        'ScanIndexForward': False,     # newest first (descending sk)
        'Limit': limit,
    }
    if last_key:
        kwargs['ExclusiveStartKey'] = last_key

    try:
        response = table.query(**kwargs)
        return {
            'items': response.get('Items', []),
            'last_key': response.get('LastEvaluatedKey'),
        }
    except Exception as e:
        logger.error('Failed to fetch notifications for user %s: %s', user_id, e)
        raise


def get_unread_count(user_id):
    """Count unread notifications for a user."""
    table = _get_table()
    try:
        response = table.query(
            IndexName='user_id-is_read-index',
            KeyConditionExpression=(
                boto3.dynamodb.conditions.Key('user_id').eq(str(user_id)) &
                boto3.dynamodb.conditions.Key('is_read').eq('false')
            ),
            Select='COUNT',
        )
        return response.get('Count', 0)
    except Exception as e:
        logger.error('Failed to get unread count for user %s: %s', user_id, e)
        return 0


def mark_as_read(user_id, notification_id, sk):
    """Mark a single notification as read."""
    table = _get_table()
    try:
        table.update_item(
            Key={'user_id': str(user_id), 'sk': sk},
            UpdateExpression='SET is_read = :val',
            ExpressionAttributeValues={':val': 'true'},
        )
        logger.info('Notification %s marked as read', notification_id)
    except Exception as e:
        logger.error('Failed to mark notification %s as read: %s', notification_id, e)
        raise


def mark_all_as_read(user_id):
    """Mark all unread notifications as read for a user."""
    table = _get_table()
    try:
        # Fetch all unread first
        response = table.query(
            IndexName='user_id-is_read-index',
            KeyConditionExpression=(
                boto3.dynamodb.conditions.Key('user_id').eq(str(user_id)) &
                boto3.dynamodb.conditions.Key('is_read').eq('false')
            ),
        )
        items = response.get('Items', [])

        # Update each one
        with table.batch_writer() as batch:
            for item in items:
                table.update_item(
                    Key={'user_id': item['user_id'], 'sk': item['sk']},
                    UpdateExpression='SET is_read = :val',
                    ExpressionAttributeValues={':val': 'true'},
                )
        logger.info('Marked %s notifications as read for user %s', len(items), user_id)
        return len(items)
    except Exception as e:
        logger.error('Failed to mark all read for user %s: %s', user_id, e)
        raise


def delete_notification(user_id, sk):
    """Delete a single notification."""
    table = _get_table()
    try:
        table.delete_item(Key={'user_id': str(user_id), 'sk': sk})
        logger.info('Notification deleted for user %s', user_id)
    except Exception as e:
        logger.error('Failed to delete notification for user %s: %s', user_id, e)
        raise
