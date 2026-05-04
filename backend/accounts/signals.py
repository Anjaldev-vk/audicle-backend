from django.db.models.signals import post_save
from django.dispatch import Signal, receiver
from .models import User

password_reset_requested = Signal()


# ------------------Account Creation: Welcome Email ------------------
@receiver(post_save, sender=User)
def send_welcome_email(sender, instance, created, **kwargs):
    """
    Triggers automatically when a new User is created.
    Dispatches the welcome email as an async Celery task.
    """
    if created:
        # Import here to avoid circular imports at module load time
        from .tasks import send_welcome_email_task
        send_welcome_email_task.delay(instance.email, instance.first_name)


# ------------------Password Reset: OTP Email ------------------
@receiver(password_reset_requested)
def send_otp_email(sender, user, otp, **kwargs):
    """
    Listens for the password_reset_requested signal.
    Dispatches the OTP email as an async Celery task.
    """
    from .tasks import send_otp_email_task
    send_otp_email_task.delay(user.email, user.first_name, otp)


# ------------------ Cache Invalidation ------------------
import logging
from django.db.models.signals import post_delete
from accounts.models import Membership
from utils.cache_keys import invalidate_user_cache, invalidate_org_cache

logger = logging.getLogger('accounts')


@receiver(post_save, sender=Membership)
def on_membership_save(sender, instance, **kwargs):
    invalidate_user_cache(instance.user_id)
    invalidate_org_cache(instance.organisation_id)
    logger.info(
        'cache invalidated on membership save user=%s org=%s',
        instance.user_id,
        instance.organisation_id
    )


@receiver(post_delete, sender=Membership)
def on_membership_delete(sender, instance, **kwargs):
    invalidate_user_cache(instance.user_id)
    invalidate_org_cache(instance.organisation_id)
    logger.info(
        'cache invalidated on membership delete user=%s org=%s',
        instance.user_id,
        instance.organisation_id
    )