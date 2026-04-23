from rest_framework import serializers


class VerifyMFASetupSerializer(serializers.Serializer):
    """Confirm authenticator app is working before activating MFA."""
    totp_code = serializers.CharField(min_length=6, max_length=6)


class VerifyMFATokenSerializer(serializers.Serializer):
    """Primary path — exchange mfa_token + app TOTP code for full JWT."""
    mfa_token = serializers.CharField()
    totp_code = serializers.CharField(min_length=6, max_length=6)


class RequestMFARecoverySerializer(serializers.Serializer):
    """Fallback path step 1 — user declares device is lost."""
    mfa_token = serializers.CharField()


class VerifyMFARecoverySerializer(serializers.Serializer):
    """Fallback path step 2 — exchange mfa_token + email OTP for full JWT."""
    mfa_token  = serializers.CharField()
    email_code = serializers.CharField(min_length=6, max_length=6)


class DisableMFASerializer(serializers.Serializer):
    """Requires TOTP confirmation before disabling MFA."""
    totp_code = serializers.CharField(min_length=6, max_length=6)
