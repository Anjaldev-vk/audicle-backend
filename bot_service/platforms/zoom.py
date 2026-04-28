import logging

from playwright.sync_api import Page

from .base import BaseMeetingBot

logger = logging.getLogger(__name__)


class ZoomBot(BaseMeetingBot):
    """
    Bot for Zoom meetings.
    Joins via browser (no Zoom client required).
    """

    def join(self, page: Page) -> None:
        """Handle Zoom browser join flow."""
        try:
            page.wait_for_timeout(3000)

            # Click 'Join from your browser' link
            for selector in [
                'text=Join from Your Browser',
                'text=join from your browser',
                'a[class*="join-browser"]',
            ]:
                try:
                    page.click(selector, timeout=5000)
                    logger.info('Clicked join from browser on Zoom')
                    break
                except Exception:
                    pass

            page.wait_for_timeout(2000)

            # Enter name
            try:
                name_input = page.wait_for_selector(
                    'input#inputname',
                    timeout=5000,
                )
                if name_input:
                    name_input.fill('Audicle Bot')
            except Exception:
                pass

            # Click join button
            for selector in [
                'button#joinBtn',
                'text=Join',
                '[aria-label="Join"]',
            ]:
                try:
                    page.click(selector, timeout=5000)
                    logger.info('Clicked join button on Zoom')
                    break
                except Exception:
                    pass

            # Handle waiting room
            try:
                page.wait_for_selector(
                    'text=Please wait',
                    timeout=5000,
                )
                logger.info('Zoom waiting room — waiting for host to admit')
            except Exception:
                pass

        except Exception as exc:
            logger.warning('Zoom join step error: %s', exc)

    def is_in_meeting(self, page: Page) -> bool:
        """Detect if bot is inside the Zoom meeting."""
        try:
            page.wait_for_selector(
                '[aria-label="leave"]',
                timeout=3000,
            )
            return True
        except Exception:
            return False

    def is_meeting_ended(self, page: Page) -> bool:
        """Detect if Zoom meeting has ended."""
        try:
            for selector in [
                'text=This meeting has been ended',
                'text=The host has ended the meeting',
                'text=Meeting ended',
            ]:
                if page.locator(selector).count() > 0:
                    return True
            return False
        except Exception:
            return False
