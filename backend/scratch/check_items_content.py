from action_items.models import ActionItem
items = ActionItem.objects.all()
print(f'Total Items: {items.count()}')
for i in items:
    print(f'ID: {i.id} | Meeting: {i.meeting.title} | Text: {i.text}')
