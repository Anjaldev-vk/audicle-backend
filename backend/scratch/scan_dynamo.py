import boto3
from django.conf import settings

try:
    dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_S3_REGION)
    table = dynamodb.Table('audicle_analytics')
    
    response = table.scan(Limit=10)
    print(f"SUCCESS: Table scan found {response['Count']} items (sampled)")
    for item in response.get('Items', []):
         print(f"Item: {item}")

except Exception as e:
    print(f"FAILURE: {e}")
