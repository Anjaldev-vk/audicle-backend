from django.contrib import admin
from .models import ActionItem


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    list_display = ['text', 'meeting', 'status', 'source', 'assigned_to', 'due_date']
    list_filter  = ['status', 'source']
    search_fields = ['text', 'meeting__title']
