from rest_framework.permissions import BasePermission


class IsOrgAdmin(BasePermission):
    """
    Allows access only to organisation owners and admins.
    """
    message = 'You must be an organisation admin to perform this action.'

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.is_org_admin
        )


class IsOrgMember(BasePermission):
    """
    Allows access only to users who belong to an organisation.
    """
    message = 'You must be part of an organisation to perform this action.'

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.is_org_member
        )


class IsIndividualUser(BasePermission):
    """
    Allows access only to individual users with no organisation.
    """
    message = 'This action is only available to individual users.'

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.is_individual
        )