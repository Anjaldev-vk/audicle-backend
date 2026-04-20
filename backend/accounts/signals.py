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