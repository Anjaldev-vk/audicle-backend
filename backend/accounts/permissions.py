from rest_framework.permissions import BasePermission
from accounts.models import Membership


class IsOrgMember(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.membership is not None
        )


class IsOrgAdmin(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.membership is not None and
            request.membership.role in [
                Membership.Role.OWNER,
                Membership.Role.ADMIN
            ]
        )


class IsOrgOwner(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.membership is not None and
            request.membership.role == Membership.Role.OWNER
        )