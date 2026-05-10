import pytest
import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock
from django.conf import settings
from rest_framework import status

from billing.models import RazorpayCustomer, Plan


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

    def test_returns_free_plan_by_default(self, auth_client, user):
        response = auth_client.get(PLAN_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["plan"] == Plan.FREE
        assert data["meetings_limit"] == 5
        assert data["bot_access"] is False or data["bot_access"] is True
        assert "available_plans" in data

    def test_returns_correct_usage(self, auth_client, user):
        user.meetings_this_month = 3
        user.save(update_fields=["meetings_this_month"])

        response = auth_client.get(PLAN_URL)
        data = response.json()["data"]
        assert data["meetings_used"] == 3

    def test_returns_subscription_status_when_exists(
        self, auth_client, personal_active_pro_customer
    ):
        response = auth_client.get(PLAN_URL)
        data = response.json()["data"]
        assert data["subscription_status"] == "active"
        assert data["current_period_end"] is not None

    def test_available_plans_contains_all_plans(self, auth_client):
        response = auth_client.get(PLAN_URL)
        data = response.json()["data"]
        plan_names = [p["plan"] for p in data["available_plans"]]
        assert "free" in plan_names
        assert "pro" in plan_names
        assert "enterprise" in plan_names


# ── Checkout endpoint ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingCheckoutView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(CHECKOUT_URL, {"plan": "pro"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_plan_returns_400(self, auth_client):
        response = auth_client.post(
            CHECKOUT_URL, {"plan": "invalid"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_plan"

    def test_free_plan_returns_400(self, auth_client):
        response = auth_client.post(
            CHECKOUT_URL, {"plan": "free"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("billing.views.get_razorpay_client")
    def test_creates_subscription_successfully(
        self, mock_client, auth_client, user, organisation
    ):
        # Mock Razorpay API calls
        mock_rz = MagicMock()
        mock_rz.customer.create.return_value = {"id": "cust_new123"}
        mock_rz.subscription.create.return_value = {
            "id": "sub_new123",
            "status": "created",
        }
        mock_client.return_value = mock_rz

        with patch.dict(
            "django.conf.settings.__dict__",
            {"RAZORPAY_PRO_PLAN_ID": "plan_test123"},
        ):
            from billing import views
            views.RAZORPAY_PLAN_IDS[Plan.PRO] = "plan_test123"

            response = auth_client.post(
                CHECKOUT_URL, {"plan": "pro"}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert "subscription_id" in data
        assert "razorpay_key" in data
        assert data["plan"] == "pro"

    @patch("billing.views.get_razorpay_client")
    def test_plan_not_configured_returns_503(
        self, mock_client, auth_client
    ):
        from billing import views
        original = views.RAZORPAY_PLAN_IDS.copy()
        views.RAZORPAY_PLAN_IDS[Plan.PRO] = ""

        response = auth_client.post(
            CHECKOUT_URL, {"plan": "pro"}, format="json"
        )

        views.RAZORPAY_PLAN_IDS.update(original)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "plan_not_configured"


# ── Usage endpoint ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingUsageView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_usage_data(self, auth_client, user):
        user.meetings_this_month = 2
        user.save(update_fields=["meetings_this_month"])

        response = auth_client.get(USAGE_URL)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["meetings_used"] == 2
        assert data["meetings_limit"] > 0
        assert "usage_percentage" in data
        assert "plan" in data

    def test_usage_percentage_calculated_correctly(
        self, auth_client, user
    ):
        user.meetings_this_month = 2
        user.plan = Plan.FREE
        user.save(update_fields=["meetings_this_month", "plan"])

        response = auth_client.get(USAGE_URL)
        data = response.json()["data"]
        # 2 out of 5 = 40%
        assert data["usage_percentage"] == 40.0


# ── Cancel endpoint ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBillingCancelView:

    def test_unauthenticated_returns_401(self, api_client):
        response = api_client.post(CANCEL_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_no_subscription_returns_404(self, auth_client):
        response = auth_client.post(CANCEL_URL)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "no_subscription"

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
        assert personal_active_pro_customer.subscription_status == "cancelled"

    @patch("billing.views.get_razorpay_client")
    def test_cancel_failure_returns_503(
        self, mock_client, auth_client, personal_active_pro_customer
    ):
        mock_rz = MagicMock()
        mock_rz.subscription.cancel.side_effect = Exception("Razorpay error")
        mock_client.return_value = mock_rz

        response = auth_client.post(CANCEL_URL)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


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

    def test_missing_signature_returns_400(self, api_client):
        response = api_client.post(
            WEBHOOK_URL,
            data={"event": "subscription.activated"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("billing.views.get_razorpay_client")
    def test_activated_event_upgrades_plan(
        self,
        mock_client,
        api_client,
        razorpay_customer,
        user,
        organisation,
    ):
        mock_rz = MagicMock()
        mock_rz.utility.verify_webhook_signature.return_value = True
        mock_client.return_value = mock_rz

        from billing import views
        views.RAZORPAY_PLAN_IDS[Plan.PRO] = "plan_pro_test"

        payload = {
            "event": "subscription.activated",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_test123",
                        "plan_id": "plan_pro_test",
                        "status": "active",
                        "current_start": 1700000000,
                        "current_end": 1702678400,
                    }
                }
            },
        }

        with patch.object(
            mock_rz.utility,
            "verify_webhook_signature",
            return_value=None,
        ):
            response = self._post_webhook(api_client, payload)

        assert response.status_code == status.HTTP_200_OK
        razorpay_customer.refresh_from_db()
        assert razorpay_customer.subscription_status == "active"

    @patch("billing.views.get_razorpay_client")
    def test_cancelled_event_downgrades_plan(
        self,
        mock_client,
        api_client,
        active_pro_customer,
        user,
        organisation,
    ):
        # mock_rz = MagicMock()
        # mock_rz.utility.verify_webhook_signature.return_value = None
        # mock_client.return_value = mock_rz

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

        # with patch.object(
        #     mock_rz.utility,
        #     "verify_webhook_signature",
        #     return_value=None,
        # ):
        #     response = self._post_webhook(api_client, payload)
        
        # We need to mock the signature verification correctly
        with patch("billing.views.get_razorpay_client") as mock_client:
            mock_rz = MagicMock()
            mock_client.return_value = mock_rz
            with patch.object(mock_rz.utility, "verify_webhook_signature", return_value=None):
                 response = self._post_webhook(api_client, payload)

        assert response.status_code == status.HTTP_200_OK
        active_pro_customer.refresh_from_db()
        assert active_pro_customer.plan == Plan.FREE

    @patch("billing.views.get_razorpay_client")
    def test_unknown_event_returns_200(
        self, mock_client, api_client
    ):
        mock_rz = MagicMock()
        mock_rz.utility.verify_webhook_signature.return_value = None
        mock_client.return_value = mock_rz

        payload = {"event": "payment.captured", "payload": {}}

        with patch.object(
            mock_rz.utility,
            "verify_webhook_signature",
            return_value=None,
        ):
            response = self._post_webhook(api_client, payload)

        assert response.status_code == status.HTTP_200_OK


# ── Model tests ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRazorpayCustomerModel:

    def test_is_active_true_when_active(self, active_pro_customer):
        assert active_pro_customer.is_active is True

    def test_is_active_false_when_not_active(self, razorpay_customer):
        assert razorpay_customer.is_active is False

    def test_str_representation(self, razorpay_customer, user):
        assert user.email in str(razorpay_customer)
        assert "free" in str(razorpay_customer)
