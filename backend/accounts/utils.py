import random

PLAN_LIMITS = {
    'free': 20,
    'pro': 100,
    'enterprise': 1000,
}


def generate_otp(length=6):
    """Generates a numeric string OTP."""
    return "".join([str(random.randint(0, 9)) for _ in range(length)])


def get_workspaces_for_user(user):
    workspaces = [{
        'type': 'personal',
        'id': 'personal',
        'name': f"{user.first_name}'s Workspace",
        'plan': user.plan,
        'role': None,
        'meetings_used': user.meetings_this_month,
        'meetings_limit': PLAN_LIMITS.get(user.plan, 20),
    }]

    for m in user.memberships.select_related('organisation').all():
        org = m.organisation
        workspaces.append({
            'type': 'organisation',
            'id': str(org.id),
            'name': org.name,
            'plan': org.plan,
            'role': m.role,
            'meetings_used': org.meetings_this_month,
            'meetings_limit': PLAN_LIMITS.get(org.plan, 20),
        })

    return workspaces