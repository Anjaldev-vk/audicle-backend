from django.contrib import admin
from .models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'meeting_limit', 'razorpay_plan_id')
    search_fields = ('name', 'razorpay_plan_id')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('get_target', 'plan', 'status', 'current_period_end')
    list_filter = ('status', 'plan')
    search_fields = ('user__email', 'organisation__name', 'razorpay_subscription_id')

    def get_target(self, obj):
        return obj.user.email if obj.user else obj.organisation.name
    get_target.short_description = 'Target (User/Org)'
