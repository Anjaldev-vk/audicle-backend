from meetings.models import Meeting
from action_items.models import ActionItem
from transcripts.models import MeetingSummary
from analytics.tasks import (
    track_meeting_created,
    track_transcription_done,
    track_summary_done,
    track_action_item_created
)
import logging

# Disable logging to avoid clutter
logging.getLogger('analytics').setLevel(logging.WARNING)

meetings = Meeting.objects.all()
print(f"Backfilling analytics for {meetings.count()} meetings...")

for m in meetings:
    workspace_id = str(m.organisation.id) if m.organisation else str(m.created_by.id)
    # Track meeting created
    track_meeting_created.delay(
        meeting_id=str(m.id),
        user_id=str(m.created_by.id),
        workspace_id=workspace_id
    )
    
    # If completed, track transcription
    if m.status == 'completed':
        track_transcription_done.delay(
            meeting_id=str(m.id),
            user_id=str(m.created_by.id),
            workspace_id=workspace_id
        )

summaries = MeetingSummary.objects.filter(status='completed')
print(f"Backfilling analytics for {summaries.count()} summaries...")
for s in summaries:
    workspace_id = str(s.organisation.id) if s.organisation else str(s.created_by.id)
    track_summary_done.delay(
        meeting_id=str(s.meeting.id),
        user_id=str(s.created_by.id),
        workspace_id=workspace_id
    )

items = ActionItem.objects.all()
print(f"Backfilling analytics for {items.count()} action items...")
for i in items:
    workspace_id = str(i.organisation.id) if i.organisation else str(i.created_by.id)
    track_action_item_created.delay(
        action_item_id=str(i.id),
        user_id=str(i.created_by.id),
        workspace_id=workspace_id
    )

print("Backfill tasks queued!")
