import uuid
from django.db import models
from django.conf import settings


class ActionItem(models.Model):

    class Status(models.TextChoices):
        OPEN = 'open',        'Open'
        IN_PROGRESS = 'in_progress', 'In Progress'
        DONE = 'done',        'Done'

    class Source(models.TextChoices):
        AI_GENERATED = 'ai_generated', 'AI Generated'
        MANUAL = 'manual',       'Manual'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(
        'meetings.Meeting',
        on_delete=models.CASCADE,
        related_name='action_items',
    )
    organisation = models.ForeignKey(
        'accounts.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='action_items',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_action_items',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_action_items',
    )
    text = models.TextField()
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['meeting']),
            models.Index(fields=['organisation']),
            models.Index(fields=['assigned_to']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return '%s — %s' % (self.meeting.title, self.text[:50])
