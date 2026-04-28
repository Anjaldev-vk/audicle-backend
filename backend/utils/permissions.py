import logging
from django.conf import settings
from rest_framework import permissions

logger = logging.getLogger(__name__)


class IsInternalService(permissions.BasePermission):
    """
    Permission class that only allows requests with a valid X-Internal-Secret header.
    Used for microservice-to-microservice communication.
    """

    def has_permission(self, request, view):
        secret = request.headers.get("X-Internal-Secret")
        expected_secret = getattr(settings, "INTERNAL_API_SECRET", None)

        if not expected_secret:
            logger.error("INTERNAL_API_SECRET is not set in Django settings.")
            return False

        if secret == expected_secret:
            return True

        logger.warning(
            "Internal service authentication failed. Invalid secret provided."
        )
        return False
