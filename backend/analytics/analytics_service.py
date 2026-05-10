from datetime import datetime, timezone, timedelta
from collections import defaultdict
from .repository import (
    query_events,
    query_events_by_user,
    count_events,
    group_by_day,
    group_by_user,
    average_metadata_value,
)
from .constants import EventType, PERIOD_DAYS


def get_workspace_id(request):
    """Return workspace_id based on request context."""
    if request.organisation:
        return str(request.organisation.id)
    return str(request.user.id)


def build_overview(workspace_id, days=30):
    """
    Build overview metrics for a workspace.
    Used for both personal and org dashboards.
    """
    meetings_completed = query_events(
        workspace_id=workspace_id,
        event_type=EventType.MEETING_COMPLETED,
        days=days,
    )
    meetings_created = count_events(
        workspace_id=workspace_id,
        event_type=EventType.MEETING_CREATED,
        days=days,
    )
    transcriptions = count_events(
        workspace_id=workspace_id,
        event_type=EventType.TRANSCRIPTION_DONE,
        days=days,
    )
    summaries = count_events(
        workspace_id=workspace_id,
        event_type=EventType.SUMMARY_DONE,
        days=days,
    )
    action_items_created = count_events(
        workspace_id=workspace_id,
        event_type=EventType.ACTION_ITEM_CREATED,
        days=days,
    )
    action_items_completed = count_events(
        workspace_id=workspace_id,
        event_type=EventType.ACTION_ITEM_COMPLETED,
        days=days,
    )
    rag_queries = count_events(
        workspace_id=workspace_id,
        event_type=EventType.RAG_QUERY,
        days=days,
    )
    bot_joins = count_events(
        workspace_id=workspace_id,
        event_type=EventType.BOT_JOINED,
        days=days,
    )

    # Deduplicate completions by meeting_id
    unique_completions = {}
    for e in meetings_completed:
        m_id = e.get('metadata', {}).get('meeting_id')
        if m_id not in unique_completions:
            unique_completions[m_id] = e
    
    unique_completions_list = list(unique_completions.values())
    meetings_completed_count = len(unique_completions_list)

    avg_duration = average_metadata_value(
        unique_completions_list, 'duration_seconds'
    )

    # Bot success rate
    bot_success_rate = 0
    if bot_joins > 0:
        bot_success_rate = round(
            (meetings_completed_count / bot_joins) * 100, 1
        )

    # Action item completion rate
    action_completion_rate = 0
    if action_items_created > 0:
        action_completion_rate = round(
            (action_items_completed / action_items_created) * 100, 1
        )

    # 5. Usage metrics (SQL)
    from accounts.models import User, Organisation, Membership
    total_members = 1
    storage_gb = round(meetings_completed_count * 0.15, 2) 

    try:
        import uuid
        try:
            val = uuid.UUID(workspace_id)
            org = Organisation.objects.filter(id=val).first()
            if org:
                total_members = Membership.objects.filter(organisation=org).count()
            else:
                user = User.objects.filter(id=val).first()
                if user:
                    total_members = 1
        except ValueError: pass
    except Exception: pass

    return {
        'period_days':              days,
        'meetings_created':         meetings_created,
        'meetings_completed':       meetings_completed_count,
        'avg_duration_seconds':     avg_duration,
        'avg_duration_minutes':     round(avg_duration / 60, 1),
        'transcriptions_done':      transcriptions,
        'summaries_done':           summaries,
        'action_items_created':     action_items_created,
        'action_items_completed':   action_items_completed,
        'action_completion_rate':   action_completion_rate,
        'rag_queries':              rag_queries,
        'bot_joins':                bot_joins,
        'bot_success_rate':         bot_success_rate,
        'total_members':            total_members,
        'storage_used_gb':          storage_gb,
    }


def build_meetings_chart(workspace_id, days=30):
    """
    Build meeting frequency chart data grouped by day.
    Returns list of {date, count} for frontend charts.
    """
    events = query_events(
        workspace_id=workspace_id,
        event_type=EventType.MEETING_COMPLETED,
        days=days,
    )
    grouped = group_by_day(events)

    # Fill in missing days with 0
    result = []
    for i in range(days):
        date = (
            datetime.now(timezone.utc) - timedelta(days=days - i - 1)
        ).strftime('%Y-%m-%d')
        result.append({
            'date':  date,
            'count': grouped.get(date, 0),
        })
    return result


def build_activity_chart(workspace_id, days=30):
    """
    Build activity chart showing all event types over time.
    Returns per-event-type daily counts.
    """
    result = {}
    for event_type in EventType.ALL:
        events  = query_events(
            workspace_id=workspace_id,
            event_type=event_type,
            days=days,
        )
        grouped = group_by_day(events)
        result[event_type] = grouped
    return result


def build_team_overview(workspace_id, days=30):
    """
    Build team-level overview — org admins only.
    Includes per-member breakdown.
    """
    overview = build_overview(workspace_id, days)

    # Per-member activity
    all_events = query_events(
        workspace_id=workspace_id,
        days=days,
    )
    by_user = group_by_user(all_events)

    return {
        **overview,
        'members_activity': by_user,
        'total_events':     len(all_events),
    }


def build_team_members(workspace_id, days=30):
    """
    Per-member breakdown for org admins.
    Returns list of member activity sorted by most active.
    """
    from accounts.models import Membership, User

    # Get all members in this org
    memberships = Membership.objects.filter(
        organisation_id=workspace_id
    ).select_related('user')

    result = []
    for membership in memberships:
        user   = membership.user
        events = query_events_by_user(
            user_id=str(user.id),
            days=days,
        )
        # Filter to this workspace only
        workspace_events = [
            e for e in events
            if e.get('workspace_id') == workspace_id
        ]
        meetings = len([
            e for e in workspace_events
            if e['event_type'] == EventType.MEETING_COMPLETED
        ])
        actions_done = len([
            e for e in workspace_events
            if e['event_type'] == EventType.ACTION_ITEM_COMPLETED
        ])
        rag = len([
            e for e in workspace_events
            if e['event_type'] == EventType.RAG_QUERY
        ])

        result.append({
            'user_id':              str(user.id),
            'name':                 user.full_name,
            'email':                user.email,
            'role':                 membership.role,
            'meetings_completed':   meetings,
            'action_items_done':    actions_done,
            'rag_queries':          rag,
            'total_events':         len(workspace_events),
        })

    # Sort by most active
    result.sort(key=lambda x: x['total_events'], reverse=True)
    return result
