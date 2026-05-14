import logging
import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RECALL_API_KEY = os.environ.get('RECALL_API_KEY')
RECALL_BASE_URL = 'https://us-west-2.recall.ai/api/v1'

HEADERS = {
    'Authorization': f'Token {RECALL_API_KEY}',
    'Content-Type': 'application/json',
}


class RecallBot:

    def __init__(self, meeting_url, meeting_id, bot_name='Audicle Bot', on_recording_started=None):
        self.meeting_url = meeting_url
        self.meeting_id = meeting_id
        self.bot_name = bot_name
        self.on_recording_started = on_recording_started
        self.bot_id = None

    def start(self) -> str | None:
        """Send bot to meeting — audio only, no transcription."""
        try:
            response = requests.post(
                f'{RECALL_BASE_URL}/bot/',
                headers=HEADERS,
                json={
                    'meeting_url': self.meeting_url,
                    'bot_name': self.bot_name,
                    'recording_config': {
                        'video_mixed_mp4': {},
                        'audio_mixed_mp4': {},
                    },
                },
                timeout=30,
            )
            # Log the actual error response
            if not response.ok:
                logger.error(
                    'Recall API error %s: %s',
                    response.status_code,
                    response.text
                )
                response.raise_for_status()

            self.bot_id = response.json()['id']
            logger.info('Recall bot started: %s', self.bot_id)
            return self.bot_id
        except Exception as exc:
            logger.error('Failed to start Recall bot: %s', exc)
            return None

    def wait_for_completion(self, timeout=3600) -> bool:
        """Poll until meeting ends. Returns True on success."""
        if not self.bot_id:
            return False

        start = time.time()
        recording_notified = False

        while time.time() - start < timeout:
            try:
                response = requests.get(
                    f'{RECALL_BASE_URL}/bot/{self.bot_id}/',
                    headers=HEADERS,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()

                status_changes = data.get('status_changes', [])
                current_status = status_changes[-1]['code'] if status_changes else 'unknown'
                logger.info('Recall bot status: %s', current_status)

                # Notify when recording starts
                if current_status == 'in_call_recording' and not recording_notified:
                    recording_notified = True
                    if self.on_recording_started:
                        self.on_recording_started()

                # Success
                if current_status in ('done', 'call_ended'):
                    return True

                # Failed
                if current_status in ('fatal', 'failed', 'kicked', 'waiting_room_timeout'):
                    logger.error('Recall bot failed: %s', current_status)
                    return False

            except Exception as exc:
                logger.warning('Recall poll error: %s', exc)

            time.sleep(10)

        logger.warning('Recall bot timed out')
        return False

    def get_audio_url(self) -> str | None:
        """Get the audio download URL after meeting ends."""
        if not self.bot_id:
            return None
        try:
            response = requests.get(
                f'{RECALL_BASE_URL}/bot/{self.bot_id}/',
                headers=HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            recordings = data.get('recordings', [])
            for recording in recordings:
                shortcuts = recording.get('media_shortcuts', {})

                # Try audio_mixed first
                audio = shortcuts.get('audio_mixed')
                if audio and audio.get('data', {}).get('download_url'):
                    return audio['data']['download_url']

                # Fallback to video_mixed
                video = shortcuts.get('video_mixed')
                if video and video.get('data', {}).get('download_url'):
                    logger.info('No audio_mixed, using video_mixed URL')
                    return video['data']['download_url']

            return None
        except Exception as exc:
            logger.error('Failed to get audio URL: %s', exc)
            return None

    def download_audio(self, output_path: str) -> bool:
        """Download audio file to local path."""
        audio_url = self.get_audio_url()
        if not audio_url:
            logger.error('No audio URL available')
            return False
        try:
            response = requests.get(audio_url, stream=True, timeout=60)
            response.raise_for_status()
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info('Audio downloaded to %s', output_path)
            return True
        except Exception as exc:
            logger.error('Audio download failed: %s', exc)
            return False

    def stop(self) -> None:
        """Force stop the bot."""
        if not self.bot_id:
            return
        try:
            requests.delete(
                f'{RECALL_BASE_URL}/bot/{self.bot_id}/',
                headers=HEADERS,
                timeout=15,
            )
            logger.info('Recall bot stopped: %s', self.bot_id)
        except Exception as exc:
            logger.warning('Failed to stop bot: %s', exc)
