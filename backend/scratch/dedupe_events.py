import boto3
from django.conf import settings
from boto3.dynamodb.conditions import Key
from analytics.constants import EventType
from meetings.models import Meeting
from analytics.tasks import track_meeting_completed

dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_S3_REGION)
table = dynamodb.Table('audicle_analytics')

uids = ['5b92e106-a5c1-4f2c-a377-3c3043ee6c0b', '3a70e79f-ce9a-4865-9701-bb575b292282', '945faf66-d49f-4ff2-b3b0-11dc165eeba6']

print("Deduplicating meeting_completed events...")

for uid in uids:
    response = table.query(
        IndexName='workspace_id-event_type-index',
        KeyConditionExpression=Key('workspace_id').eq(uid) & Key('event_type').eq(EventType.MEETING_COMPLETED)
    )
    items = response.get('Items', [])
    print(f"User {uid}: Found {len(items)} completion events.")
    
    # Delete all
    for item in items:
        table.delete_item(Key={'workspace_id': item['workspace_id'], 'sk': item['sk']})
    
    # Re-backfill correctly for this user
    meetings = Meeting.objects.filter(created_by__id=uid, status='completed')
    print(f"Re-backfilling {meetings.count()} meetings...")
    for m in meetings:
        track_meeting_completed.delay(str(m.id))

print("Cleanup and re-backfill triggered!")
