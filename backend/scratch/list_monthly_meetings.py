from meetings.models import Meeting
from accounts.models import User
from django.utils import timezone

u = User.objects.get(email='anjaldev.aiuse@gmail.com')
start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
ms = Meeting.objects.filter(created_by=u, created_at__gte=start)

print(f"Meetings for {u.email} this month: {ms.count()}")
for m in ms:
    print(f"Meeting: {m.title} | Status: {m.status} | Created: {m.created_at}")
