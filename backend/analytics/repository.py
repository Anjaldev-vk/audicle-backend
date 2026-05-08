import boto3
import uuid
from datetime import datetime, timezone, timedelta
from django.conf import settings
from boto3.dynamodb.conditions import Key
import logging

logger = logging.getLogger(__name__)


def _get_table():
    dynamodb = boto3.resource(
        'dynamodb',
        region_name=settings.DYNAMODB_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    return dynamodb.Table(settings.DYNAMODB_ANALYTICS_TABLE)


def write_event(
    workspace_id,
    event_type,
    user_id,
    metadata=None,
):
    """
    Write an analytics event to DynamoDB.
    workspace_id: org id for org workspace, user id for personal
    """
    table = _get_table()
    now   = datetime.now(timezone.utc)
    event_id = str(uuid.uuid4())
    sk = '%s#%s#%s' % (event_type, now.isoformat(), event_id)

    item = {
        'workspace_id': str(workspace_id),
        'sk':           sk,
        'id':           event_id,
        'event_type':   event_type,
        'user_id':      str(user_id),
        'created_at':   now.isoformat(),
        'metadata':     metadata or {},
        # TTL — auto-expire after 1 year
        'ttl': int(now.timestamp()) + (365 * 24 * 60 * 60),
    }

    try:
        table.put_item(Item=item)
        logger.info(
            'Analytics event written: %s for workspace %s',
            event_type, workspace_id,
        )
    except Exception as e:
        logger.error(
            'Failed to write analytics event %s: %s',
            event_type, e,
        )
        raise


def query_events(
    workspace_id,
    event_type=None,
    days=30,
    limit=1000,
):
    """
    Query events for a workspace within the last N days.
    Optionally filter by event_type using GSI.
    """
    table     = _get_table()
    since     = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since.isoformat()

    try:
        if event_type:
            # Use GSI: workspace_id-event_type-index
            response = table.query(
                IndexName='workspace_id-event_type-index',
                KeyConditionExpression=(
                    Key('workspace_id').eq(str(workspace_id)) &
                    Key('event_type').eq(event_type)
                ),
                FilterExpression=(
                    boto3.dynamodb.conditions.Attr('created_at').gte(since_str)
                ),
                Limit=limit,
                ScanIndexForward=False,
            )
        else:
            # Query all events for workspace
            response = table.query(
                KeyConditionExpression=(
                    Key('workspace_id').eq(str(workspace_id)) &
                    Key('sk').gte(since_str)
                ),
                Limit=limit,
                ScanIndexForward=False,
            )
        return response.get('Items', [])
    except Exception as e:
        logger.error(
            'Failed to query analytics for workspace %s: %s',
            workspace_id, e,
        )
        return []


def query_events_by_user(user_id, days=30, limit=500):
    """Query all events by a specific user using GSI."""
    table     = _get_table()
    since     = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since.isoformat()

    try:
        response = table.query(
            IndexName='user_id-sk-index',
            KeyConditionExpression=(
                Key('user_id').eq(str(user_id)) &
                Key('sk').gte(since_str)
            ),
            Limit=limit,
            ScanIndexForward=False,
        )
        return response.get('Items', [])
    except Exception as e:
        logger.error(
            'Failed to query analytics for user %s: %s',
            user_id, e,
        )
        return []


def count_events(workspace_id, event_type, days=30):
    """Count events of a specific type for a workspace."""
    events = query_events(
        workspace_id=workspace_id,
        event_type=event_type,
        days=days,
    )
    return len(events)


def group_by_day(events):
    """
    Group events by date.
    Returns dict: {'2026-05-01': 5, '2026-05-02': 3, ...}
    """
    counts = {}
    for event in events:
        date_str = event['created_at'][:10]  # YYYY-MM-DD
        counts[date_str] = counts.get(date_str, 0) + 1
    return counts


def group_by_user(events):
    """
    Group events by user_id.
    Returns dict: {'user_id': count, ...}
    """
    counts = {}
    for event in events:
        uid = event['user_id']
        counts[uid] = counts.get(uid, 0) + 1
    return counts


def average_metadata_value(events, key):
    """
    Average a numeric metadata field across events.
    e.g. average duration_seconds across meeting_completed events.
    """
    values = [
        float(e['metadata'].get(key, 0))
        for e in events
        if e.get('metadata', {}).get(key) is not None
    ]
    if not values:
        return 0
    return round(sum(values) / len(values), 2)