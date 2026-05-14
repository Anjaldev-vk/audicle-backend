from rest_framework import serializers
from .models import Plan, Subscription


class PlanInfoSerializer(serializers.Serializer):
    plan = serializers.CharField(source='name')
    name = serializers.CharField()
    meetings_limit = serializers.IntegerField(source='meeting_limit')
    max_workspaces = serializers.IntegerField()
    max_members = serializers.IntegerField()
    bot_access = serializers.BooleanField()
    rag_access = serializers.BooleanField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    price_id = serializers.CharField(source='razorpay_plan_id', required=False)


class BillingStatusSerializer(serializers.Serializer):
    plan = serializers.CharField()
    meetings_used = serializers.IntegerField()
    meetings_limit = serializers.IntegerField()
    usage_percentage = serializers.FloatField()
    subscription_status = serializers.CharField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    available_plans = PlanInfoSerializer(many=True)


class CheckoutRequestSerializer(serializers.Serializer):
    # Changed to string as plans are now database-driven
    plan = serializers.CharField()


class CheckoutResponseSerializer(serializers.Serializer):
    subscription_id = serializers.CharField()
    razorpay_key = serializers.CharField()
    plan = serializers.CharField()


class UsageSerializer(serializers.Serializer):
    plan = serializers.CharField()
    meetings_used = serializers.IntegerField()
    meetings_limit = serializers.IntegerField()
    usage_percentage = serializers.FloatField()


class VerifySubscriptionSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()
