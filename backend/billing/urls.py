from django.urls import path
from .views import (
    BillingPlanView,
    BillingCheckoutView,
    BillingUsageView,
    BillingCancelView,
    BillingWebhookView,
    BillingVerifyView,
)

urlpatterns = [
    path("plan/", BillingPlanView.as_view(), name="billing-plan"),
    path("checkout/", BillingCheckoutView.as_view(), name="billing-checkout"),
    path("usage/", BillingUsageView.as_view(), name="billing-usage"),
    path("cancel/", BillingCancelView.as_view(), name="billing-cancel"),
    path("webhook/", BillingWebhookView.as_view(), name="billing-webhook"),
    path("verify/", BillingVerifyView.as_view(), name="billing-verify"),
]
