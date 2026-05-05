import logging
from meetings.models import Meeting

logger = logging.getLogger("meetings")


class TenantQuerysetMixin:
    def get_meeting_queryset(self, user, organisation):
        from meetings.models import Meeting
        if organisation:
            return Meeting.objects.filter(
                organisation=organisation,
                is_archived=False
            ).select_related('created_by', 'organisation')
        return Meeting.objects.filter(
            created_by=user,
            organisation=None,
            is_archived=False
        ).select_related('created_by')


def get_meeting_or_404(meeting_id: str, user, organisation=None):
    """
    Fetch a single meeting scoped to the user's tenant.
    Returns Meeting or None.
    """
    mixin = TenantQuerysetMixin()
    try:
        qs = mixin.get_meeting_queryset(user, organisation)
        meeting = qs.get(id=meeting_id)
        return meeting
    except Meeting.DoesNotExist:
        logger.warning("get_meeting_or_404: Meeting %s not found in queryset for user %s (org %s)", meeting_id, user.id, organisation.id if organisation else "None")
        return None
    except Exception as exc:
        logger.error("get_meeting_or_404: Error fetching meeting %s: %s", meeting_id, exc)
        return None
