from rest_framework import serializers
from .models import Plan, RazorpayCustomer


class PlanInfoSerializer(serializers.Serializer):
    plan = serializers.CharField()
    name = serializers.CharField()
    meetings_limit = serializers.IntegerField()
    max_workspaces = serializers.IntegerField()
    max_members = serializers.IntegerField()
    bot_access = serializers.BooleanField()
    rag_access = serializers.BooleanField()
    price_id = serializers.CharField(required=False)


class BillingStatusSerializer(serializers.Serializer):
    plan = serializers.CharField()
    meetings_used = serializers.IntegerField()
    meetings_limit = serializers.IntegerField()
    usage_percentage = serializers.FloatField()
    subscription_status = serializers.CharField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    available_plans = PlanInfoSerializer(many=True)


class CheckoutRequestSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=[Plan.PRO, Plan.ENTERPRISE])


class CheckoutResponseSerializer(serializers.Serializer):
    subscription_id = serializers.CharField()
    razorpay_key = serializers.CharField()
    plan = serializers.CharField()


class UsageSerializer(serializers.Serializer):
    plan = serializers.CharField()
    meetings_used = serializers.IntegerField()
    meetings_limit = serializers.IntegerField()
    usage_percentage = serializers.FloatField()
