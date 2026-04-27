from rest_framework import serializers
from .models import EmbeddingChunk, ChatSession, ChatMessage


class EmbeddingChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmbeddingChunk
        fields = [
            'id', 'meeting', 'chunk_index',
            'chunk_text', 'start_seconds', 'end_seconds',
            'created_at'
        ]
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'role', 'content', 'sources', 'created_at']
        read_only_fields = ['id', 'role', 'sources', 'created_at']


class ChatSessionSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = [
            'id', 'title', 'message_count',
            'messages', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'title', 'created_at', 'updated_at']

    def get_message_count(self, obj):
        return obj.messages.count()


class ChatSessionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for session list — no messages."""
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'message_count', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_message_count(self, obj):
        return obj.messages.count()


class RAGSearchSerializer(serializers.Serializer):
    query = serializers.CharField(
        max_length=1000,
        help_text="Natural language search query"
    )
    meeting_id = serializers.UUIDField(
        required=False,
        help_text="Optional — scope search to a single meeting"
    )
    limit = serializers.IntegerField(
        required=False,
        default=5,
        min_value=1,
        max_value=20,
        help_text="Number of chunks to retrieve (default 5)"
    )


class ChatMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(
        max_length=2000,
        help_text="User message content"
    )
    meeting_id = serializers.UUIDField(
        required=False,
        help_text="Optional — scope context to a single meeting"
    )


class InternalEmbedSerializer(serializers.Serializer):
    transcript_id = serializers.UUIDField()
    chunks = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of {chunk_text, chunk_index, start_seconds, end_seconds, embedding}"
    )