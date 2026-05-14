from django.utils import timezone
from utils.response import error_response


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


def get_active_subscription(request):
    """
    Get subscription based on active workspace.
    Personal → user subscription
    Organisation → org subscription
    """
    from billing.models import Subscription
    workspace = getattr(request, 'organisation', None)

    if workspace:
        # Organisation workspace
        return Subscription.objects.filter(organisation=workspace).first()
    else:
        # Personal workspace
        user = getattr(request, 'user', request)
        if not hasattr(user, 'id'):
             return None
        return Subscription.objects.filter(user=user).first()


def check_meeting_limit(request):
    """
    Returns a 403 Response if user/org has hit monthly meeting limit.
    Returns None if within limits.
    """
    subscription = get_active_subscription(request)
    
    # Fallback to Free plan defaults if no subscription record found
    if not subscription:
        limit = 5
    else:
        limit = subscription.plan.meeting_limit

    if limit == -1:
        return None  # unlimited

    monthly_count = _get_monthly_meeting_count(request)

    if monthly_count >= limit:
        return meeting_limit_response(limit)
    
    return None


def _get_monthly_meeting_count(request):
    """Helper to count meetings for the current period."""
    from meetings.models import Meeting
    
    now = timezone.now()
    user = getattr(request, 'user', request)
    org = getattr(request, 'organisation', None)
    
    # Filter by workspace (if any) or by personal user
    filters = {
        'is_archived': False,
        'created_at__year': now.year,
        'created_at__month': now.month,
    }
    
    if org:
        filters['organisation'] = org
    else:
        filters['created_by'] = user
        filters['organisation'] = None

    return Meeting.objects.filter(**filters).count()


def check_bot_access(request):
    subscription = get_active_subscription(request)
    # Allow if no subscription found (tests/free users)
    if not subscription:
        return None
    bot_access = subscription.plan.bot_access
    if not bot_access:
        return bot_access_response()
    return None


def check_rag_access(request):
    """
    Returns a 403 Response if user's plan does not include RAG access.
    Returns None if allowed.
    """
    subscription = get_active_subscription(request)
    
    if subscription:
        rag_access = subscription.plan.rag_access
    else:
        # Fallback to Free plan
        from billing.models import Plan
        free_plan = Plan.objects.filter(name='Free').first()
        rag_access = free_plan.rag_access if free_plan else False
    
    if not rag_access:
        return rag_access_response()
    return None


def check_member_limit(organisation):
    """
    Returns a 403 Response if org has hit its member limit.
    Returns None if within limits.
    """
    from accounts.models import Membership
    from billing.models import Subscription
    
    subscription = Subscription.objects.filter(organisation=organisation).first()
    
    # Default to 2 members for Free plan
    max_members = subscription.plan.max_members if subscription else 2
    
    if max_members == -1:
        return None
        
    current = Membership.objects.filter(organisation=organisation).count()
    if current >= max_members:
        return member_limit_response()
    return None


def check_workspace_limit(user):
    """
    Returns a 403 Response if user has hit their workspace limit.
    Returns None if they are within limits.
    """
    from accounts.models import Membership
    from billing.models import Subscription
    
    subscription = Subscription.objects.filter(user=user).first()
    
    # Default to 2 workspaces for Free plan
    max_ws = subscription.plan.max_workspaces if subscription else 2
    
    if max_ws == -1:
        return None
        
    current = Membership.objects.filter(user=user, role='owner').count()
    if current >= max_ws:
        return workspace_limit_response()
    return None
