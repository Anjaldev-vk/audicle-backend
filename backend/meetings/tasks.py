import logging
from datetime import timedelta

from django.conf import settings
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
    stale_failed = _fail_stale_bot_joining_meetings(now)
    window_end   = now + timedelta(minutes=5)

    meetings = (
        Meeting.objects
        .filter(
            status       = Meeting.Status.SCHEDULED,
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
                meeting.save(update_fields=["status", "updated_at"])
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
    return {
        "dispatched": dispatched,
        "total": count,
        "stale_failed": stale_failed,
    }


def _fail_stale_bot_joining_meetings(now):
    """
    Mark bot join attempts as failed when they never progress to recording.
    """
    from meetings.models import Meeting

    timeout_minutes = getattr(settings, "BOT_JOINING_TIMEOUT_MINUTES", 10)
    stale_cutoff = now - timedelta(minutes=timeout_minutes)

    stale_qs = Meeting.objects.filter(
        status=Meeting.Status.BOT_JOINING,
        updated_at__lte=stale_cutoff,
        is_archived=False,
    )
    stale_count = stale_qs.update(
        status=Meeting.Status.FAILED,
        updated_at=now,
    )

    if stale_count:
        logger.warning(
            "auto_dispatch_bots: marked %s stale bot join attempt(s) as failed",
            stale_count,
        )

    return stale_count


@shared_task(name="meetings.download_and_upload_audio", bind=True, max_retries=3)
def download_and_upload_audio(self, bot_id, meeting_id):
    """
    Background task to fetch audio from Recall.ai and move it to S3.
    This replaces the local polling logic in the bot_service.
    """
    from meetings.models import Meeting
    import requests
    import boto3
    import tempfile
    import os
    import uuid
    from utils.kafka_producer import send_transcription_task

    try:
        meeting = Meeting.objects.get(id=meeting_id)
    except Meeting.DoesNotExist:
        logger.error("download_and_upload_audio: Meeting %s not found", meeting_id)
        return

    RECALL_API_KEY = os.environ.get('RECALL_API_KEY')
    RECALL_BASE_URL = 'https://us-west-2.recall.ai/api/v1'
    
    # 1. Get Audio URL
    headers = {'Authorization': f'Token {RECALL_API_KEY}'}
    try:
        resp = requests.get(f'{RECALL_BASE_URL}/bot/{bot_id}/', headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        recordings = data.get('recordings', [])
        audio_url = None
        for recording in recordings:
            shortcuts = recording.get('media_shortcuts') or {}
            
            # 1. Try audio_mixed
            audio = shortcuts.get('audio_mixed') or {}
            audio_url = audio.get('data', {}).get('download_url')
            if audio_url:
                break
            
            # 2. Fallback to video_mixed
            video = shortcuts.get('video_mixed') or {}
            audio_url = video.get('data', {}).get('download_url')
            if audio_url:
                logger.info("download_and_upload_audio: Using video_mixed fallback for bot %s", bot_id)
                break
        
        if not audio_url:
            logger.error("No audio URL found for bot %s", bot_id)
            return

        # 2. Download and Upload to S3
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
            with requests.get(audio_url, stream=True, timeout=300) as r:
                for chunk in r.iter_content(chunk_size=8192):
                    tmp.write(chunk)
            tmp_path = tmp.name

        try:
            s3_key = f'meetings/{meeting_id}/audio_{uuid.uuid4().hex[:8]}.mp3'
            s3 = boto3.client(
                's3',
                region_name=settings.AWS_S3_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            )
            s3.upload_file(tmp_path, settings.AWS_STORAGE_BUCKET_NAME, s3_key)
            
            # 3. Update Meeting
            meeting.audio_s3_key = s3_key
            meeting.status = Meeting.Status.PROCESSING
            meeting.save()
            
            # 4. Trigger Transcription
            send_transcription_task(
                meeting_id=str(meeting.id),
                file_path=s3_key,
                user_id=str(meeting.created_by_id)
            )
            
        finally:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception as exc:
        logger.error("download_and_upload_audio failed for bot %s: %s", bot_id, exc)
        raise self.retry(exc=exc, countdown=60)
