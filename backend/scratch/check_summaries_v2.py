from transcripts.models import MeetingSummary
from action_items.models import ActionItem
summaries = MeetingSummary.objects.filter(status='completed')
print(f'Completed Summaries: {summaries.count()}')
for s in summaries:
    ai_count = len(s.action_items) if s.action_items else 0
    org_id = s.organisation.id if s.organisation else 'None'
    user_id = s.created_by.id if s.created_by else 'None'
    meeting_org_id = s.meeting.organisation.id if s.meeting.organisation else 'None'
    print(f'Summary ID: {s.id}, AI Count: {ai_count}, Org: {org_id}, User: {user_id}, Meeting Org: {meeting_org_id}')

action_items = ActionItem.objects.all()
print(f'Total Action Items in DB: {action_items.count()}')
