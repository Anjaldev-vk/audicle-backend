from rest_framework import serializers
from .models import ActionItem


class ActionItemSerializer(serializers.ModelSerializer):
    assigned_to_email = serializers.EmailField(
        source='assigned_to.email', read_only=True
    )
    assigned_to_name = serializers.CharField(
        source='assigned_to.full_name', read_only=True
    )
    meeting_title = serializers.CharField(
        source='meeting.title', read_only=True
    )

    class Meta:
        model = ActionItem
        fields = [
            'id', 'meeting', 'meeting_title',
            'organisation', 'created_by',
            'assigned_to', 'assigned_to_email', 'assigned_to_name',
            'text', 'due_date', 'status', 'source',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'organisation', 'created_by',
            'source', 'created_at', 'updated_at',
            'meeting_title', 'assigned_to_email', 'assigned_to_name',
        ]


class ActionItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionItem
        fields = ['text', 'due_date', 'assigned_to']


class ActionItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionItem
        fields = ['text', 'due_date', 'status', 'assigned_to']
