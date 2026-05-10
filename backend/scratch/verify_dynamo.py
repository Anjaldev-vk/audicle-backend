import boto3
from django.conf import settings
from boto3.dynamodb.conditions import Key

try:
    dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_S3_REGION)
    table = dynamodb.Table('audicle_analytics')
    
    # Query for a known user/workspace
    workspace_id = '5b92e106-a5c1-4f2c-a377-3c3043ee6c0b'
    response = table.query(
        IndexName='workspace_id-event_type-index',
        KeyConditionExpression=Key('workspace_id').eq(workspace_id)
    )
    
    print(f"SUCCESS: Found {response['Count']} events for workspace {workspace_id}")
    for item in response.get('Items', []):
        print(f"Event: {item.get('event_type')} at {item.get('timestamp')}")

except Exception as e:
    print(f"FAILURE: {e}")
