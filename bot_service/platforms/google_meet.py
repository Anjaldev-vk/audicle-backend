import logging
import time

from playwright.sync_api import Page

from .base import BaseMeetingBot

logger = logging.getLogger(__name__)


class GoogleMeetBot(BaseMeetingBot):
    """
    Bot for Google Meet meetings.
    Joins via URL, dismisses popups, detects meeting end.
    """

    def join(self, page: Page) -> None:
        """Handle Google Meet join flow."""
        try:
            # Dismiss 'Use without an account' or sign-in prompts
            page.wait_for_timeout(3000)

            # Click 'Continue without microphone' or similar
            for selector in [
                'text=Continue without microphone',
                'text=Dismiss',
                '[aria-label="Dismiss"]',
            ]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except Exception:
                    pass

            # Turn off camera if button exists
            for selector in [
                '[aria-label="Turn off camera"]',
                '[data-is-muted="false"][aria-label*="camera"]',
            ]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except Exception:
                    pass

            # Mute microphone
            for selector in [
                '[aria-label="Turn off microphone"]',
                '[data-is-muted="false"][aria-label*="microphone"]',
            ]:
                try:
                    page.click(selector, timeout=3000)
                    break
                except Exception:
                    pass

            # Click 'Join now' button
            for selector in [
                'text=Join now',
                'text=Ask to join',
                '[aria-label="Join now"]',
                '[jsname="Qx7uuf"]',
            ]:
                try:
                    page.click(selector, timeout=5000)
                    logger.info('Clicked join button on Google Meet')
                    break
                except Exception:
                    pass

            # Set display name if prompted
            try:
                name_input = page.wait_for_selector(
                    'input[placeholder*="name"]',
                    timeout=3000,
                )
                if name_input:
                    name_input.fill('Audicle Bot')
            except Exception:
                pass

        except Exception as exc:
            logger.warning('Google Meet join step error: %s', exc)

    def is_in_meeting(self, page: Page) -> bool:
        """Detect if bot is inside the Google Meet."""
        try:
            # Meeting controls visible = inside meeting
            page.wait_for_selector(
                '[aria-label="Leave call"]',
                timeout=3000,
            )
            return True
        except Exception:
            return False

    def is_meeting_ended(self, page: Page) -> bool:
        """Detect if Google Meet has ended."""
        try:
            # Meeting ended screen
            for selector in [
                'text=You\'ve left the meeting',
                'text=The meeting has ended',
                'text=Return to home screen',
            ]:
                if page.locator(selector).count() > 0:
                    return True
            return False
        except Exception:
            return False
