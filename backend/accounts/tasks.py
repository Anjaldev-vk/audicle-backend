import logging
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Email Tasks
# These replace the synchronous send_mail calls in signals.py.
# autoretry_for: retries on any Exception (e.g. SMTP timeout)
# max_retries:   3 attempts total
# retry_backoff: exponential backoff (60s, 120s, 240s)
# ─────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=300,
    default_retry_delay=60,
)
def send_welcome_email_task(self, user_email: str, first_name: str):
    """
    Sends a welcome email to a newly registered user.
    Called from accounts/signals.py after User creation.
    """
    subject = "Welcome to Audicle!"
    message = (
        f"Hello {first_name},\n\n"
        "Your account has been successfully created. "
        "We're excited to have you on board!"
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user_email],
            fail_silently=False,
        )
        logger.info(f"Welcome email sent to {user_email}")
    except Exception as exc:
        logger.error(f"Failed to send welcome email to {user_email}: {exc}")
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=300,
    default_retry_delay=60,
)
def send_otp_email_task(self, user_email: str, first_name: str, otp: str):
    """
    Sends an OTP email for password reset.
    Called from accounts/signals.py on password_reset_requested signal.
    """
    subject = "Your Password Reset OTP"
    message = (
        f"Hello {first_name},\n\n"
        f"Your OTP for password reset is: {otp}\n"
        "This code expires in 10 minutes.\n\n"
        "If you did not request this, please ignore this email."
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user_email],
            fail_silently=False,
        )
        logger.info(f"OTP email sent to {user_email}")
    except Exception as exc:
        logger.error(f"Failed to send OTP email to {user_email}: {exc}")
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────
# Celery Beat Periodic Tasks
# ─────────────────────────────────────────────────────────────

@shared_task
def cleanup_expired_otps_task():
    """
    Runs every hour via Celery Beat.
    Nulls out otp and otp_expiry on any User records
    where the OTP has expired (otp_expiry < now).
    Logs the count of cleaned records for observability.
    """
    # Import here to avoid circular imports at module level
    from accounts.models import User

    now = timezone.now()
    expired_qs = User.objects.filter(
        otp__isnull=False,
        otp_expiry__lt=now,
    )
    count = expired_qs.update(otp=None, otp_expiry=None)

    logger.info(f"[Celery Beat] cleanup_expired_otps_task: cleared {count} expired OTP(s).")
    return f"Cleaned {count} expired OTPs"


@shared_task
def reset_monthly_usage_task():
    """
    Runs on the 1st of every month at midnight UTC via Celery Beat.
    Resets meetings_this_month to 0 and updates usage_reset_date
    for all organisations.
    """
    from accounts.models import Organisation
    from django.utils.timezone import now

    today = now().date()
    updated = Organisation.objects.all().update(
        meetings_this_month=0,
        usage_reset_date=today,
    )

    logger.info(
        f"[Celery Beat] reset_monthly_usage_task: reset {updated} organisation(s) on {today}."
    )
    return f"Reset {updated} organisations"
