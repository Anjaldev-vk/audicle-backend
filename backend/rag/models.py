import uuid
from django.db import models
from pgvector.django import VectorField


class EmbeddingChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transcript = models.ForeignKey(
        'transcripts.Transcript',
        on_delete=models.CASCADE,
        related_name='embedding_chunks'
    )
    meeting = models.ForeignKey(
        'meetings.Meeting',
        on_delete=models.CASCADE,
        related_name='embedding_chunks'
    )
    organisation = models.ForeignKey(
        'accounts.Organisation',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='embedding_chunks'
    )
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='embedding_chunks'
    )
    chunk_text = models.TextField()
    embedding = VectorField(dimensions=768)  # text-embedding-004 (Gemini)
    chunk_index = models.PositiveIntegerField()
    start_seconds = models.FloatField(null=True, blank=True)
    end_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['chunk_index']
        indexes = [
            models.Index(fields=['transcript']),
            models.Index(fields=['meeting']),
            models.Index(fields=['organisation']),
        ]

    def __str__(self):
        return f"Chunk {self.chunk_index} — {self.meeting}"


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='chat_sessions'
    )
    organisation = models.ForeignKey(
        'accounts.Organisation',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_sessions'
    )
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['organisation']),
        ]

    def __str__(self):
        return f"Session {self.id} — {self.user.email}"


class ChatMessage(models.Model):

    class Role(models.TextChoices):
        USER = 'user', 'User'
        ASSISTANT = 'assistant', 'Assistant'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices
    )
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True) 
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session']),
        ]

    def __str__(self):
        return f"{self.role} — Session {self.session_id}"