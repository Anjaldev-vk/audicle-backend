from meetings.models import Meeting
from analytics.tasks import track_meeting_completed

meetings = Meeting.objects.filter(status='completed')
print(f"Repairing durations for {meetings.count()} meetings...")

for m in meetings:
    if m.started_at and m.ended_at:
        m.duration_seconds = int((m.ended_at - m.started_at).total_seconds())
        m.save(update_fields=['duration_seconds'])
        print(f"Updated meeting {m.title}: {m.duration_seconds}s")
        
        # Trigger analytics update for this meeting
        track_meeting_completed.delay(str(m.id))
    else:
        print(f"Skipping meeting {m.title}: Missing timestamps")

print("Repair complete and analytics queued!")
