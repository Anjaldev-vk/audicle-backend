import logging
import os
import requests

from platforms.recall_bot import RecallBot

logger = logging.getLogger(__name__)


class BotRunner:
    """
    Lightweight bot orchestrator.
    Responsibilities:
    - Start Recall.ai bot
    - Save recall_bot_id to Django
    - Watch for completion
    - Notify Django when done
    
    Audio download + S3 upload is handled by
    Django's download_and_upload_audio Celery task
    via webhook — NOT here.
    """

    def __init__(self, meeting_id, meeting_url, platform, duration_cap, django_url, internal_secret):
        self.meeting_id      = meeting_id
        self.meeting_url     = meeting_url
        self.platform        = platform
        self.duration_cap    = duration_cap
        self.django_url      = django_url
        self.internal_secret = internal_secret

    def run(self) -> None:

        # 1. Notify Django — joining
        self._post_status('bot_joining')

        # 2. Start Recall bot
        bot = RecallBot(
            meeting_url=self.meeting_url,
            meeting_id=self.meeting_id,
            on_recording_started=lambda: self._post_status('recording'),
        )

        bot_id = bot.start()
        if not bot_id:
            self._post_status('failed', 'Recall bot failed to start')
            return

        # 3. Save recall_bot_id to Django immediately
        self._post_status('bot_joining', recall_bot_id=bot_id)
        logger.info('Recall bot started: %s for meeting %s', bot_id, self.meeting_id)

        # 4. Just watch — webhook handles everything else
        success = bot.wait_for_completion(timeout=self.duration_cap)
        if not success:
            self._post_status('failed', 'Recall bot did not complete')
            bot.stop()
            return

        # 5. Done — webhook already triggered audio download
        logger.info(
            'Bot finished for meeting %s — webhook handling audio',
            self.meeting_id
        )

    def _post_status(self, status, error_message=None, recall_bot_id=None):
        payload = {'meeting_id': self.meeting_id, 'status': status}
        if error_message:
            payload['error_message'] = error_message
        if recall_bot_id:
            payload['recall_bot_id'] = recall_bot_id

        try:
            requests.post(
                f'{self.django_url}/internal/bot/status/',
                json=payload,
                headers={
                    'Content-Type': 'application/json',
                    'X-Internal-Secret': self.internal_secret,
                },
                timeout=15,
            )
            logger.info('Status posted: %s for meeting %s', status, self.meeting_id)
        except requests.RequestException as exc:
            logger.error('Failed to POST status: %s', exc)
