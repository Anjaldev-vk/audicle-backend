from accounts.models import User
for u in User.objects.all():
    print(f"Name: {u.full_name} | Email: {u.email} | ID: {u.id}")
