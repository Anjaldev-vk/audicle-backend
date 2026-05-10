import boto3
from django.conf import settings
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_S3_REGION)
table = dynamodb.Table('audicle_analytics')

users = ['5b92e106-a5c1-4f2c-a377-3c3043ee6c0b', '3a70e79f-ce9a-4865-9701-bb575b292282']

for uid in users:
    response = table.query(
        KeyConditionExpression=Key('workspace_id').eq(uid)
    )
    print(f"Deleting {response['Count']} items for user {uid}...")
    for item in response.get('Items', []):
        table.delete_item(Key={'workspace_id': item['workspace_id'], 'sk': item['sk']})

print("Cleanup complete!")
