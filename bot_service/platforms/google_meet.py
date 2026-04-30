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
            # Check for block screen
            if page.locator('text=You can\'t join this video call').count() > 0:
                logger.error("Google Meet blocked the bot: 'You can't join this video call'")
                page.screenshot(path="/tmp/blocked_join_screenshot.png")
                # Try to reload once
                logger.info("Attempting reload...")
                page.reload()
                page.wait_for_timeout(5000)

            # Dismiss 'Use without an account' or sign-in prompts
            page.wait_for_timeout(3000)

            # Click 'Continue without microphone' or similar
            for selector in [
                'text=Continue without microphone',
                'text=Dismiss',
                '[aria-label="Dismiss"]',
                'text=Got it',
                'span:has-text("Got it")',
                'button:has-text("Got it")',
                '[aria-label="Got it"]',
                'text=No thanks',
            ]:
                try:
                    if page.locator(selector).is_visible():
                        page.click(selector, timeout=3000, force=True)
                        page.wait_for_timeout(1000)
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

            # Set display name if prompted
            try:
                # Be more specific to the join page name input
                name_input = page.wait_for_selector(
                    'input[jsname="YPqjbf"]',
                    timeout=5000,
                )
                if name_input:
                    logger.info("Found name input, filling it.")
                    name_input.click()
                    name_input.fill('Audicle Bot')
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Could not find or fill name input: {e}")

            # Click 'Join now' or 'Ask to join' button
            join_button_clicked = False
            for selector in [
                'span:has-text("Ask to join")',
                'span:has-text("Join now")',
                'button:has-text("Ask to join")',
                'button:has-text("Join now")',
                'text=Ask to join',
                'text=Join now',
                '[aria-label="Ask to join"]',
                '[aria-label="Join now"]',
                '[jsname="Qx7uuf"]',
            ]:
                try:
                    # Actually wait for the element to appear
                    element = page.wait_for_selector(selector, timeout=3000, state="visible")
                    if element:
                        # Use force=True to bypass pointer interception if an overlay is present
                        element.click(timeout=3000, force=True)
                        logger.info(f'Clicked join button with selector: {selector}')
                        print(f'Clicked join button with selector: {selector}')
                        join_button_clicked = True
                        break
                except Exception:
                    pass
            
            if not join_button_clicked:
                logger.warning("Failed to find and click any join button.")
                print("WARNING: Failed to find and click any join button.")
                page.screenshot(path="/tmp/failed_join_screenshot.png")

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
        """Detect if Google Meet has ended. MUST be called from the same thread as the Page."""
        try:
            # Fast: check URL changed away from meet (instant property access)
            url = page.url
            if url and 'meet.google.com' not in url:
                logger.info('Meeting end: page left meet.google.com')
                return True

            # Fast: check for end-screen text via JS (no implicit wait)
            try:
                body_text = page.evaluate("document.body.innerText || ''")
                end_phrases = [
                    "you've left", "the meeting has ended", "your meeting has ended",
                    "everyone has left", "return to home screen", "you left the meeting",
                ]
                body_lower = body_text.lower()
                for phrase in end_phrases:
                    if phrase in body_lower:
                        logger.info(f'Meeting end via text: "{phrase}"')
                        return True
            except Exception:
                pass

            # Fast: check if Leave call button is gone (locator.count has no implicit wait)
            try:
                leave_count = page.locator('[aria-label="Leave call"]').count()
                join_screen = page.locator('input[jsname="YPqjbf"]').count()
                if leave_count == 0 and join_screen == 0:
                    logger.info('Meeting end: Leave call button gone')
                    return True
            except Exception:
                pass

            return False
        except Exception:
            return False

    def get_participant_count(self, page: Page) -> int:
        """Count participants using fast JS evaluation."""
        try:
            # Use JS to count participant tiles — fastest method, no DOM waiting
            count = page.evaluate("""
                () => {
                    // Try participant list items
                    const items = document.querySelectorAll('[data-participant-id]');
                    if (items.length > 0) return items.length;
                    // Fallback: count video tiles
                    const tiles = document.querySelectorAll('[jsname="kRQnlf"]');
                    if (tiles.length > 0) return tiles.length;
                    return -1;
                }
            """)
            return int(count) if count is not None else -1
        except Exception:
            return -1
