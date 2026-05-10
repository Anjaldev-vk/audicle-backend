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

            items_to_create = []
            for item in raw_items:
                text = ""
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = item.get('task', item.get('text', '')).strip()
                
                if text:
                    items_to_create.append(
                        ActionItem(
                            meeting=summary.meeting,
                            organisation=summary.meeting.organisation,
                            created_by=summary.created_by,
                            text=text,
                            source=ActionItem.Source.AI_GENERATED,
                            status=ActionItem.Status.PENDING,
                        )
                    )

            new_items = ActionItem.objects.bulk_create(items_to_create)
            logger.info(
                'populate_action_items: created %s items for meeting %s',
                len(items_to_create),
                summary.meeting.id,
            )

            # 3. Trigger analytics for each new item
            from analytics.tasks import track_action_item_created
            workspace_id = (
                str(summary.meeting.organisation.id)
                if summary.meeting.organisation
                else str(summary.created_by.id)
            )
            for item in new_items:
                track_action_item_created.delay(
                    action_item_id=str(item.id),
                    user_id=str(summary.created_by.id),
                    workspace_id=workspace_id,
                )
    except Exception as exc:
        logger.error(
            'populate_action_items failed for summary %s: %s', summary_id, exc
        )
        raise self.retry(exc=exc, countdown=5)
