from django.utils import timezone
from datetime import timedelta
from accounts.models import User
from meetings.models import Meeting
from analytics.tasks import track_meeting_created, track_meeting_completed
from analytics.repository import query_events
from analytics.constants import EventType
import logging

logging.getLogger('analytics').setLevel(logging.WARNING)

now = timezone.now()
start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

print("Starting Master Repair...")

for user in User.objects.all():
    # 1. Recount meetings for the month
    this_month_count = Meeting.objects.filter(
        created_by=user,
        created_at__gte=start_of_month
    ).count()
    
    if user.meetings_this_month != this_month_count:
        print(f"Updating {user.email} count: {user.meetings_this_month} -> {this_month_count}")
        user.meetings_this_month = this_month_count
        user.save(update_fields=['meetings_this_month'])

    # 2. Sync all completed meetings to Analytics
    meetings = Meeting.objects.filter(created_by=user, status='completed')
    if meetings.exists():
        print(f"Syncing {meetings.count()} completed meetings for {user.email}...")
        for m in meetings:
            # Ensure duration is calculated if possible
            if not m.duration_seconds and m.started_at and m.ended_at:
                m.duration_seconds = int((m.ended_at - m.started_at).total_seconds())
                m.save(update_fields=['duration_seconds'])
            
            # Re-trigger completion event
            track_meeting_completed.delay(str(m.id))
            # Also ensure creation event exists
            track_meeting_created.delay(str(m.id))

print("Master Repair Complete!")
