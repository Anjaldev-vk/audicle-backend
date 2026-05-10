import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from meetings.models import Meeting
from action_items.models import ActionItem
from transcripts.models import MeetingSummary

print(f"Total Meetings: {Meeting.objects.count()}")
print(f"Total Action Items: {ActionItem.objects.count()}")
print(f"Total Summaries: {MeetingSummary.objects.count()}")

# Check status of meetings
from django.db.models import Count
print("\nMeeting Status Counts:")
for m in Meeting.objects.values('status').annotate(count=Count('id')):
    print(f"{m['status']}: {m['count']}")
