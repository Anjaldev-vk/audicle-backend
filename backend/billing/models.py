import uuid
from django.db import models
from accounts.models import User, Organisation


class Plan(models.Model):
    """
    Model representing a subscription plan.
    Enables dynamic pricing and limit management from the Django admin.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    name = models.CharField(max_length=50, unique=True)
    razorpay_plan_id = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        unique=True,
        help_text="Razorpay plan ID e.g. plan_XXXXXXXXXX. Leave blank for Free plan."
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    meeting_limit = models.IntegerField(
        default=5, 
        help_text="Number of meetings allowed per month. Use -1 for unlimited."
    )
    
    # Feature flags
    max_workspaces = models.IntegerField(default=2, help_text="For Personal plan users.")
    max_members = models.IntegerField(default=3, help_text="For Organisation plans.")
    bot_access = models.BooleanField(default=True)
    rag_access = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Subscription(models.Model):
    """
    Stores subscription info per user (personal) or organisation.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    
    # One of these will be set, not both
    user = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    organisation = models.OneToOneField(
        Organisation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    
    razorpay_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Razorpay subscription ID e.g. sub_XXXXXXXXXX",
    )
    
    status = models.CharField(
        max_length=50,
        choices=[
            ('active', 'Active'),
            ('past_due', 'Past Due'),
            ('cancelled', 'Cancelled'),
            ('paused', 'Paused'),
        ],
        default='active',
    )
    
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.user:
            target = self.user.email
        elif self.organisation:
            target = self.organisation.name
        else:
            target = "(unlinked)"
        plan_name = self.plan.name if self.plan else "(no plan)"
        return f"{target} - {plan_name} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == 'active'
