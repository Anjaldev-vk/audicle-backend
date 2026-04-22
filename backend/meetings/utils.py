import logging

from meetings.models import Meeting

logger = logging.getLogger("meetings")


def get_meeting_queryset(user):
    """
    Returns a correctly tenant-scoped Meeting queryset.
    - Org users   → all non-archived meetings in their organisation
    - Individual  → only their own non-archived meetings
    Never returns cross-tenant data.
    """
    if user.organisation:
        return (
            Meeting.objects
            .filter(
                organisation=user.organisation,
                is_archived=False,
            )
            .select_related("created_by", "organisation")
            .prefetch_related("participants")
        )
    return (
        Meeting.objects
        .filter(
            created_by=user,
            organisation=None,
            is_archived=False,
        )
        .select_related("created_by")
        .prefetch_related("participants")
    )


def get_meeting_or_404(meeting_id: str, user):
    """
    Fetch a single meeting scoped to the user's tenant.
    Returns Meeting or None — views handle the 404 response.
    """
    try:
        return get_meeting_queryset(user).get(id=meeting_id)
    except Meeting.DoesNotExist:
        return None
