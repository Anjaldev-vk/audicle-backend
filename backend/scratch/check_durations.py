from meetings.models import Meeting
meetings = Meeting.objects.filter(status='completed')
print(f"Checking {meetings.count()} completed meetings...")
for m in meetings:
    print(f"Meeting: {m.title} | ID: {m.id} | Duration: {m.duration_seconds} | Started: {m.started_at} | Ended: {m.ended_at}")
