from django.contrib import admin
from .models import EmbeddingChunk, ChatSession, ChatMessage


@admin.register(EmbeddingChunk)
class EmbeddingChunkAdmin(admin.ModelAdmin):
    list_display = ['id', 'meeting', 'chunk_index', 'created_at']
    list_filter = ['organisation']
    raw_id_fields = ['transcript', 'meeting', 'organisation', 'created_by']


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'title', 'created_at', 'updated_at']
    list_filter = ['organisation']
    raw_id_fields = ['user', 'organisation']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'role', 'created_at']
    list_filter = ['role']
    raw_id_fields = ['session']