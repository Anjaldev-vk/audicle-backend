from .tasks import _create_and_push

def send_notification(user_id, notification_type, payload):
    """
    Wrapper for creating and pushing notifications.
    Matches the signature expected by the webhook view.
    """
    message = payload.get('message', '')
    metadata = payload.get('metadata', {})
    # Add meeting_id to metadata if it's in the payload but not metadata
    if 'meeting_id' in payload and 'meeting_id' not in metadata:
        metadata['meeting_id'] = payload['meeting_id']
        
    workspace_id = payload.get('workspace_id')
    
    return _create_and_push(
        user_id=user_id,
        notification_type=notification_type,
        message=message,
        metadata=metadata,
        workspace_id=workspace_id
    )
