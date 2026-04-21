import logging
import secrets

import pyotp
from django.core import signing
from django.core.cache import cache

logger = logging.getLogger("accounts")

APP_NAME          = "Audicle"
MFA_TOKEN_SALT    = "audicle.mfa.token"
MFA_TOKEN_MAX_AGE = 300   # 5 minutes in seconds


# ── Secret + URI ──────────────────────────────────────────────────────────────

def generate_mfa_secret() -> str:
    """Generate a new base32 TOTP secret."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, user_email: str) -> str:
    """
    Return an otpauth:// URI for QR code rendering.
    Frontend renders this URI as a QR code using react-qr-code.
    """
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=user_email,
        issuer_name=APP_NAME,
    )


# ── TOTP verification ─────────────────────────────────────────────────────────

def verify_app_code(secret: str, code: str) -> bool:
    """
    Verify a 6-digit TOTP code against the secret.
    valid_window=1 tolerates ±30s clock drift — industry standard.
    """
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ── Email OTP fallback ────────────────────────────────────────────────────────

def generate_email_otp() -> str:
    """Generate a cryptographically secure 6-digit OTP."""
    return str(secrets.randbelow(900000) + 100000)  # always 6 digits


def store_email_otp(user_id: str, otp: str) -> None:
    """Store the email OTP in Redis with a 5-minute TTL."""
    cache.set(f"mfa_fallback_{user_id}", otp, timeout=300)


def verify_email_otp(user_id: str, otp: str) -> bool:
    """
    Verify the email OTP from Redis.
    Deletes the key on success — single use.
    """
    stored = cache.get(f"mfa_fallback_{user_id}")
    if stored and stored == otp:
        cache.delete(f"mfa_fallback_{user_id}")
        return True
    return False


# ── Recovery rate limiting (per user, not per IP) ─────────────────────────────

def check_recovery_rate_limit(user_id: str) -> bool:
    """
    Allow max 3 recovery email requests per user per hour.
    Returns True if the request is allowed, False if rate limited.
    """
    key   = f"mfa_recovery_rate_{user_id}"
    count = cache.get(key, 0)
    if count >= 3:
        return False
    cache.set(key, count + 1, timeout=3600)  # 1-hour window
    return True


# ── MFA session token (short-lived, single-purpose) ──────────────────────────

def generate_mfa_token(user_id: str) -> str:
    """
    Generate a short-lived signed token scoped only for MFA verification.
    Uses Django's signing framework — stateless, no DB/cache needed.
    Expires in 5 minutes.
    """
    return signing.dumps(
        {"user_id": str(user_id)},
        salt=MFA_TOKEN_SALT,
    )


def verify_mfa_token(token: str) -> str | None:
    """
    Verify and decode an MFA token.
    Returns user_id string if valid, None if expired or tampered.
    """
    try:
        data = signing.loads(
            token,
            salt=MFA_TOKEN_SALT,
            max_age=MFA_TOKEN_MAX_AGE,
        )
        return data["user_id"]
    except signing.SignatureExpired:
        logger.warning("MFA token expired")
        return None
    except signing.BadSignature:
        logger.warning("MFA token bad signature")
        return None
