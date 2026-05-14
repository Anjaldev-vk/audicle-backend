import logging
import razorpay
import datetime
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema

from accounts.models import Organisation
from utils.response import success_response, error_response
from .models import Subscription, Plan
from .serializers import (
    BillingStatusSerializer,
    CheckoutRequestSerializer,
    CheckoutResponseSerializer,
    UsageSerializer,
    VerifySubscriptionSerializer,
)

logger = logging.getLogger(__name__)


def get_razorpay_client():
    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )


def get_or_create_subscription(user, organisation=None):
    """
    Finds or creates a Subscription for the given user/org.
    Ensures a real Razorpay Customer ID exists if they are upgrading.
    Note: Free subscriptions are created by signals, so this mainly 
    handles ensuring the razorpay_customer_id is present when needed.
    """
    # 1. Get the existing subscription (should exist due to signals)
    sub = Subscription.objects.filter(
        user=user if not organisation else None,
        organisation=organisation
    ).first()

    if not sub:
        # Fallback if signal failed — only create if Free plan exists
        free_plan = Plan.objects.filter(name='Free').first()
        if free_plan:
            sub = Subscription.objects.create(
                user=user if not organisation else None,
                organisation=organisation,
                plan=free_plan,
                status='active'
            )

    return sub


@extend_schema(tags=["Billing"])
class BillingPlanView(APIView):
    """
    GET /api/v1/billing/plan/
    Returns current plan, usage, and available upgrade options.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        org = request.organisation

        # Always query fresh from DB to avoid OneToOneField descriptor caching
        if org:
            subscription = Subscription.objects.filter(organisation=org).first()
        else:
            subscription = Subscription.objects.filter(user=user, organisation__isnull=True).first()

        if not subscription:
            # Fallback for older accounts: assign Free plan on the fly
            free_plan = Plan.objects.filter(name='Free').first()
            if not free_plan:
                return error_response(
                    message="System plans not initialized",
                    code="plans_not_found",
                    status_code=500
                )
            subscription = Subscription.objects.create(
                user=user if not org else None,
                organisation=org,
                plan=free_plan,
                status='active'
            )

        plan = subscription.plan
        meetings_used = org.meetings_this_month if org else user.meetings_this_month
        max_meetings = plan.meeting_limit
        
        usage_pct = 0
        if max_meetings > 0:
            usage_pct = (meetings_used / max_meetings * 100)
        elif max_meetings == -1:
            usage_pct = 0 # Unlimited
        
        available_plans = Plan.objects.exclude(name='Free').order_by('price')

        # Manually serialize the available plans
        from .serializers import PlanInfoSerializer, BillingStatusSerializer
        available_plans_data = PlanInfoSerializer(available_plans, many=True).data

        data = {
            "plan": plan.name,
            "meetings_used": meetings_used,
            "usage_percentage": round(usage_pct, 2),
            "meetings_limit": max_meetings,
            "bot_access": plan.bot_access,
            "rag_access": plan.rag_access,
            "subscription_status": subscription.status,
            "current_period_end": subscription.current_period_end,
            "available_plans": available_plans_data
        }
        
        return success_response(message="Billing plan retrieved", data=data)


@extend_schema(tags=["Billing"])
class BillingCheckoutView(APIView):
    """
    POST /api/v1/billing/checkout/
    Initializes a Razorpay subscription and returns the ID.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Invalid plan selected",
                code="invalid_plan",
                status_code=400,
                errors=serializer.errors
            )
            
        plan_name = serializer.validated_data["plan"]
        
        if plan_name == "Free":
            return error_response(
                message="Free plan cannot be purchased",
                code="free_plan_not_purchasable",
                status_code=400
            )
            
        target_plan = Plan.objects.filter(name=plan_name).first()
        
        if not target_plan:
            return error_response(
                message="Plan does not exist",
                code="invalid_plan",
                status_code=400
            )
            
        if not target_plan.razorpay_plan_id:
            return error_response(
                message="Plan not configured for checkout",
                code="plan_not_configured",
                status_code=503
            )
            
        subscription = get_or_create_subscription(request.user, request.organisation)
        client = get_razorpay_client()
        
        # Ensure we have a customer ID (if not, we might need to create it here or elsewhere)
        # For simplicity, we'll assume the razorpay_customer_id is managed during the first checkout
        # Or we can create it on the fly:
        
        # notes for Razorpay
        notes = {
            "user_id": str(request.user.id),
            "org_id": str(request.organisation.id) if request.organisation else "personal",
            "plan_name": plan_name
        }

        try:
            # In a real app, you'd check if customer already exists in Razorpay
            # For now, let's just create the subscription
            rz_subscription = client.subscription.create({
                "plan_id": target_plan.razorpay_plan_id,
                "total_count": 12, # 1 year of monthly renewals
                "quantity": 1,
                "notes": notes
            })
            
            subscription.razorpay_subscription_id = rz_subscription["id"]
            subscription.status = rz_subscription["status"]
            subscription.save(update_fields=["razorpay_subscription_id", "status"])
            
            return success_response(message="Checkout initialized", data={
                "subscription_id": rz_subscription["id"],
                "razorpay_key": settings.RAZORPAY_KEY_ID,
                "plan": plan_name
            })
            
        except Exception as e:
            logger.error("Failed to create Razorpay subscription: %s", str(e))
            return error_response(
                message="Could not initialize subscription",
                code="razorpay_error",
                status_code=503
            )


@extend_schema(tags=["Billing"])
class BillingUsageView(APIView):
    """
    GET /api/v1/billing/usage/
    Returns detailed usage metrics.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        org = request.organisation

        # Always query fresh from DB to avoid OneToOneField descriptor caching
        if org:
            subscription = Subscription.objects.filter(organisation=org).first()
        else:
            subscription = Subscription.objects.filter(user=user, organisation__isnull=True).first()

        if not subscription:
            return error_response(
                message="No active subscription",
                code="no_subscription",
                status_code=404
            )
            
        plan = subscription.plan
        meetings_used = org.meetings_this_month if org else user.meetings_this_month
        max_meetings = plan.meeting_limit
        
        usage_pct = 0
        if max_meetings > 0:
            usage_pct = (meetings_used / max_meetings * 100)
        
        data = {
            "plan": plan.name,
            "meetings_used": meetings_used,
            "meetings_limit": max_meetings,
            "usage_percentage": round(usage_pct, 1)
        }
        return success_response(message="Usage retrieved", data=data)


@extend_schema(tags=["Billing"])
class BillingCancelView(APIView):
    """
    POST /api/v1/billing/cancel/
    Cancels the current active subscription.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = request.organisation
        user = request.user

        # Always query fresh from DB to avoid OneToOneField descriptor caching
        if org:
            subscription = Subscription.objects.filter(organisation=org).first()
        else:
            subscription = Subscription.objects.filter(user=user, organisation__isnull=True).first()

        if not subscription or not subscription.razorpay_subscription_id:
            return error_response(
                message="No active subscription found",
                code="no_subscription",
                status_code=404
            )
            
        client = get_razorpay_client()
        
        try:
            client.subscription.cancel(subscription.razorpay_subscription_id, {
                "cancel_at_cycle_end": 1
            })
            
            subscription.status = "cancelled"
            subscription.save(update_fields=["status"])
            
            return success_response(message="Subscription cancellation requested")
            
        except Exception as e:
            logger.error("Failed to cancel Razorpay subscription: %s", str(e))
            return error_response(
                message="Failed to cancel subscription with Razorpay",
                code="razorpay_error",
                status_code=503
            )


@extend_schema(tags=["Billing"])
class BillingWebhookView(APIView):
    """
    POST /api/v1/billing/webhook/
    Handles Razorpay events (subscription.activated, subscription.cancelled, etc.)
    """
    authentication_classes = [] 
    permission_classes = []      

    def post(self, request):
        signature = request.META.get("HTTP_X_RAZORPAY_SIGNATURE")
        if not signature:
            return error_response(message="Missing signature", code="missing_signature", status_code=400)

        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        if not webhook_secret:
            logger.error("RAZORPAY_WEBHOOK_SECRET is not configured.")
            return error_response(
                message="Webhook verification failed",
                code="webhook_config_error",
                status_code=503
            )

        client = get_razorpay_client()

        try:
            client.utility.verify_webhook_signature(
                request.body.decode("utf-8"),
                signature,
                webhook_secret,
            )
        except Exception:
            return error_response(
                message="Invalid signature",
                code="invalid_signature",
                status_code=403
            )

        data = request.data
        event = data.get("event")
        payload = data.get("payload", {}).get("subscription", {}).get("entity", {})
        subscription_id = payload.get("id")
        
        if not subscription_id:
            return success_response(message="Ignored")

        subscription = Subscription.objects.filter(
            razorpay_subscription_id=subscription_id
        ).first()
        
        if not subscription:
            return success_response(message="Subscription not found")

        if event == "subscription.activated":
            self._handle_activated(subscription, payload)
        elif event == "subscription.cancelled":
            self._handle_cancelled(subscription, payload)
        elif event == "subscription.charged":
            self._handle_charged(subscription, payload)
            
        return success_response(message="Webhook processed")

    def _handle_activated(self, subscription, payload):
        subscription.status = "active"
        
        # Find which plan this corresponds to
        plan_id = payload.get("plan_id")
        plan = Plan.objects.filter(razorpay_plan_id=plan_id).first()
        if plan:
            subscription.plan = plan
        
        if payload.get("current_start"):
            subscription.current_period_start = datetime.datetime.fromtimestamp(payload["current_start"], tz=datetime.timezone.utc)
        if payload.get("current_end"):
            subscription.current_period_end = datetime.datetime.fromtimestamp(payload["current_end"], tz=datetime.timezone.utc)
            
        subscription.save()
        logger.info("Subscription %s activated", subscription.id)

    def _handle_cancelled(self, subscription, payload):
        subscription.status = "cancelled"
        free_plan = Plan.objects.filter(name='Free').first()
        if free_plan:
            subscription.plan = free_plan
        subscription.save()
        logger.info("Subscription %s cancelled", subscription.id)

    def _handle_charged(self, subscription, payload):
        if payload.get("current_end"):
            subscription.current_period_end = datetime.datetime.fromtimestamp(payload["current_end"], tz=datetime.timezone.utc)
            subscription.save(update_fields=["current_period_end"])


@extend_schema(tags=["Billing"])
class BillingVerifyView(APIView):
    """
    POST /api/v1/billing/verify/
    Verifies Razorpay payment signature after successful checkout.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifySubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        razorpay_order_id = serializer.validated_data["razorpay_order_id"]
        razorpay_payment_id = serializer.validated_data["razorpay_payment_id"]
        razorpay_signature = serializer.validated_data["razorpay_signature"]

        client = get_razorpay_client()
        
        try:
            # Verify the signature
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })
            
            # Find the subscription
            subscription = Subscription.objects.filter(
                razorpay_subscription_id=razorpay_order_id
            ).first()
            
            if subscription:
                subscription.status = 'active'
                subscription.save()
                
            return success_response(message="Payment verified successfully")
            
        except Exception as e:
            logger.error("Razorpay verification failed: %s", str(e))
            return error_response(
                message="Payment verification failed",
                code="verification_failed",
                status_code=400
            )
