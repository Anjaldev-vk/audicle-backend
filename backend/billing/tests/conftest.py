import pytest
from billing.models import Subscription, Plan
from django.utils import timezone
from datetime import timedelta


@pytest.fixture
def user(individual_user):
    return individual_user


@pytest.fixture
def free_plan(db):
    return Plan.objects.get_or_create(name='Free', defaults={'meeting_limit': 5})[0]


@pytest.fixture
def pro_plan(db):
    return Plan.objects.get_or_create(name='Pro', defaults={'meeting_limit': 50, 'razorpay_plan_id': 'plan_pro_123'})[0]


@pytest.fixture
def razorpay_customer(user, organisation, free_plan):
    """Note: Subscription is now the model name replacing RazorpayCustomer."""
    # Signals might have already created one, so we use get_or_create or update
    sub, _ = Subscription.objects.get_or_create(
        user=None, # In old tests, it might have been both. We'll use org-level for this fixture.
        organisation=organisation,
        defaults={
            'plan': free_plan,
            'razorpay_subscription_id': "sub_test123",
            'status': "active",
        }
    )
    return sub


@pytest.fixture
def active_pro_customer(user, organisation, pro_plan):
    """Organisation-level active customer."""
    sub, _ = Subscription.objects.update_or_create(
        user=None,
        organisation=organisation,
        defaults={
            'plan': pro_plan,
            'razorpay_subscription_id': "sub_pro123",
            'status': "active",
            'current_period_end': timezone.now() + timedelta(days=30),
        }
    )
    return sub


@pytest.fixture
def personal_active_pro_customer(user, pro_plan):
    """User-level (personal) active customer."""
    sub, _ = Subscription.objects.update_or_create(
        user=user,
        organisation=None,
        defaults={
            'plan': pro_plan,
            'razorpay_subscription_id': "sub_pers123",
            'status': "active",
            'current_period_end': timezone.now() + timedelta(days=30),
        }
    )
    return sub
