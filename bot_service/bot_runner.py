import logging
import os
import subprocess
import tempfile
import time
import uuid

import boto3
import requests
from botocore.exceptions import ClientError

from platforms.google_meet import GoogleMeetBot
from platforms.zoom import ZoomBot
from platforms.teams import TeamsBot

logger = logging.getLogger(__name__)

AWS_ACCESS_KEY_ID     = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_BUCKET_NAME       = os.environ.get('AWS_STORAGE_BUCKET_NAME')
AWS_REGION            = os.environ.get('AWS_S3_REGION', 'ap-southeast-2')


class BotRunner:
    """
    Orchestrates the full bot pipeline:
    1. Select platform bot
    2. Join meeting via Playwright
    3. Record audio via ffmpeg
    4. Upload to S3
    5. Notify Django → triggers transcription
    """

    PLATFORM_MAP = {
        'google_meet': GoogleMeetBot,
        'zoom':        ZoomBot,
        'teams':       TeamsBot,
    }

    def __init__(
        self,
        meeting_id: str,
        meeting_url: str,
        platform: str,
        duration_cap: int,
        django_url: str,
        internal_secret: str,
    ):
        self.meeting_id      = meeting_id
        self.meeting_url     = meeting_url
        self.platform        = platform
        self.duration_cap    = duration_cap
        self.django_url      = django_url
        self.internal_secret = internal_secret

    def run(self) -> None:
        """Full pipeline — join, record, upload, notify."""

        # 1. Notify Django — bot is joining
        self._post_status('bot_joining')

        # 2. Select platform bot
        BotClass = self.PLATFORM_MAP.get(self.platform)
        if not BotClass:
            logger.error('Unknown platform: %s', self.platform)
            self._post_status('failed', f'Unknown platform: {self.platform}')
            return

        # 3. Use temp dir for audio recording
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, f'{self.meeting_id}.mp3')

            try:
                # 4. Launch platform bot + start recording
                bot = BotClass(
                    meeting_url=self.meeting_url,
                    audio_output_path=audio_path,
                    duration_cap=self.duration_cap,
                    on_recording_started=lambda: self._post_status('recording'),
                )

                logger.info(
                    'Bot joining %s meeting %s',
                    self.platform,
                    self.meeting_id,
                )

                bot.run()

                logger.info(
                    'Bot finished recording meeting %s',
                    self.meeting_id,
                )

            except Exception as exc:
                logger.error(
                    'Bot failed for meeting %s: %s',
                    self.meeting_id,
                    exc,
                )
                self._post_status('failed', str(exc))
                return

            # 5. Check audio was recorded
            if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
                logger.error(
                    'No audio recorded for meeting %s',
                    self.meeting_id,
                )
                self._post_status('failed', 'No audio recorded')
                return

            # 6. Upload to S3
            s3_key = self._upload_to_s3(audio_path)
            if not s3_key:
                self._post_status('failed', 'S3 upload failed')
                return

            logger.info(
                'Audio uploaded to S3: %s for meeting %s',
                s3_key,
                self.meeting_id,
            )

            # 7. Notify Django — processing, triggers transcription
            self._post_status('processing', s3_key=s3_key)

    def _upload_to_s3(self, local_path: str) -> str | None:
        """Upload audio file to S3. Returns s3_key on success, None on failure."""
        s3_key = f'meetings/{self.meeting_id}/audio_{uuid.uuid4().hex[:8]}.mp3'
        try:
            client = boto3.client(
                's3',
                region_name=AWS_REGION,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            )
            client.upload_file(
                Filename=local_path,
                Bucket=AWS_BUCKET_NAME,
                Key=s3_key,
            )
            logger.info('Uploaded %s to s3://%s/%s', local_path, AWS_BUCKET_NAME, s3_key)
            return s3_key
        except ClientError as exc:
            logger.error('S3 upload failed: %s', exc)
            return None

    def _post_status(
        self,
        status: str,
        error_message: str = None,
        s3_key: str = None,
    ) -> None:
        """POST bot status update to Django internal API."""
        payload = {
            'meeting_id': self.meeting_id,
            'status':     status,
        }
        if error_message:
            payload['error_message'] = error_message
        if s3_key:
            payload['audio_s3_key'] = s3_key

        try:
            requests.post(
                f'{self.django_url}/internal/bot/status/',
                json=payload,
                headers={
                    'Content-Type':      'application/json',
                    'X-Internal-Secret': self.internal_secret,
                },
                timeout=15,
            )
            logger.info(
                'Bot status posted: %s for meeting %s',
                status,
                self.meeting_id,
            )
        except requests.RequestException as exc:
            logger.error('Failed to POST bot status: %s', exc)
