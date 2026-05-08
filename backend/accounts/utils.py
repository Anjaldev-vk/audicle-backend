import random

PLAN_LIMITS = {
    'free': {
        'meetings_per_month':  5,
        'max_workspaces':      2,
        'max_members':         3,
        'bot_access':          True,
        'rag_access':          True,
        'export_access':       False,
        'analytics_team':      False,
        'audit_log_access':    False,
    },
    'pro': {
        'meetings_per_month':  50,
        'max_workspaces':      4,
        'max_members':         20,
        'bot_access':          True,
        'rag_access':          True,
        'export_access':       True,
        'analytics_team':      False,
        'audit_log_access':    False,
    },
    'enterprise': {
        'meetings_per_month':  None, 
        'max_workspaces':      None,
        'max_members':         None,
        'bot_access':          True,
        'rag_access':          True,
        'export_access':       True,
        'analytics_team':      True,
        'audit_log_access':    True,
    },
}


def get_plan_limits(plan):
    """Return limits for a given plan string."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS['free'])


def check_limit(current_count, limit):
    """
    Returns True if limit is reached.
    None limit means unlimited — always returns False.
    """
    if limit is None:
        return False
    return current_count >= limit


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
        'meetings_limit': get_plan_limits(user.plan)['meetings_per_month'],
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
            'meetings_limit': get_plan_limits(org.plan)['meetings_per_month'],
        })

    return workspaces