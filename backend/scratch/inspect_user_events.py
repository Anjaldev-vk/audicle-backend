import boto3
from django.conf import settings
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_S3_REGION)
table = dynamodb.Table('audicle_analytics')

uid = '5b92e106-a5c1-4f2c-a377-3c3043ee6c0b' # anjaldev.aiuse
response = table.query(
    IndexName='workspace_id-event_type-index',
    KeyConditionExpression=Key('workspace_id').eq(uid)
)
print(f"User {uid} Total Events: {response['Count']}")
for item in response.get('Items', []):
    print(f"Event: {item.get('event_type')} | Date: {item.get('created_at')} | Metadata: {item.get('metadata')}")
