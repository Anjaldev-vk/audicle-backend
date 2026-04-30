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

    JOIN_TIMEOUT    = 120  # seconds to wait for join (allow time for host to admit)
    POLL_INTERVAL   = 5    # seconds between meeting-end checks (faster = quicker stop)
    SILENCE_TIMEOUT = 300  # seconds of silence before assuming meeting ended

    def __init__(
        self,
        meeting_url: str,
        audio_output_path: str,
        duration_cap: int = 3600,
        on_recording_started=None,
    ):
        self.meeting_url       = meeting_url
        self.audio_output_path = audio_output_path
        self.duration_cap      = duration_cap
        self.on_recording_started = on_recording_started
        self._ffmpeg_process   = None

    def run(self) -> None:
        """Full bot lifecycle — launch browser, join, record, leave."""
        # Set virtual display so Chromium audio pipeline works
        os.environ.setdefault('DISPLAY', ':99')
        os.environ.setdefault('PULSE_SINK', 'audicle_sink')

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,  # Must be False for audio capture via PulseAudio
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--use-fake-ui-for-media-stream',   # no camera/mic permission prompts
                    '--autoplay-policy=no-user-gesture-required',
                    '--disable-blink-features=AutomationControlled',
                ],
            )

            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 720},
                permissions=['microphone', 'camera'],
                ignore_https_errors=True,
            )

            page = context.new_page()
            # Stealth: remove webdriver property
            page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

            try:
                logger.info('Navigating to %s', self.meeting_url)
                page.goto(self.meeting_url, wait_until='networkidle')
                page.wait_for_timeout(3000)

                # Check if we were redirected to the landing page
                if page.url.strip('/') == 'https://meet.google.com':
                    logger.warning('Redirected to home page, re-navigating to meeting URL...')
                    page.goto(self.meeting_url, wait_until='networkidle')
                    page.wait_for_timeout(5000)

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
        page.screenshot(path="/tmp/join_timeout.png")
        logger.warning('Join timeout — failing bot')
        raise RuntimeError("Join timeout: Bot could not enter the meeting.")

    def _wait_for_meeting_end(self, page: Page) -> None:
        """Poll until meeting ends or duration cap reached."""
        start = time.time()
        alone_since = None

        while time.time() - start < self.duration_cap:
            # Direct call — Playwright MUST be called from same thread it was created in
            try:
                if self.is_meeting_ended(page):
                    logger.info('Meeting ended — stopping bot')
                    return
            except Exception as exc:
                logger.warning('is_meeting_ended error: %s', exc)

            # Empty-room safety net: stop if bot is alone for 90s
            try:
                if hasattr(self, 'get_participant_count'):
                    count = self.get_participant_count(page)
                    if 0 <= count <= 1:
                        if alone_since is None:
                            alone_since = time.time()
                            logger.warning('Bot alone (%d participant) — 90s timer started', count)
                        elif time.time() - alone_since > 90:
                            logger.info('Bot alone for 90s — ending recording')
                            return
                    else:
                        if alone_since is not None:
                            logger.info('Participants rejoined — resetting timer')
                        alone_since = None
            except Exception:
                pass

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
            '-y',                              # overwrite output
            '-f', 'pulse',                     # PulseAudio input
            '-i', 'audicle_sink.monitor',      # capture from virtual sink monitor
            '-acodec', 'libmp3lame',           # MP3 encoding
            '-ab', '128k',                     # 128kbps bitrate
            '-ar', '44100',                    # sample rate
            self.audio_output_path,
        ]

        try:
            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info('ffmpeg recording started → %s', self.audio_output_path)
            if self.on_recording_started:
                self.on_recording_started()
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
