import uuid
from django.db import models
from accounts.models import User, Organisation


class Plan(models.TextChoices):
    FREE = "free", "Free"
    PRO = "pro", "Pro"
    ENTERPRISE = "enterprise", "Enterprise"


class RazorpayCustomer(models.Model):
    """
    Stores Razorpay customer and subscription info per user/org.
    One record per workspace.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="razorpay_customers",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="razorpay_customers",
    )

    # Razorpay IDs
    razorpay_customer_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Razorpay customer ID e.g. cust_XXXXXXXXXX",
    )
    razorpay_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Razorpay subscription ID e.g. sub_XXXXXXXXXX",
    )

    # Plan info
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE,
    )
    subscription_status = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="created/authenticated/active/paused/cancelled/expired",
    )

    # Billing period
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["user", "organisation"]]
        indexes = [
            models.Index(fields=["user", "plan"]),
            models.Index(fields=["razorpay_subscription_id"]),
        ]
        verbose_name = "Razorpay Customer"
        verbose_name_plural = "Razorpay Customers"

    def __str__(self):
        return f"RazorpayCustomer({self.user.email}, {self.plan})"

    @property
    def is_active(self) -> bool:
        return self.subscription_status == "active"
