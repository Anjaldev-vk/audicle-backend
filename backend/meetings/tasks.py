import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("meetings")


@shared_task(
    name="meetings.auto_dispatch_bots",
    bind=True,
    max_retries=0,
)
def auto_dispatch_bots_task(self):
    """
    Celery Beat task — runs every 5 minutes.

    Finds all meetings where:
        - status = SCHEDULED
        - platform != upload (no bot needed for uploads)
        - meeting_url is set
        - scheduled_at falls within the ±5-minute dispatch window
        - not archived

    For each matching meeting:
        1. Sends a Kafka message to bot_tasks topic
        2. Updates meeting.status = BOT_JOINING
    """
    from meetings.models import Meeting
    from utils.kafka_producer import send_bot_task

    now          = timezone.now()
    window_start = now - timedelta(minutes=5)
    window_end   = now + timedelta(minutes=5)

    meetings = (
        Meeting.objects
        .filter(
            status       = Meeting.Status.SCHEDULED,
            scheduled_at__gte = window_start,
            scheduled_at__lte = window_end,
            is_archived  = False,
        )
        .exclude(platform=Meeting.Platform.UPLOAD)
        .exclude(meeting_url__isnull=True)
        .exclude(meeting_url="")
    )

    count = meetings.count()
    logger.info("auto_dispatch_bots: found %s meeting(s) to dispatch", count)

    dispatched = 0
    for meeting in meetings:
        try:
            success = send_bot_task(
                meeting_id   = str(meeting.id),
                meeting_url  = meeting.meeting_url,
                platform     = meeting.platform,
                duration_cap = 3600,
            )
            if success:
                meeting.status = Meeting.Status.BOT_JOINING
                meeting.save(update_fields=["status"])
                dispatched += 1
                logger.info(
                    "auto_dispatch_bots: dispatched bot for meeting %s",
                    meeting.id,
                )
        except Exception as exc:
            logger.error(
                "auto_dispatch_bots: failed to dispatch meeting %s: %s",
                meeting.id,
                exc,
            )

    logger.info(
        "auto_dispatch_bots: dispatched %s/%s meeting(s)",
        dispatched,
        count,
    )
    return {"dispatched": dispatched, "total": count}
