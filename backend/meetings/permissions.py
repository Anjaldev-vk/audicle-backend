from rest_framework.permissions import BasePermission


class IsMeetingOwnerOrOrgAdmin(BasePermission):
    """
    Allows access only to:
    - The user who created the meeting
    - Org admins / owners of the same organisation
    Used for PATCH, DELETE, and bot/dispatch/ endpoints.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user

        if obj.created_by == user:
            return True

        if (
            request.organisation
            and obj.organisation == request.organisation
            and request.membership
            and request.membership.role in ("owner", "admin")
        ):
            return True

        return False
