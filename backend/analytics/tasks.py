from celery import shared_task
from .repository import write_event
from .constants import EventType
import logging

logger = logging.getLogger(__name__)


def _get_workspace_id(meeting):
    """Return org id for org workspace, created_by id for personal."""
    if meeting.organisation:
        return str(meeting.organisation.id)
    return str(meeting.created_by.id)


@shared_task
def track_meeting_created(meeting_id):
    try:
        from meetings.models import Meeting
        meeting = Meeting.objects.select_related(
            'created_by', 'organisation'
        ).get(id=meeting_id)
        # 1. Update SQL usage counter (Immediate feedback on creation)
        if meeting.organisation:
            meeting.organisation.meetings_this_month += 1
            meeting.organisation.save(update_fields=['meetings_this_month'])
        else:
            meeting.created_by.meetings_this_month += 1
            meeting.created_by.save(update_fields=['meetings_this_month'])

        # 2. Write DynamoDB event
        write_event(
            workspace_id=_get_workspace_id(meeting),
            event_type=EventType.MEETING_CREATED,
            user_id=meeting.created_by.id,
            metadata={
                'meeting_id': str(meeting.id),
                'platform':   meeting.platform,
                'title':      meeting.title,
            },
        )
    except Exception as e:
        logger.error('track_meeting_created failed: %s', e)


@shared_task
def track_meeting_completed(meeting_id):
    try:
        from meetings.models import Meeting
        meeting = Meeting.objects.select_related(
            'created_by', 'organisation'
        ).get(id=meeting_id)

        # Write DynamoDB event (Usage was already incremented on creation)
        write_event(
            workspace_id=_get_workspace_id(meeting),
            event_type=EventType.MEETING_COMPLETED,
            user_id=meeting.created_by.id,
            metadata={
                'meeting_id':       str(meeting.id),
                'duration_seconds': meeting.duration_seconds or 0,
                'platform':         meeting.platform,
            },
        )
    except Exception as e:
        logger.error('track_meeting_completed failed: %s', e)


@shared_task
def track_bot_joined(meeting_id):
    try:
        from meetings.models import Meeting
        meeting = Meeting.objects.select_related(
            'created_by', 'organisation'
        ).get(id=meeting_id)
        write_event(
            workspace_id=_get_workspace_id(meeting),
            event_type=EventType.BOT_JOINED,
            user_id=meeting.created_by.id,
            metadata={'meeting_id': str(meeting.id)},
        )
    except Exception as e:
        logger.error('track_bot_joined failed: %s', e)


@shared_task
def track_transcription_done(meeting_id, user_id, workspace_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.TRANSCRIPTION_DONE,
            user_id=user_id,
            metadata={'meeting_id': meeting_id},
        )
    except Exception as e:
        logger.error('track_transcription_done failed: %s', e)


@shared_task
def track_summary_done(meeting_id, user_id, workspace_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.SUMMARY_DONE,
            user_id=user_id,
            metadata={'meeting_id': meeting_id},
        )
    except Exception as e:
        logger.error('track_summary_done failed: %s', e)


@shared_task
def track_action_item_created(action_item_id, user_id, workspace_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.ACTION_ITEM_CREATED,
            user_id=user_id,
            metadata={'action_item_id': action_item_id},
        )
    except Exception as e:
        logger.error('track_action_item_created failed: %s', e)


@shared_task
def track_action_item_completed(action_item_id, user_id, workspace_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.ACTION_ITEM_COMPLETED,
            user_id=user_id,
            metadata={'action_item_id': action_item_id},
        )
    except Exception as e:
        logger.error('track_action_item_completed failed: %s', e)


@shared_task
def track_rag_query(user_id, workspace_id, session_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.RAG_QUERY,
            user_id=user_id,
            metadata={'session_id': session_id},
        )
    except Exception as e:
        logger.error('track_rag_query failed: %s', e)


@shared_task
def track_member_joined(user_id, workspace_id):
    try:
        write_event(
            workspace_id=workspace_id,
            event_type=EventType.MEMBER_JOINED,
            user_id=user_id,
            metadata={},
        )
    except Exception as e:
        logger.error('track_member_joined failed: %s', e)