import pytest
import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock
from django.conf import settings
from rest_framework import status

from billing.models import Subscription, Plan


PLAN_URL = "/api/v1/billing/plan/"
CHECKOUT_URL = "/api/v1/billing/checkout/"
WEBHOOK_URL = "/api/v1/billing/webhook/"
USAGE_URL = "/api/v1/billing/usage/"
CANCEL_URL = "/api/v1/billing/cancel/"


def make_webhook_signature(payload: dict, secret: str) -> str:
    """Generate valid Razorpay webhook signature."""
    body = json.dumps(payload, separators=(",", ":"))
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── Plan endpoint ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingPlanView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_free_plan_by_default(self, auth_client, user, free_plan):
        response = auth_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["plan"] == "Free"
        assert data["meetings_limit"] == 5
        assert "available_plans" in data

    def test_returns_correct_usage(self, auth_client, user, free_plan):
        Subscription.objects.get_or_create(user=user, defaults={'plan': free_plan})
        user.meetings_this_month = 3
        user.save(update_fields=["meetings_this_month"])

        response = auth_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["meetings_used"] == 3

    def test_returns_subscription_status_when_exists(
        self, auth_client, personal_active_pro_customer
    ):
        response = auth_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["subscription_status"] == "active"
        assert data["current_period_end"] is not None

    def test_available_plans_contains_upgrade_plans(self, auth_client, pro_plan):
        # We need a free plan too so the view doesn't fail on get_or_create
        Plan.objects.get_or_create(name='Free', defaults={'meeting_limit': 5})
        response = auth_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        plan_names = [p["plan"] for p in data["available_plans"]]
        assert "Pro" in plan_names


# ── Checkout endpoint ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingCheckoutView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(CHECKOUT_URL, {"plan": "Pro"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_plan_returns_400(self, auth_client):
        response = auth_client.post(
            CHECKOUT_URL, {"plan": "invalid"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_plan"

    def test_free_plan_returns_400(self, auth_client, free_plan):
        response = auth_client.post(
            CHECKOUT_URL, {"plan": "Free"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("billing.views.get_razorpay_client")
    def test_creates_subscription_successfully(
        self, mock_client, auth_client, user, free_plan, pro_plan
    ):
        mock_rz = MagicMock()
        mock_rz.subscription.create.return_value = {
            "id": "sub_new123",
            "status": "created",
        }
        mock_client.return_value = mock_rz

        Subscription.objects.get_or_create(user=user, defaults={'plan': free_plan})

        response = auth_client.post(
            CHECKOUT_URL, {"plan": "Pro"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert "subscription_id" in data
        assert data["plan"] == "Pro"

    @patch("billing.views.get_razorpay_client")
    def test_plan_not_configured_returns_503(
        self, mock_client, auth_client, user, free_plan, db
    ):
        Plan.objects.get_or_create(name='Unconfigured', price=100)
        Subscription.objects.get_or_create(user=user, defaults={'plan': free_plan})
        
        response = auth_client.post(
            CHECKOUT_URL, {"plan": "Unconfigured"}, format="json"
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "plan_not_configured"


# ── Usage endpoint ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingUsageView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_usage_data(self, auth_client, user, free_plan):
        Subscription.objects.get_or_create(user=user, defaults={'plan': free_plan})
        
        user.meetings_this_month = 2
        user.save(update_fields=["meetings_this_month"])

        response = auth_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["meetings_used"] == 2
        assert data["meetings_limit"] == 5
        assert data["plan"] == "Free"

    def test_usage_percentage_calculated_correctly(
        self, auth_client, user, free_plan
    ):
        Subscription.objects.get_or_create(user=user, defaults={'plan': free_plan})
        user.meetings_this_month = 2
        user.save(update_fields=["meetings_this_month"])

        response = auth_client.get(USAGE_URL)
        data = response.json()["data"]
        assert data["usage_percentage"] == 40.0


# ── Cancel endpoint ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingCancelView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(CANCEL_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("billing.views.get_razorpay_client")
    def test_cancels_subscription_successfully(
        self, mock_client, auth_client, personal_active_pro_customer
    ):
        mock_rz = MagicMock()
        mock_rz.subscription.cancel.return_value = {"status": "cancelled"}
        mock_client.return_value = mock_rz

        response = auth_client.post(CANCEL_URL)
        assert response.status_code == status.HTTP_200_OK

        personal_active_pro_customer.refresh_from_db()
        assert personal_active_pro_customer.status == "cancelled"


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingWebhookView:

    def _post_webhook(self, client, payload, secret="test_secret"):
        body = json.dumps(payload, separators=(",", ":"))
        signature = hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        return client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )

    @patch("billing.views.get_razorpay_client")
    def test_activated_event_upgrades_plan(
        self,
        mock_client,
        api_client,
        personal_active_pro_customer,
        pro_plan,
    ):
        mock_rz = MagicMock()
        mock_client.return_value = mock_rz
        personal_active_pro_customer.razorpay_subscription_id = "sub_test123"
        personal_active_pro_customer.save()

        payload = {
            "event": "subscription.activated",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_test123",
                        "plan_id": "plan_pro_123",
                        "status": "active",
                        "current_start": 1700000000,
                        "current_end": 1702678400,
                    }
                }
            },
        }

        with patch.object(mock_rz.utility, "verify_webhook_signature", return_value=None):
            response = self._post_webhook(api_client, payload)

        assert response.status_code == status.HTTP_200_OK
        personal_active_pro_customer.refresh_from_db()
        assert personal_active_pro_customer.status == "active"
        assert personal_active_pro_customer.plan == pro_plan

    @patch("billing.views.get_razorpay_client")
    def test_cancelled_event_downgrades_plan(
        self,
        mock_client,
        api_client,
        personal_active_pro_customer,
        free_plan,
    ):
        personal_active_pro_customer.razorpay_subscription_id = "sub_pro123"
        personal_active_pro_customer.save()

        payload = {
            "event": "subscription.cancelled",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_pro123",
                        "status": "cancelled",
                    }
                }
            },
        }

        mock_rz = MagicMock()
        mock_client.return_value = mock_rz
        with patch.object(mock_rz.utility, "verify_webhook_signature", return_value=None):
             response = self._post_webhook(api_client, payload)

        assert response.status_code == status.HTTP_200_OK
        personal_active_pro_customer.refresh_from_db()
        assert personal_active_pro_customer.plan == free_plan


# ── Model tests ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSubscriptionModel:

    def test_is_active_true_when_active(self, personal_active_pro_customer):
        assert personal_active_pro_customer.status == 'active'

    def test_str_representation(self, personal_active_pro_customer, user):
        assert user.email in str(personal_active_pro_customer)
        assert "Pro" in str(personal_active_pro_customer)
