import logging

from django.conf import settings
from django.utils import timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import requests as http_requests
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from utils.response import error_response, success_response
from .models import GoogleCalendarToken

logger = logging.getLogger("calendar_integration")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def _build_flow():
    """Build Google OAuth flow from settings."""
    return Flow.from_client_config(
        client_config={
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.GOOGLE_CALENDAR_REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.GOOGLE_CALENDAR_REDIRECT_URI,
    )


class CalendarConnectView(APIView):
    """
    GET /api/v1/calendar/connect/

    Generates a Google OAuth URL and returns it to the frontend.
    Frontend redirects the user to this URL.
    After consent, Google redirects to /api/v1/calendar/callback/

    We encode user_id and workspace_id in the OAuth state parameter
    so we know which user to associate the token with on callback.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import json
        import base64

        flow = _build_flow()

        # Encode user context in state so callback knows who this is
        state_data = {
            "user_id": str(request.user.id),
            "organisation_id": str(request.organisation.id)
            if request.organisation
            else None,
        }
        state = base64.urlsafe_b64encode(
            json.dumps(state_data).encode()
        ).decode()

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent to always get refresh token
            state=state,
        )

        logger.info(
            "Calendar connect initiated by user %s",
            request.user.email,
        )

        return success_response(
            message="Google Calendar authorization URL generated.",
            data={"auth_url": auth_url},
            status_code=status.HTTP_200_OK,
        )


class CalendarCallbackView(APIView):
    """
    GET /api/v1/calendar/callback/

    Google redirects here after user grants calendar access.
    Exchanges the authorization code for access + refresh tokens.
    Saves tokens to GoogleCalendarToken model.
    Redirects user back to frontend dashboard.
    """

    permission_classes = []
    authentication_classes = []

    def get(self, request):
        import json
        import base64
        from datetime import datetime

        from accounts.models import User, Organisation

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        # User denied access
        if error:
            logger.info("Calendar connect denied: %s", error)
            return error_response(
                message="Calendar access was denied.",
                code="access_denied",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if not code or not state:
            return error_response(
                message="Missing code or state parameter.",
                code="invalid_callback",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Decode state to get user context
        try:
            state_data = json.loads(
                base64.urlsafe_b64decode(state.encode()).decode()
            )
            user_id = state_data["user_id"]
            organisation_id = state_data.get("organisation_id")
        except Exception:
            return error_response(
                message="Invalid state parameter.",
                code="invalid_state",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Get user
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return error_response(
                message="User not found.",
                code="user_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Get organisation if provided
        organisation = None
        if organisation_id:
            try:
                organisation = Organisation.objects.get(id=organisation_id)
            except Organisation.DoesNotExist:
                pass

        # Exchange code for tokens
        try:
            flow = _build_flow()
            flow.fetch_token(code=code)
            credentials = flow.credentials
        except Exception as exc:
            logger.error(
                "Token exchange failed for user %s: %s",
                user_id,
                exc,
            )
            return error_response(
                message="Failed to exchange authorization code for tokens.",
                code="token_exchange_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Get Google email from token
        try:
            import google.oauth2.id_token
            import google.auth.transport.requests

            id_info = google.oauth2.id_token.verify_oauth2_token(
                credentials.id_token,
                google.auth.transport.requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
            google_email = id_info.get("email", user.email)
        except Exception:
            google_email = user.email

        # Save or update token
        token_expiry = credentials.expiry or timezone.now()
        if timezone.is_naive(token_expiry):
            token_expiry = timezone.make_aware(token_expiry)

        GoogleCalendarToken.objects.update_or_create(
            user=user,
            organisation=organisation,
            defaults={
                "access_token": credentials.token,
                "refresh_token": credentials.refresh_token or "",
                "token_expiry": token_expiry,
                "google_email": google_email,
                "is_active": True,
                "sync_error": None,
            },
        )

        logger.info(
            "Calendar connected for user %s google_email=%s",
            user.email,
            google_email,
        )

        # Fire immediate sync so user sees their meetings right away
        from .tasks import sync_calendar_for_user
        sync_calendar_for_user.delay(
            user_id=str(user.id),
            organisation_id=str(organisation.id) if organisation else None,
        )

        # Redirect to frontend
        frontend_url = getattr(
            settings, "FRONTEND_URL", "http://localhost"
        )
        from django.shortcuts import redirect
        return redirect(f"{frontend_url}/settings?calendar=connected")


class CalendarDisconnectView(APIView):
    """
    POST /api/v1/calendar/disconnect/

    Revokes Google Calendar access and deletes the stored token.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        organisation = getattr(request, "organisation", None)

        try:
            token = GoogleCalendarToken.objects.get(
                user=request.user,
                organisation=organisation,
            )
        except GoogleCalendarToken.DoesNotExist:
            return error_response(
                message="No calendar connection found.",
                code="not_connected",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Revoke token with Google
        try:
            http_requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token.access_token},
                timeout=5,
            )
        except Exception as exc:
            logger.error(
                "Failed to revoke Google token for user %s: %s",
                request.user.email,
                exc,
            )
            # Continue with local deletion even if revoke fails

        token.delete()

        logger.info(
            "Calendar disconnected for user %s",
            request.user.email,
        )

        return success_response(
            message="Google Calendar disconnected successfully.",
            status_code=status.HTTP_200_OK,
        )


class CalendarStatusView(APIView):
    """
    GET /api/v1/calendar/status/

    Returns whether the user has connected their Google Calendar
    for the current workspace.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = getattr(request, "organisation", None)

        try:
            token = GoogleCalendarToken.objects.get(
                user=request.user,
                organisation=organisation,
                is_active=True,
            )
            return success_response(
                message="Calendar connection status retrieved.",
                data={
                    "connected": True,
                    "google_email": token.google_email,
                    "last_synced_at": token.last_synced_at.isoformat()
                    if token.last_synced_at
                    else None,
                    "sync_error": token.sync_error,
                },
                status_code=status.HTTP_200_OK,
            )
        except GoogleCalendarToken.DoesNotExist:
            return success_response(
                message="Calendar connection status retrieved.",
                data={
                    "connected": False,
                    "google_email": None,
                    "last_synced_at": None,
                    "sync_error": None,
                },
                status_code=status.HTTP_200_OK,
            )
