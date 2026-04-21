import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.mfa_serializers import (
    DisableMFASerializer,
    MFARecoveryRequestSerializer,
    MFARecoveryVerifySerializer,
    MFATokenVerifySerializer,
    VerifyMFASetupSerializer,
)
from accounts.mfa_utils import (
    check_recovery_rate_limit,
    generate_email_otp,
    generate_mfa_secret,
    generate_mfa_token,
    get_totp_uri,
    store_email_otp,
    verify_app_code,
    verify_email_otp,
    verify_mfa_token,
)
from accounts.models import User
from accounts.serializers import get_tokens
from accounts.views import set_auth_cookies
from accounts.tasks import (
    send_mfa_fallback_email_task,
    send_mfa_disabled_alert_task,
)

logger = logging.getLogger("accounts")


# ------------------ MFA Setup Views (enable + verify) --------------------------

class EnableMFAView(APIView):
    """
    POST /api/v1/accounts/mfa/enable/

    Generates a TOTP secret and returns the otpauth:// URI.
    Frontend renders the URI as a QR code using react-qr-code.
    Does NOT activate MFA yet — user must call verify-setup/ to confirm.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user

        if user.mfa_enabled:
            return Response(
                {
                    "status": "error",
                    "code": "mfa_already_enabled",
                    "message": "MFA is already enabled on this account.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        secret   = generate_mfa_secret()
        totp_uri = get_totp_uri(secret, user.email)

        # Store secret temporarily — not activated until verify-setup confirms it
        user.mfa_secret = secret
        user.save(update_fields=["mfa_secret"])

        return Response(
            {
                "success": True,
                "message": (
                    "Scan the QR code with your authenticator app "
                    "(Google Authenticator, Authy), then call verify-setup/."
                ),
                "totp_uri": totp_uri,
            },
            status=status.HTTP_200_OK,
        )


#------------- Verify MFA setup with TOTP code from app, activate MFA on success -----------

class VerifyMFASetupView(APIView):
    """
    POST /api/v1/accounts/mfa/verify-setup/

    Confirms the TOTP code from the authenticator app.
    Activates MFA on success.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = VerifyMFASetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user      = request.user
        totp_code = serializer.validated_data["totp_code"]

        if not user.mfa_secret:
            return Response(
                {
                    "status": "error",
                    "code": "mfa_setup_not_initiated",
                    "message": "Call /mfa/enable/ first to initiate MFA setup.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return Response(
                {
                    "status": "error",
                    "code": "invalid_totp_code",
                    "message": "Invalid or expired TOTP code. Check your authenticator app.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.mfa_enabled = True
        user.save(update_fields=["mfa_enabled"])

        logger.info("MFA enabled for user %s", user.email)

        return Response(
            {
                "success": True,
                "message": "MFA has been enabled successfully.",
                "data": {},
            },
            status=status.HTTP_200_OK,
        )


#---─ Primary MFA login step — exchange mfa_token + TOTP code for full JWT ─────────────────

class MFATokenVerifyView(APIView):
    """
    POST /api/v1/accounts/mfa/verify/

    Primary path — exchange mfa_token + 6-digit app code for full JWT.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = MFATokenVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token = serializer.validated_data["mfa_token"]
        totp_code = serializer.validated_data["totp_code"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return Response(
                {
                    "status": "error",
                    "code": "invalid_mfa_token",
                    "message": "MFA token is invalid or has expired. Please log in again.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "user_not_found",
                    "message": "User not found.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return Response(
                {
                    "status": "error",
                    "code": "invalid_totp_code",
                    "message": "Invalid or expired TOTP code.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        tokens   = get_tokens(user)
        response = Response(
            {
                "success": True,
                "message": "MFA verified. Login successful.",
                "access_token": tokens["access"],
                "user": {
                    "id":    str(user.id),
                    "email": user.email,
                },
            },
            status=status.HTTP_200_OK,
        )
        set_auth_cookies(response, tokens["refresh"])
        logger.info("MFA primary login successful for %s", user.email)
        return response


# -----─ Fallback path step 1 — request email OTP for MFA recovery (lost device) ─────────────────

class MFARecoveryRequestView(APIView):
    """
    POST /api/v1/accounts/mfa/recover/request/

    User declares their device is lost.
    Sends a 6-digit OTP to their registered email.
    Rate limited to 3 requests per user per hour.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = MFARecoveryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token = serializer.validated_data["mfa_token"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return Response(
                {
                    "status": "error",
                    "code": "invalid_mfa_token",
                    "message": "MFA token is invalid or has expired. Please log in again.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Per-user rate limit — 3 emails per hour
        if not check_recovery_rate_limit(user_id):
            return Response(
                {
                    "status": "error",
                    "code": "recovery_rate_limited",
                    "message": "Too many recovery attempts. Please try again in 1 hour.",
                    "errors": {},
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "user_not_found",
                    "message": "User not found.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        otp = generate_email_otp()
        store_email_otp(user_id, otp)

        from accounts.tasks import send_mfa_fallback_email_task
        send_mfa_fallback_email_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
            otp=otp,
        )

        logger.info("MFA fallback email queued for %s", user.email)

        return Response(
            {
                "success": True,
                "message": "Emergency code sent to your registered email address.",
                "data": {},
            },
            status=status.HTTP_200_OK,
        )


# -----─ Fallback path step 2 — verify email OTP, disable MFA, and log in ─────────────────

class MFARecoveryVerifyView(APIView):
    """
    POST /api/v1/accounts/mfa/recover/verify/

    Exchange mfa_token + email OTP for full JWT.
    Auto-disables MFA on success — forces fresh setup since device is lost.
    Sends a security alert email to the user.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = MFARecoveryVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token  = serializer.validated_data["mfa_token"]
        email_code = serializer.validated_data["email_code"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return Response(
                {
                    "status": "error",
                    "code": "invalid_mfa_token",
                    "message": "MFA token is invalid or has expired. Please log in again.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not verify_email_otp(user_id, email_code):
            return Response(
                {
                    "status": "error",
                    "code": "invalid_email_code",
                    "message": "Invalid or expired emergency code.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {
                    "status": "error",
                    "code": "user_not_found",
                    "message": "User not found.",
                    "errors": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Auto-disable MFA — device is lost, force fresh setup
        user.mfa_enabled = False
        user.mfa_secret  = None
        user.save(update_fields=["mfa_enabled", "mfa_secret"])

        # Security alert — notify user MFA was disabled
        from accounts.tasks import send_mfa_disabled_alert_task
        send_mfa_disabled_alert_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
        )

        tokens   = get_tokens(user)
        response = Response(
            {
                "success": True,
                "message": (
                    "Emergency access granted. MFA has been disabled — "
                    "please set it up again from your security settings."
                ),
                "access_token": tokens["access"],
                "user": {
                    "id":    str(user.id),
                    "email": user.email,
                },
            },
            status=status.HTTP_200_OK,
        )
        set_auth_cookies(response, tokens["refresh"])
        logger.info(
            "MFA recovery login for %s — MFA auto-disabled, fresh setup required",
            user.email,
        )
        return response


#-------------------- MFA Disable View (intentional disable from security settings) --------------------------

class DisableMFAView(APIView):
    """
    POST /api/v1/accounts/mfa/disable/

    Intentional disable from security settings.
    Requires valid TOTP code as confirmation.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = DisableMFASerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user      = request.user
        totp_code = serializer.validated_data["totp_code"]

        if not user.mfa_enabled:
            return Response(
                {
                    "status": "error",
                    "code": "mfa_not_enabled",
                    "message": "MFA is not enabled on this account.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return Response(
                {
                    "status": "error",
                    "code": "invalid_totp_code",
                    "message": "Invalid or expired TOTP code.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.mfa_enabled = False
        user.mfa_secret  = None
        user.save(update_fields=["mfa_enabled", "mfa_secret"])

        # Security alert
        from accounts.tasks import send_mfa_disabled_alert_task
        send_mfa_disabled_alert_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
        )

        logger.info("MFA intentionally disabled for %s", user.email)

        return Response(
            {
                "success": True,
                "message": "MFA has been disabled.",
                "data": {},
            },
            status=status.HTTP_200_OK,
        )
