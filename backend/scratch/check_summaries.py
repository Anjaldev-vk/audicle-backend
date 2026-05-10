from transcripts.models import MeetingSummary
summaries = MeetingSummary.objects.filter(status='completed')
print(f'Completed Summaries: {summaries.count()}')
for s in summaries:
    ai_count = len(s.action_items) if s.action_items else 0
    print(f'Summary ID: {s.id}, Action Items Count: {ai_count}')
