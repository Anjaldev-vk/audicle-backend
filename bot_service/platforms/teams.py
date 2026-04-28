import logging

from playwright.sync_api import Page

from .base import BaseMeetingBot

logger = logging.getLogger(__name__)


class TeamsBot(BaseMeetingBot):
    """
    Bot for Microsoft Teams meetings.
    Joins via browser without Teams client.
    """

    def join(self, page: Page) -> None:
        """Handle Teams browser join flow."""
        try:
            page.wait_for_timeout(3000)

            # Click 'Continue on this browser'
            for selector in [
                'text=Continue on this browser',
                'text=Join on the web instead',
                '[data-tid="joinOnWeb"]',
            ]:
                try:
                    page.click(selector, timeout=5000)
                    logger.info('Clicked continue on browser for Teams')
                    break
                except Exception:
                    pass

            page.wait_for_timeout(2000)

            # Enter name
            try:
                name_input = page.wait_for_selector(
                    'input[placeholder*="name"]',
                    timeout=5000,
                )
                if name_input:
                    name_input.fill('Audicle Bot')
            except Exception:
                pass

            # Turn off camera
            try:
                page.click('[aria-label*="camera"]', timeout=3000)
            except Exception:
                pass

            # Mute mic
            try:
                page.click('[aria-label*="microphone"]', timeout=3000)
            except Exception:
                pass

            # Click join now
            for selector in [
                'text=Join now',
                '[data-tid="prejoin-join-button"]',
                'button[aria-label*="Join now"]',
            ]:
                try:
                    page.click(selector, timeout=5000)
                    logger.info('Clicked join now on Teams')
                    break
                except Exception:
                    pass

        except Exception as exc:
            logger.warning('Teams join step error: %s', exc)

    def is_in_meeting(self, page: Page) -> bool:
        """Detect if bot is inside the Teams meeting."""
        try:
            page.wait_for_selector(
                '[aria-label*="Leave"]',
                timeout=3000,
            )
            return True
        except Exception:
            return False

    def is_meeting_ended(self, page: Page) -> bool:
        """Detect if Teams meeting has ended."""
        try:
            for selector in [
                'text=The meeting has ended',
                'text=You left the meeting',
                '[data-tid="meeting-ended"]',
            ]:
                if page.locator(selector).count() > 0:
                    return True
            return False
        except Exception:
            return False
