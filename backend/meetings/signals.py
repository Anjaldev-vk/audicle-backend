import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_save
from django.dispatch import receiver

from meetings.models import Meeting
from transcripts.models import Transcript, MeetingSummary
from rag.models import EmbeddingChunk

logger = logging.getLogger("meetings")


def _push(group_name: str, message: dict):
    channel_layer = get_channel_layer()
    try:
        async_to_sync(channel_layer.group_send)(group_name, message)
    except Exception as e:
        logger.error("channel_layer.group_send failed for %s: %s", group_name, str(e))


@receiver(post_save, sender=Meeting)
def meeting_status_changed(sender, instance, **kwargs):
    group = f"meeting_{instance.id}"
    _push(group, {
        "type": "meeting.status_update",
        "meeting_id": str(instance.id),
        "status": instance.status,
    })
    logger.info("Pushed meeting.status_update for meeting %s → %s", instance.id, instance.status)


@receiver(post_save, sender=Transcript)
def transcript_status_changed(sender, instance, **kwargs):
    group = f"meeting_{instance.meeting_id}"
    _push(group, {
        "type": "transcript.ready",
        "meeting_id": str(instance.meeting_id),
        "transcript_id": str(instance.id),
        "status": instance.status,
    })
    logger.info("Pushed transcript.ready for meeting %s → %s", instance.meeting_id, instance.status)


@receiver(post_save, sender=MeetingSummary)
def summary_status_changed(sender, instance, **kwargs):
    group = f"meeting_{instance.meeting_id}"
    _push(group, {
        "type": "summary.ready",
        "meeting_id": str(instance.meeting_id),
        "summary_id": str(instance.id),
        "status": instance.status,
    })
    logger.info("Pushed summary.ready for meeting %s → %s", instance.meeting_id, instance.status)


@receiver(post_save, sender=EmbeddingChunk)
def embedding_chunk_saved(sender, instance, **kwargs):
    group = f"meeting_{instance.meeting_id}"
    _push(group, {
        "type": "embedding.ready",
        "meeting_id": str(instance.meeting_id),
    })
