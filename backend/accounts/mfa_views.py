import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.mfa_serializers import (
    DisableMFASerializer,
    RequestMFARecoverySerializer,
    VerifyMFARecoverySerializer,
    VerifyMFATokenSerializer,
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
from utils.response import success_response, error_response

logger = logging.getLogger("accounts")


# ------------------ MFA Setup Views (enable + verify) --------------------------

class EnableMFAView(APIView):
    """
    POST /api/v1/accounts/mfa/enable/

    Generates a TOTP secret and returns the otpauth:// URI.
    Does NOT activate MFA yet — user must call verify-setup/ to confirm.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user

        if user.mfa_enabled:
            return error_response(
                message="MFA is already enabled on this account.",
                code="mfa_already_enabled",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        secret   = generate_mfa_secret()
        totp_uri = get_totp_uri(secret, user.email)

        # Store secret temporarily — not activated until verify-setup confirms it
        user.mfa_secret = secret
        user.save(update_fields=["mfa_secret"])

        return success_response(
            message="Scan the QR code with your authenticator app, then call verify-setup/.",
            data={"totp_uri": totp_uri},
            status_code=status.HTTP_200_OK
        )


class VerifyMFASetupView(APIView):
    """
    POST /api/v1/accounts/mfa/verify-setup/

    Confirms the TOTP code from the authenticator app and activates MFA on success.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = VerifyMFASetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user      = request.user
        totp_code = serializer.validated_data["totp_code"]

        if not user.mfa_secret:
            return error_response(
                message="Call /mfa/enable/ first to initiate MFA setup.",
                code="mfa_setup_not_initiated",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return error_response(
                message="Invalid or expired TOTP code. Check your authenticator app.",
                code="invalid_totp_code",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user.mfa_enabled = True
        user.save(update_fields=["mfa_enabled"])

        logger.info("MFA enabled for user %s", user.email)

        return success_response(
            message="MFA has been enabled successfully.",
            data={},
            status_code=status.HTTP_200_OK
        )


#---─ Primary MFA login step — exchange mfa_token + TOTP code for full JWT ─────────────────

class VerifyMFATokenView(APIView):
    """
    POST /api/v1/accounts/mfa/verify/

    Primary path — exchange mfa_token + 6-digit app code for full JWT.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = VerifyMFATokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token = serializer.validated_data["mfa_token"]
        totp_code = serializer.validated_data["totp_code"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return error_response(
                message="MFA token is invalid or has expired. Please log in again.",
                code="invalid_mfa_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return error_response(
                message="User not found.",
                code="user_not_found",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return error_response(
                message="Invalid or expired TOTP code.",
                code="invalid_totp_code",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        tokens   = get_tokens(user)
        response = success_response(
            message="MFA verified. Login successful.",
            data={
                "access_token": tokens["access"],
                "user": {
                    "id":    str(user.id),
                    "email": user.email,
                },
            },
            status_code=status.HTTP_200_OK
        )
        set_auth_cookies(response, tokens["refresh"])
        logger.info("MFA primary login successful for %s", user.email)
        return response


# -----─ Fallback path step 1 — request email OTP for MFA recovery (lost device) ─────────────────

class RequestMFARecoveryView(APIView):
    """
    POST /api/v1/accounts/mfa/recover/request/

    User declares their device is lost. Sends a 6-digit OTP to registered email.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = RequestMFARecoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token = serializer.validated_data["mfa_token"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return error_response(
                message="MFA token is invalid or has expired. Please log in again.",
                code="invalid_mfa_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        # Per-user rate limit — 3 emails per hour
        if not check_recovery_rate_limit(user_id):
            return error_response(
                message="Too many recovery attempts. Please try again in 1 hour.",
                code="recovery_rate_limited",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return error_response(
                message="User not found.",
                code="user_not_found",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        otp = generate_email_otp()
        store_email_otp(user_id, otp)

        send_mfa_fallback_email_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
            otp=otp,
        )

        logger.info("MFA fallback email queued for %s", user.email)

        return success_response(
            message="Emergency code sent to your registered email address.",
            data={},
            status_code=status.HTTP_200_OK
        )


# -----─ Fallback path step 2 — verify email OTP, disable MFA, and log in ─────────────────

class VerifyMFARecoveryView(APIView):
    """
    POST /api/v1/accounts/mfa/recover/verify/

    Exchange mfa_token + email OTP for full JWT. Auto-disables MFA on success.
    """
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope   = "auth"

    def post(self, request, *args, **kwargs):
        serializer = VerifyMFARecoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa_token  = serializer.validated_data["mfa_token"]
        email_code = serializer.validated_data["email_code"]

        user_id = verify_mfa_token(mfa_token)
        if not user_id:
            return error_response(
                message="MFA token is invalid or has expired. Please log in again.",
                code="invalid_mfa_token",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        if not verify_email_otp(user_id, email_code):
            return error_response(
                message="Invalid or expired emergency code.",
                code="invalid_email_code",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return error_response(
                message="User not found.",
                code="user_not_found",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        # Auto-disable MFA — device is lost, force fresh setup
        user.mfa_enabled = False
        user.mfa_secret  = None
        user.save(update_fields=["mfa_enabled", "mfa_secret"])

        # Security alert — notify user MFA was disabled
        send_mfa_disabled_alert_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
        )

        tokens   = get_tokens(user)
        response = success_response(
            message="Emergency access granted. MFA has been disabled.",
            data={
                "access_token": tokens["access"],
                "user": {
                    "id":    str(user.id),
                    "email": user.email,
                },
            },
            status_code=status.HTTP_200_OK
        )
        set_auth_cookies(response, tokens["refresh"])
        logger.info("MFA recovery login for %s", user.email)
        return response


class DisableMFAView(APIView):
    """
    POST /api/v1/accounts/mfa/disable/

    Intentional disable from security settings.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = DisableMFASerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        totp_code = serializer.validated_data["totp_code"]

        if not user.mfa_enabled:
            return error_response(
                message="MFA is not enabled on this account.",
                code="mfa_not_enabled",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        if not verify_app_code(user.mfa_secret, totp_code):
            return error_response(
                message="Invalid or expired TOTP code.",
                code="invalid_totp_code",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user.mfa_enabled = False
        user.mfa_secret  = None
        user.save(update_fields=["mfa_enabled", "mfa_secret"])

        # Security alert
        send_mfa_disabled_alert_task.delay(
            user_email=user.email,
            first_name=user.first_name or "there",
        )

        logger.info("MFA intentionally disabled for %s", user.email)

        return success_response(
            message="MFA has been disabled.",
            data={},
            status_code=status.HTTP_200_OK
        )
