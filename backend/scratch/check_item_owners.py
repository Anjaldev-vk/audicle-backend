from action_items.models import ActionItem
items = ActionItem.objects.all()
for i in items:
    print(f'Meeting: {i.meeting.title} | User: {i.created_by.full_name} | Text: {i.text}')
