from celery import shared_task
from django.db import transaction
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def populate_action_items_from_summary(self, summary_id):
    """
    Parse action_items from MeetingSummary and create ActionItem rows.
    Called automatically when a summary completes.
    """
    from transcripts.models import MeetingSummary
    from .models import ActionItem

    try:
        summary = (
            MeetingSummary.objects
            .select_related('meeting', 'created_by', 'meeting__organisation')
            .get(id=summary_id)
        )
    except MeetingSummary.DoesNotExist:
        logger.error('populate_action_items: summary %s not found', summary_id)
        return

    raw_items = summary.action_items  # JSON list of strings
    if not raw_items or not isinstance(raw_items, list):
        logger.info(
            'populate_action_items: no action items in summary %s', summary_id
        )
        return

    try:
        with transaction.atomic():
            # Avoid duplicates — delete existing AI-generated items first
            ActionItem.objects.filter(
                meeting=summary.meeting,
                source=ActionItem.Source.AI_GENERATED,
            ).delete()

            items_to_create = [
                ActionItem(
                    meeting=summary.meeting,
                    organisation=summary.meeting.organisation,
                    created_by=summary.created_by,
                    text=item.strip(),
                    source=ActionItem.Source.AI_GENERATED,
                    status=ActionItem.Status.OPEN,
                )
                for item in raw_items
                if isinstance(item, str) and item.strip()
            ]
            ActionItem.objects.bulk_create(items_to_create)
            logger.info(
                'populate_action_items: created %s items for meeting %s',
                len(items_to_create),
                summary.meeting.id,
            )
    except Exception as exc:
        logger.error(
            'populate_action_items failed for summary %s: %s', summary_id, exc
        )
        raise self.retry(exc=exc, countdown=5)
