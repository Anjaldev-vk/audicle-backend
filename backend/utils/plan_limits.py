from utils.response import error_response
from accounts.utils import get_plan_limits, check_limit


def workspace_limit_response():
    return error_response(
        code='plan_limit_reached',
        message='You have reached the maximum number of workspaces '
                'for your plan. Upgrade to Pro to create more.',
        status_code=403,
    )


def member_limit_response():
    return error_response(
        code='plan_limit_reached',
        message='You have reached the maximum number of members '
                'for your plan. Upgrade to add more members.',
        status_code=403,
    )


def bot_access_response():
    return error_response(
        code='plan_limit_reached',
        message='Bot access requires a Pro plan. '
                'Upgrade to auto-join meetings.',
        status_code=403,
    )


def rag_access_response():
    return error_response(
        code='plan_limit_reached',
        message='AI chat requires a Pro plan. '
                'Upgrade to chat with your meeting history.',
        status_code=403,
    )


def meeting_limit_response(limit):
    return error_response(
        code='plan_limit_reached',
        message='You have reached your limit of %s meetings '
                'this month. Upgrade to record more.' % limit,
        status_code=403,
    )


def check_workspace_limit(user):
    """
    Returns a 403 Response if user has hit their workspace limit.
    Returns None if they are within limits.
    """
    from accounts.models import Membership
    limits = get_plan_limits(user.plan)
    max_ws = limits['max_workspaces']
    if max_ws is None:
        return None
    current = Membership.objects.filter(
        user=user, role='owner'
    ).count()
    if check_limit(current, max_ws):
        return workspace_limit_response()
    return None


def check_member_limit(organisation):
    """
    Returns a 403 Response if org has hit its member limit.
    Returns None if within limits.
    """
    from accounts.models import Membership
    limits = get_plan_limits(organisation.plan)
    max_members = limits['max_members']
    if max_members is None:
        return None
    current = Membership.objects.filter(
        organisation=organisation
    ).count()
    if check_limit(current, max_members):
        return member_limit_response()
    return None


def check_bot_access(user):
    """
    Returns a 403 Response if user's plan does not include bot access.
    Returns None if allowed.
    """
    limits = get_plan_limits(user.plan)
    if not limits['bot_access']:
        return bot_access_response()
    return None


def check_rag_access(user):
    """
    Returns a 403 Response if user's plan does not include RAG access.
    Returns None if allowed.
    """
    limits = get_plan_limits(user.plan)
    if not limits['rag_access']:
        return rag_access_response()
    return None


def check_meeting_limit(user):
    """
    Returns a 403 Response if user has hit monthly meeting limit.
    Returns None if within limits.
    Checks personal plan for personal workspace,
    org plan for org workspace.
    """
    limits = get_plan_limits(user.plan)
    max_meetings = limits['meetings_per_month']
    if max_meetings is None:
        return None
    if check_limit(user.meetings_this_month, max_meetings):
        return meeting_limit_response(max_meetings)
    return None


def check_org_meeting_limit(organisation):
    """
    Returns a 403 Response if org has hit monthly meeting limit.
    Returns None if within limits.
    """
    limits = get_plan_limits(organisation.plan)
    max_meetings = limits['meetings_per_month']
    if max_meetings is None:
        return None
    if check_limit(organisation.meetings_this_month, max_meetings):
        return meeting_limit_response(max_meetings)
    return None
