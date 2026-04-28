import logging
import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod

from playwright.sync_api import sync_playwright, Browser, Page

logger = logging.getLogger(__name__)


class BaseMeetingBot(ABC):
    """
    Abstract base class for all platform meeting bots.

    Subclasses implement:
        join(page)     — platform-specific join logic
        is_in_meeting(page) — detect if bot is inside the meeting
        is_meeting_ended(page) — detect if meeting has ended
    """

    JOIN_TIMEOUT    = 60   # seconds to wait for join
    POLL_INTERVAL   = 10   # seconds between meeting-end checks
    SILENCE_TIMEOUT = 300  # seconds of silence before assuming meeting ended

    def __init__(
        self,
        meeting_url: str,
        audio_output_path: str,
        duration_cap: int = 3600,
    ):
        self.meeting_url       = meeting_url
        self.audio_output_path = audio_output_path
        self.duration_cap      = duration_cap
        self._ffmpeg_process   = None

    def run(self) -> None:
        """Full bot lifecycle — launch browser, join, record, leave."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--use-fake-ui-for-media-stream',
                    '--use-fake-device-for-media-stream',
                    '--allow-file-access-from-files',
                    '--autoplay-policy=no-user-gesture-required',
                ],
            )

            context = browser.new_context(
                permissions=['microphone', 'camera'],
                ignore_https_errors=True,
            )

            page = context.new_page()

            try:
                logger.info('Navigating to %s', self.meeting_url)
                page.goto(self.meeting_url, timeout=30000)

                # Platform-specific join logic
                self.join(page)

                # Wait until inside meeting
                self._wait_for_join(page)

                # Start recording audio
                self._start_recording()

                # Wait until meeting ends or cap reached
                self._wait_for_meeting_end(page)

            finally:
                self._stop_recording()
                page.close()
                context.close()
                browser.close()

    @abstractmethod
    def join(self, page: Page) -> None:
        """Platform-specific join steps."""
        pass

    @abstractmethod
    def is_in_meeting(self, page: Page) -> bool:
        """Return True when bot has successfully joined the meeting."""
        pass

    @abstractmethod
    def is_meeting_ended(self, page: Page) -> bool:
        """Return True when the meeting has ended."""
        pass

    def _wait_for_join(self, page: Page) -> None:
        """Poll until is_in_meeting returns True or timeout."""
        start = time.time()
        while time.time() - start < self.JOIN_TIMEOUT:
            if self.is_in_meeting(page):
                logger.info('Bot successfully joined meeting')
                return
            time.sleep(3)
        logger.warning('Join timeout — proceeding anyway')

    def _wait_for_meeting_end(self, page: Page) -> None:
        """Poll until meeting ends or duration cap reached."""
        start = time.time()
        while time.time() - start < self.duration_cap:
            if self.is_meeting_ended(page):
                logger.info('Meeting ended — stopping bot')
                return
            time.sleep(self.POLL_INTERVAL)
        logger.info('Duration cap reached — stopping bot')

    def _start_recording(self) -> None:
        """
        Start ffmpeg to capture audio from virtual display.
        Uses pulse audio virtual sink in headless mode.
        Falls back to silence if no audio device available.
        """
        os.makedirs(os.path.dirname(self.audio_output_path), exist_ok=True)

        cmd = [
            'ffmpeg',
            '-y',                          # overwrite output
            '-f', 'pulse',                 # PulseAudio input
            '-i', 'default',               # default audio device
            '-acodec', 'libmp3lame',       # MP3 encoding
            '-ab', '128k',                 # 128kbps bitrate
            '-ar', '44100',                # sample rate
            self.audio_output_path,
        ]

        try:
            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info('ffmpeg recording started → %s', self.audio_output_path)
        except Exception as exc:
            logger.error('ffmpeg failed to start: %s', exc)
            self._ffmpeg_process = None

    def _stop_recording(self) -> None:
        """Stop ffmpeg gracefully."""
        if self._ffmpeg_process:
            self._ffmpeg_process.terminate()
            try:
                self._ffmpeg_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._ffmpeg_process.kill()
            logger.info('ffmpeg recording stopped')
            self._ffmpeg_process = None
