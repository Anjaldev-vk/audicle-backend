import pytest
from billing.models import RazorpayCustomer, Plan
from django.utils import timezone
from datetime import timedelta


@pytest.fixture
def user(individual_user):
    return individual_user


@pytest.fixture
def razorpay_customer(user, organisation):
    return RazorpayCustomer.objects.create(
        user=user,
        organisation=organisation,
        razorpay_customer_id="cust_test123",
        razorpay_subscription_id="sub_test123",
        plan=Plan.FREE,
        subscription_status="created",
    )


@pytest.fixture
def active_pro_customer(user, organisation):
    """Organisation-level active customer."""
    return RazorpayCustomer.objects.create(
        user=user,
        organisation=organisation,
        razorpay_customer_id="cust_pro123",
        razorpay_subscription_id="sub_pro123",
        plan=Plan.PRO,
        subscription_status="active",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def personal_active_pro_customer(user):
    """User-level (personal) active customer."""
    return RazorpayCustomer.objects.create(
        user=user,
        organisation=None,
        razorpay_customer_id="cust_pers123",
        razorpay_subscription_id="sub_pers123",
        plan=Plan.PRO,
        subscription_status="active",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )
